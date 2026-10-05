"""Pluggable fraud-analysis pipeline (spec §17).

The service gathers facts from stored evidence into a `FraudContext`; each detector is
a pure function of that context. No detector reads the database, decrypts, or sees
identity values, so detectors are simple to test and to add.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from kyc.fraud import crosscheck
from kyc.fraud.crosscheck import Comparison
from kyc.fraud.signals import COVERAGE, ENGINE_VERSION, Category, Severity, Signal


@dataclass(frozen=True)
class BarcodeFacts:
    symbology: str
    signature_present: bool
    signature_valid: bool | None
    signature_reason: str | None


@dataclass(frozen=True)
class NFCFacts:
    status: str
    reason_codes: tuple[str, ...]
    active_authentication: bool | None


@dataclass(frozen=True)
class DuplicateFacts:
    window_hours: int
    velocity_limit: int
    other_users: int = 0              # other user_ids that presented the same document number
    same_user_sessions: int = 0       # earlier sessions of this user with the same document
    recent_sessions: int = 0          # sessions with this document inside the window, this one included
    reused_capture_sides: tuple[str, ...] = ()
    reused_selfie: bool = False


@dataclass(frozen=True)
class CaptureMetadata:
    side: str
    media_type: str
    editor: str | None = None         # recognized editing application named in the file
    screenshot: bool = False          # the file declares itself a screenshot
    captured_on: date | None = None   # EXIF DateTimeOriginal (unverified; any app can write it)


@dataclass(frozen=True)
class PortraitFacts:
    score: float
    fail_threshold: float
    calibrated: bool
    policy_version: str


@dataclass
class FraudContext:
    verification_level: str
    session_date: date
    comparisons: list[Comparison] = field(default_factory=list)
    checks: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)   # check_type → (result, reasons)
    barcodes: list[BarcodeFacts] = field(default_factory=list)
    nfc: NFCFacts | None = None
    duplicates: DuplicateFacts | None = None
    metadata: list[CaptureMetadata] = field(default_factory=list)
    portrait: PortraitFacts | None = None


@dataclass
class FraudReport:
    signals: list[Signal]
    cross_check: dict
    cross_check_result: str
    result: str
    coverage: dict
    detectors: list[str]
    engine_version: str = ENGINE_VERSION


Detector = Callable[[FraudContext], list[Signal]]


def detect_consistency(context: FraudContext) -> list[Signal]:
    return crosscheck.signals(crosscheck.build(context.comparisons))


# Adapter and MRZ reason codes that are fraud-relevant, and the signal each becomes.
VALIDITY_CODES = {
    "EXPIRED_DOCUMENT": ("EXPIRED_DOCUMENT", Severity.MEDIUM),
    "ISSUED_BEFORE_BIRTH": ("IMPOSSIBLE_DATES_ISSUED_BEFORE_BIRTH", Severity.MEDIUM),
    "ISSUE_DATE_IN_FUTURE": ("IMPOSSIBLE_DATES_ISSUED_IN_FUTURE", Severity.MEDIUM),
    "DATE_OF_BIRTH_IMPOSSIBLE": ("IMPOSSIBLE_DATES_BIRTH", Severity.MEDIUM),
    "EXPIRY_NOT_AFTER_ISSUE": ("IMPOSSIBLE_DATES_EXPIRY_BEFORE_ISSUE", Severity.MEDIUM),
    "UNUSUAL_VALIDITY_PERIOD": ("UNUSUAL_VALIDITY_PERIOD", Severity.LOW),
    "MRZ_CHECK_DIGIT_FAILED": ("MRZ_CHECK_DIGIT_FAILED", Severity.MEDIUM),
    "MRZ_DATE_OF_BIRTH_INVALID": ("MRZ_IMPOSSIBLE_DATE", Severity.MEDIUM),
    "MRZ_EXPIRY_INVALID": ("MRZ_IMPOSSIBLE_DATE", Severity.MEDIUM),
}


def detect_validity(context: FraudContext) -> list[Signal]:
    found: dict[str, Signal] = {}
    for check_type, (result, reasons) in context.checks.items():
        if result not in ("REVIEW", "FAIL"):
            continue
        for reason in reasons:
            if reason in VALIDITY_CODES:
                code, severity = VALIDITY_CODES[reason]
                found.setdefault(code, Signal(code, severity, Category.VALIDITY, "validity",
                                              details={"check_type": check_type, "reason_code": reason}))
    return list(found.values())


NFC_CODES = {
    "DATA_GROUP_HASH_MISMATCH": ("NFC_DATA_GROUP_ALTERED", Severity.HIGH, Category.TAMPER),
    "SOD_SIGNATURE_INVALID": ("NFC_SECURITY_OBJECT_INVALID", Severity.HIGH, Category.TAMPER),
    "SOD_MESSAGE_DIGEST_MISMATCH": ("NFC_SECURITY_OBJECT_INVALID", Severity.HIGH, Category.TAMPER),
    "ACTIVE_AUTHENTICATION_FAILED": ("NFC_CHIP_CLONE_SUSPECTED", Severity.HIGH, Category.TAMPER),
    "DOCUMENT_SIGNER_OUTSIDE_VALIDITY": ("NFC_SIGNER_OUTSIDE_VALIDITY", Severity.MEDIUM, Category.VALIDITY),
    "SOD_UNPARSEABLE": ("NFC_CHIP_UNREADABLE", Severity.MEDIUM, Category.VALIDITY),
    "CHIP_DATA_INCOMPLETE": ("NFC_CHIP_UNREADABLE", Severity.MEDIUM, Category.VALIDITY),
    "DG1_UNREADABLE": ("NFC_CHIP_UNREADABLE", Severity.MEDIUM, Category.VALIDITY),
    "DOCUMENT_SIGNER_NOT_TRUSTED": ("NFC_ISSUER_NOT_TRUSTED", Severity.LOW, Category.CONTEXT),
    "CSCA_TRUST_STORE_NOT_CONFIGURED": ("NFC_ISSUER_NOT_TRUSTED", Severity.LOW, Category.CONTEXT),
    "ACTIVE_AUTHENTICATION_WITHOUT_SERVER_CHALLENGE": ("NFC_REPLAYABLE_AUTHENTICATION", Severity.LOW, Category.CONTEXT),
}


def detect_tamper(context: FraudContext) -> list[Signal]:
    found: dict[str, Signal] = {}
    for item in context.barcodes:
        if item.signature_present and item.signature_valid is False:
            found.setdefault("BARCODE_SIGNATURE_INVALID", Signal(
                "BARCODE_SIGNATURE_INVALID", Severity.HIGH, Category.TAMPER, "tamper", sources=("BARCODE",),
                details={"symbology": item.symbology, "reason": item.signature_reason}))
    if context.nfc is not None:
        for reason in context.nfc.reason_codes:
            if reason in NFC_CODES:
                code, severity, category = NFC_CODES[reason]
                found.setdefault(code, Signal(code, severity, category, "tamper", sources=("NFC",),
                                              details={"reason_code": reason, "nfc_status": context.nfc.status}))
        if context.nfc.status in ("NFC_NOT_SUPPORTED", "NFC_NOT_AVAILABLE"):
            found.setdefault("NFC_NOT_COMPLETED", Signal("NFC_NOT_COMPLETED", Severity.LOW, Category.CONTEXT, "tamper",
                                                         sources=("NFC",), details={"nfc_status": context.nfc.status}))
    return list(found.values())


def detect_duplicates(context: FraudContext) -> list[Signal]:
    facts = context.duplicates
    if facts is None:
        return []
    found = []
    if facts.other_users:
        found.append(Signal("DOCUMENT_USED_BY_ANOTHER_USER", Severity.HIGH, Category.DUPLICATE, "duplicates",
                            ("document_number",), details={"other_users": facts.other_users}))
    if facts.same_user_sessions:
        found.append(Signal("DOCUMENT_PREVIOUSLY_USED", Severity.LOW, Category.DUPLICATE, "duplicates",
                            ("document_number",), details={"earlier_sessions": facts.same_user_sessions}))
    if facts.recent_sessions >= facts.velocity_limit:
        found.append(Signal("DOCUMENT_VELOCITY", Severity.MEDIUM, Category.DUPLICATE, "duplicates", ("document_number",),
                            details={"sessions": facts.recent_sessions, "window_hours": facts.window_hours}))
    if facts.reused_capture_sides:
        # A fresh photograph never has the same bytes twice; an identical file is a replayed upload.
        found.append(Signal("DOCUMENT_CAPTURE_REUSED", Severity.HIGH, Category.DUPLICATE, "duplicates",
                            details={"sides": list(facts.reused_capture_sides)}))
    if facts.reused_selfie:
        found.append(Signal("SELFIE_CAPTURE_REUSED", Severity.HIGH, Category.DUPLICATE, "duplicates"))
    return found


def detect_metadata(context: FraudContext) -> list[Signal]:
    """One-directional: markers raise signals, missing metadata proves nothing (the web client strips it)."""
    found: dict[str, Signal] = {}
    for item in context.metadata:
        if item.editor:
            found.setdefault("EDITING_SOFTWARE_IN_METADATA", Signal(
                "EDITING_SOFTWARE_IN_METADATA", Severity.MEDIUM, Category.METADATA, "metadata",
                details={"side": item.side, "editor": item.editor}))
        if item.screenshot:
            found.setdefault("SCREENSHOT_METADATA", Signal("SCREENSHOT_METADATA", Severity.MEDIUM, Category.METADATA,
                                                           "metadata", details={"side": item.side}))
        if item.captured_on and (context.session_date - item.captured_on).days > 7:
            found.setdefault("OLD_CAPTURE_TIMESTAMP", Signal(
                "OLD_CAPTURE_TIMESTAMP", Severity.LOW, Category.METADATA, "metadata",
                details={"side": item.side, "age_days": (context.session_date - item.captured_on).days}))
    return list(found.values())


def detect_portrait(context: FraudContext) -> list[Signal]:
    facts = context.portrait
    if facts is None or facts.score >= facts.fail_threshold:
        return []
    # Uncalibrated thresholds cannot carry a HIGH claim; the score is kept for the reviewer.
    return [Signal("PORTRAIT_DIFFERS_FROM_CHIP", Severity.HIGH if facts.calibrated else Severity.MEDIUM,
                   Category.PORTRAIT, "portrait", sources=("VISUAL", "NFC"),
                   details={"score": round(facts.score, 4), "policy_version": facts.policy_version,
                            "calibrated": facts.calibrated})]


def detect_context(context: FraudContext) -> list[Signal]:
    result, reasons = context.checks.get("ISSUING_COUNTRY", ("", ()))
    if "ISSUING_STATE_DIFFERS_FROM_SESSION_COUNTRY" in reasons:
        return [Signal("ISSUING_STATE_DIFFERS_FROM_SESSION_COUNTRY", Severity.LOW, Category.CONTEXT, "context")]
    return []


DETECTORS: dict[str, Detector] = {
    "cross-check": detect_consistency, "validity": detect_validity, "tamper": detect_tamper,
    "duplicates": detect_duplicates, "metadata": detect_metadata, "portrait": detect_portrait, "context": detect_context,
}


def result_for(signals: list[Signal]) -> str:
    """Advisory summary for `checks.fraud`. Cryptographic tamper proof is FAIL; anything else
    of MEDIUM or HIGH severity needs review. The session decision is still the risk engine's."""
    if any(item.category == Category.TAMPER and item.severity == Severity.HIGH for item in signals):
        return "FAIL"
    if any(item.severity in (Severity.MEDIUM, Severity.HIGH) for item in signals):
        return "REVIEW"
    return "PASS"


def analyze(context: FraudContext, detectors: dict[str, Detector] | None = None) -> FraudReport:
    detectors = detectors or DETECTORS
    found: list[Signal] = []
    for detector in detectors.values():
        found.extend(detector(context))
    matrix = crosscheck.build(context.comparisons)
    order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    found.sort(key=lambda item: (order[item.severity], item.signal))
    return FraudReport(
        signals=found,
        cross_check={name: {"pairs": item.pairs, "status": item.status, "outlier": item.outlier,
                            "protected_sources": item.protected_sources} for name, item in matrix.items()},
        cross_check_result=crosscheck.summary(matrix), result=result_for(found),
        coverage={name: {"status": status, "note": note} for name, (status, note) in COVERAGE.items()},
        detectors=list(detectors))
