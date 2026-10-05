"""Deterministic risk policy (spec §19): versioned data, never a model and never an LLM.

The built-in policy is the floor. An operator file (RISK_POLICY_FILE) may add
per-country/per-document overrides, and every override can only TIGHTEN a rule:
IGNORE → REVIEW → FAIL, more required checks, fewer checks allowed to be
NOT_APPLICABLE. Nothing a configuration says can turn a FAIL or REVIEW into a PASS.
"""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kyc.domain.enums import DocumentType, VerificationLevel as L

DEFAULT_VERSION = "RISK-DEFAULT-2026.10.1"
RANK = {"IGNORE": 0, "REVIEW": 1, "FAIL": 2}

BASE_REQUIRED = ("document_quality", "document_classification", "document_data", "expiry", "cross_check", "fraud")
LEVEL_REQUIRED = {
    L.DOCUMENT_ONLY: BASE_REQUIRED,
    L.DOCUMENT_FACE: BASE_REQUIRED + ("portrait_quality", "face_quality", "face_match"),
    L.DOCUMENT_FACE_LIVENESS: BASE_REQUIRED + ("portrait_quality", "face_quality", "face_match", "liveness"),
    L.DOCUMENT_FACE_LIVENESS_NFC: BASE_REQUIRED + ("portrait_quality", "face_quality", "face_match", "liveness", "nfc"),
}
# A required check may legitimately not apply (an NSSF card prints no expiry; a card with
# no MRZ, barcode or chip has nothing to cross-check). Overrides can only shrink this set.
NOT_APPLICABLE_ALLOWED = ("expiry", "cross_check")

# (check, value) → (outcome, reason). Values not listed: PASS/NOT_APPLICABLE are fine,
# anything else falls back to REVIEW with CHECK_<NAME>_<VALUE>.
CHECK_RULES: dict[str, tuple[str, str]] = {
    "liveness:FAIL": ("FAIL", "LIVENESS_FAILED"),
    "face_match:FAIL": ("FAIL", "FACE_MISMATCH"),
    "expiry:FAIL": ("FAIL", "EXPIRED_DOCUMENT"),
    "fraud:FAIL": ("FAIL", "HIGH_RISK_TAMPER_SIGNAL"),
    "barcode:FAIL": ("FAIL", "HIGH_RISK_TAMPER_SIGNAL"),
    "liveness:REVIEW": ("REVIEW", "LIVENESS_INCONCLUSIVE"),
    "face_match:REVIEW": ("REVIEW", "FACE_SCORE_BORDERLINE"),
    "nfc:FAIL": ("REVIEW", "NFC_NOT_VERIFIED"),
    "nfc:REVIEW": ("REVIEW", "NFC_NOT_VERIFIED"),
    "expiry:REVIEW": ("REVIEW", "EXPIRY_UNKNOWN"),
    "document_classification:REVIEW": ("REVIEW", "LOW_CLASSIFICATION_CONFIDENCE"),
    "document_data:REVIEW": ("REVIEW", "LOW_OCR_CONFIDENCE"),
    "document_data:FAIL": ("REVIEW", "DOCUMENT_DATA_INVALID"),
    "mrz:REVIEW": ("REVIEW", "MRZ_INVALID"),
    "mrz:FAIL": ("REVIEW", "MRZ_INVALID"),
    "mrz_consistency:REVIEW": ("REVIEW", "FIELD_MISMATCH"),
    "cross_check:REVIEW": ("REVIEW", "FIELD_MISMATCH"),
    "barcode:REVIEW": ("REVIEW", "BARCODE_INCONSISTENT"),
    "issuing_country:REVIEW": ("REVIEW", "ISSUING_COUNTRY_MISMATCH"),
    "face_quality:REVIEW": ("REVIEW", "FACE_QUALITY_UNVERIFIED"),
    "portrait_quality:REVIEW": ("REVIEW", "PORTRAIT_QUALITY_UNVERIFIED"),
    "fraud:REVIEW": ("REVIEW", "FRAUD_SIGNALS_PRESENT"),
    "chip_document_consistency:FAIL": ("REVIEW", "FIELD_MISMATCH"),
    "chip_active_authentication:FAIL": ("FAIL", "HIGH_RISK_TAMPER_SIGNAL"),
    "chip_face_match:FAIL": ("FAIL", "FACE_MISMATCH"),
    "chip_face_match:REVIEW": ("REVIEW", "FACE_SCORE_BORDERLINE"),
    "document_portrait:REVIEW": ("REVIEW", "PORTRAIT_UNVERIFIED"),
    "document_portrait:FAIL": ("REVIEW", "PORTRAIT_UNVERIFIED"),
}
# Signals by exact code, or by "CATEGORY:SEVERITY" / "*:SEVERITY". The most specific match wins
# (exact code, then category, then wildcard); FAIL/REVIEW add the reason FRAUD_<SIGNAL>.
SIGNAL_RULES: dict[str, str] = {
    "TAMPER:HIGH": "FAIL",
    "*:HIGH": "REVIEW",
    "*:MEDIUM": "REVIEW",
    "*:LOW": "IGNORE",
}
# Floors the code enforces even if a rule above were edited: these can never resolve to less than FAIL.
FAIL_FLOOR = ("liveness:FAIL", "fraud:FAIL", "TAMPER:HIGH")


