"""Risk engine (spec §19): evidence + policy → PASS | REVIEW | FAIL with reason codes.

Pure and deterministic: the same evidence and policy version always give the same
decision, and the trace records every rule that fired. FAIL beats REVIEW beats PASS.
PASS requires every required check to be PASS (or an allowed NOT_APPLICABLE) and no
rule to have fired.
"""

from dataclasses import dataclass, field

from kyc.domain.enums import VerificationLevel as L
from kyc.domain.state_machine import VerificationEvidence
from kyc.risk.policy import FAIL_FLOOR, ResolvedPolicy

FINE = ("PASS", "NOT_APPLICABLE")


@dataclass(frozen=True)
class RiskInputs:
    level: L
    checks: dict[str, str]
    signals: tuple[tuple[str, str, str], ...] = ()     # (signal, category, severity)
    face_match_calibrated: bool = False
    # Evidence that the document itself is genuine, not merely self-consistent: a verified chip,
    # a valid signed barcode, or a supported forensics detector. Reading is not authenticating.
    authenticity_sources: tuple[str, ...] = ()


@dataclass
class RiskOutcome:
    decision: str
    reason_codes: list[str]
    policy_version: str
    trace: list[dict] = field(default_factory=list)
    evidence: VerificationEvidence = field(default_factory=VerificationEvidence)


def evaluate(inputs: RiskInputs, policy: ResolvedPolicy) -> RiskOutcome:
    trace: list[dict] = []

    def hit(outcome: str, reason: str, rule: str):
        if outcome != "IGNORE":
            trace.append({"outcome": outcome, "reason": reason, "rule": rule})

    # 1. Required evidence for this level must exist and be usable.
    for name in policy.required:
        value = inputs.checks.get(name)
        if value is None:
            hit("REVIEW", f"EVIDENCE_MISSING_{name.upper()}", f"required:{name}")
        elif value == "UNAVAILABLE":
            hit("REVIEW", f"{name.upper()}_UNAVAILABLE", f"required:{name}")
        elif value == "NOT_APPLICABLE" and name not in policy.not_applicable_allowed:
            hit("REVIEW", f"{name.upper()}_NOT_APPLICABLE", f"required:{name}")

    # 2. Every reported check, required or not, against the check rules.
    for name, value in sorted(inputs.checks.items()):
        if name == "nfc_status":
            continue  # detail of `nfc`, not a separate check
        if value == "UNAVAILABLE":
            continue  # required ones were reported in step 1; others are engines this level does not use
        key = f"{name}:{value}"
        if key in policy.check_rules:
            outcome, reason = policy.check_rules[key]
            if key in FAIL_FLOOR:
                outcome = "FAIL"
            hit(outcome, reason, f"check:{key}")
        elif value not in FINE:
            hit("REVIEW", f"CHECK_{name.upper()}_{value}", f"check:{key}")
    if not inputs.authenticity_sources:
        hit("REVIEW", "DOCUMENT_AUTHENTICITY_UNVERIFIED", "authenticity:none")
    if inputs.checks.get("face_match") == "REVIEW" and not inputs.face_match_calibrated:
        hit("REVIEW", "FACE_MATCH_UNCALIBRATED", "calibration:face_match")

    # 3. Fraud signals.
    for code, category, severity in inputs.signals:
        hit(policy.signal_outcome(code, category, severity), f"FRAUD_{code}", f"signal:{code}:{category}:{severity}")

    fails = [item["reason"] for item in trace if item["outcome"] == "FAIL"]
    reviews = [item["reason"] for item in trace if item["outcome"] == "REVIEW"]
    checks = inputs.checks
    if fails:
        decision, reasons = "FAIL", fails
    elif reviews:
        decision, reasons = "REVIEW", reviews
    else:
        decision = "PASS"
        reasons = ["DOCUMENT_VALID"]
        reasons += ["FACE_MATCH"] if inputs.level != L.DOCUMENT_ONLY else []
        reasons += ["LIVENESS_PASS"] if inputs.level in (L.DOCUMENT_FACE_LIVENESS, L.DOCUMENT_FACE_LIVENESS_NFC) else []
        reasons += ["NFC_VERIFIED"] if inputs.level == L.DOCUMENT_FACE_LIVENESS_NFC else []
        reasons += ["NO_FRAUD_SIGNALS" if not inputs.signals else "ONLY_LOW_FRAUD_SIGNALS"]
    evidence = VerificationEvidence(
        document_valid=all(checks.get(name) in FINE for name in ("document_quality", "document_classification",
                                                                 "document_data", "expiry")),
        face_match=checks.get("face_match") == "PASS", liveness_pass=checks.get("liveness") == "PASS",
        nfc_verified=checks.get("nfc") == "PASS", policy_pass=decision == "PASS")
    return RiskOutcome(decision, list(dict.fromkeys(reasons)), policy.version, trace, evidence)