class Override(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    document_type: DocumentType | None = None
    require: tuple[str, ...] = ()
    disallow_not_applicable: tuple[str, ...] = ()
    check_rules: dict[str, str] = Field(default_factory=dict)    # "check:VALUE" → REVIEW | FAIL
    signal_rules: dict[str, str] = Field(default_factory=dict)   # code | CATEGORY:SEV → REVIEW | FAIL


class PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=3, max_length=100)
    overrides: tuple[Override, ...] = ()

    @model_validator(mode="after")
    def only_tighten(self):
        for item in self.overrides:
            for key, outcome in item.check_rules.items():
                if outcome not in ("REVIEW", "FAIL") or ":" not in key:
                    raise ValueError(f"check rule {key!r} must be 'check:VALUE' → REVIEW or FAIL")
                default = CHECK_RULES.get(key, ("REVIEW", ""))[0]
                if RANK[outcome] < RANK[default]:
                    raise ValueError(f"override {key!r} would loosen {default} to {outcome}")
            for key, outcome in item.signal_rules.items():
                if outcome not in ("REVIEW", "FAIL"):
                    raise ValueError(f"signal rule {key!r} must be REVIEW or FAIL")
        return self


class RiskPolicy:
    def __init__(self, file: PolicyFile | None = None, digest: str | None = None):
        self.file = file
        self.version = file.version if file else DEFAULT_VERSION
        self.digest = digest

    @classmethod
    def load(cls, path: Path | None) -> "RiskPolicy":
        if path is None:
            return cls()
        raw = Path(path).read_bytes()
        return cls(PolicyFile.model_validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()[:16])

    def resolve(self, level: L, country: str | None, document_type: str | None) -> "ResolvedPolicy":
        required = list(LEVEL_REQUIRED[level])
        allowed = set(NOT_APPLICABLE_ALLOWED)
        checks, signals = dict(CHECK_RULES), dict(SIGNAL_RULES)
        applied = []
        for index, item in enumerate(self.file.overrides if self.file else ()):
            if item.country not in (None, country) or (item.document_type and item.document_type.value != document_type):
                continue
            applied.append(index)
            required += [name for name in item.require if name not in required]
            allowed -= set(item.disallow_not_applicable)
            for key, outcome in item.check_rules.items():
                current = checks.get(key, ("REVIEW", f"CHECK_{key.replace(':', '_').upper()}"))
                if RANK[outcome] >= RANK[current[0]]:
                    checks[key] = (outcome, current[1])
            for key, outcome in item.signal_rules.items():
                if RANK[outcome] >= RANK.get(signals.get(key, "IGNORE"), 0):
                    signals[key] = outcome
        return ResolvedPolicy(self.version, self.digest, tuple(required), frozenset(allowed), checks, signals, tuple(applied))


class ResolvedPolicy:
    def __init__(self, version, digest, required, not_applicable_allowed, check_rules, signal_rules, overrides):
        self.version = version
        self.digest = digest
        self.required = required
        self.not_applicable_allowed = not_applicable_allowed
        self.check_rules = check_rules
        self.signal_rules = signal_rules
        self.overrides = overrides

    def signal_outcome(self, code: str, category: str, severity: str) -> str:
        outcome = self.signal_rules.get(code) or self.signal_rules.get(f"{category}:{severity}") \
            or self.signal_rules.get(f"*:{severity}", "REVIEW")
        if f"{category}:{severity}" in FAIL_FLOOR:
            return "FAIL"
        return outcome
