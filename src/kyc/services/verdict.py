"""Session outcome and per-check results in one standard vocabulary.

Every check reports one of PASS, FAIL, REVIEW, NOT_SUPPORTED, NOT_APPLICABLE, NOT_RUN or
ERROR. A check that did not run is NOT_RUN, a capability the platform does not have is
NOT_SUPPORTED, and an engine failure is ERROR: none of them is ever shown as PASS.

The final result separates identity outcomes from system failures: VERIFIED → PASS,
MANUAL_REVIEW → REVIEW, REJECTED → FAIL, while EXPIRED and TECHNICAL_ERROR carry no
identity decision. TECHNICAL_ERROR is reported while the latest step failed for an internal
reason; the session stays open so that a retry (or the worker) can still finish it.

Everything here is derived deterministically from stored evidence. Nothing here decides;
the risk engine does.
"""

from datetime import timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import AuditLog, KYCSession, RiskAssessmentRecord
from kyc.domain.enums import SessionStatus as S, VerificationLevel as L
from kyc.fraud.signals import COVERAGE
from kyc.risk.policy import LEVEL_REQUIRED

CHECK_STATUSES = ("PASS", "FAIL", "REVIEW", "NOT_SUPPORTED", "NOT_APPLICABLE", "NOT_RUN", "ERROR")
STORED = {"PASS": "PASS", "FAIL": "FAIL", "REVIEW": "REVIEW", "NOT_APPLICABLE": "NOT_APPLICABLE", "UNAVAILABLE": "ERROR"}

# (published check name, evidence key, group). Order is the order shown to reviewers.
CATALOG = (
    ("DOCUMENT_QUALITY", "document_quality", "DOCUMENT"),
    ("DOCUMENT_CLASSIFICATION", "document_classification", "DOCUMENT"),
    ("OCR_FIELD_VALIDATION", "document_data", "DOCUMENT"),
    ("MRZ", "mrz", "DOCUMENT"),
    ("MRZ_VISUAL_CONSISTENCY", "mrz_consistency", "DOCUMENT"),
    ("BARCODE", "barcode", "DOCUMENT"),
    ("EXPIRY", "expiry", "DOCUMENT"),
    ("ISSUING_COUNTRY", "issuing_country", "DOCUMENT"),
    ("DOCUMENT_PORTRAIT", "document_portrait", "BIOMETRIC"),
    ("DOCUMENT_PORTRAIT_QUALITY", "portrait_quality", "BIOMETRIC"),
    ("LIVE_FACE_QUALITY", "face_quality", "BIOMETRIC"),
    ("FACE_MATCH", "face_match", "BIOMETRIC"),
    ("LIVENESS", "liveness", "LIVENESS"),
    ("NFC", "nfc", "NFC"),
    ("NFC_DOCUMENT_CONSISTENCY", "chip_document_consistency", "NFC"),
    ("NFC_ACTIVE_AUTHENTICATION", "chip_active_authentication", "NFC"),
    ("NFC_PORTRAIT_FACE_MATCH", "chip_face_match", "NFC"),
    ("CROSS_CHECK", "cross_check", "CROSS_CHECK"),
    ("FRAUD_ANALYSIS", "fraud", "FRAUD"),
)
# Evidence that only exists at some verification levels.
LEVEL_ONLY = {"portrait_quality", "face_quality", "face_match", "liveness", "nfc", "chip_document_consistency",
              "chip_active_authentication", "chip_face_match"}
# Forensic capabilities reported by the fraud engine's own coverage table.
FORENSICS = ("DOCUMENT_LAYOUT_MISMATCH", "FONT_INCONSISTENCY", "TEXT_REGION_INCONSISTENCY", "IMAGE_MANIPULATION",
             "SCREENSHOT_REPRODUCTION", "PORTRAIT_REPLACEMENT", "METADATA_ANOMALY")

FINAL = {S.VERIFIED: ("VERIFIED", "PASS"), S.MANUAL_REVIEW: ("MANUAL_REVIEW", "REVIEW"),
         S.REJECTED: ("REJECTED", "FAIL"), S.EXPIRED: ("EXPIRED", None)}
# Audit actions recorded when an internal service, not the person's evidence, stopped a step.
TECHNICAL_ACTIONS = {"RISK_ASSESSMENT_FAILED", "DOCUMENT_PROCESSING_FAILED", "FRAUD_ANALYSIS_FAILED",
                     "BIOMETRIC_PROCESSING_UNAVAILABLE", "LIVENESS_UNAVAILABLE", "REFERENCE_FACE_UNAVAILABLE"}
# Reading a session or case is audited too, but it is not a step of the verification.
READ_ONLY_ACTIONS = ("SESSION_ACCESSED", "REVIEW_CASE_VIEWED", "REVIEW_IMAGE_VIEWED", "REVIEW_ACCESS_DENIED")
RECAPTURE_STATUSES = {S.DOCUMENT_REQUIRED, S.SELFIE_REQUIRED, S.LIVENESS_REQUIRED, S.NFC_REQUIRED}
END_USER = {
    "PASS": "Identity verification completed successfully.",
    "REVIEW": "Your verification has been submitted for additional review.",
    "FAIL": "We could not verify your identity.",
    "RECAPTURE": "Please take a new photo and try again.",
    "TECHNICAL": "We couldn't complete verification right now. Please try again.",
    "EXPIRED": "This verification has expired. Please start again.",
    "IN_PROGRESS": "Your verification is in progress.",
}


def _applicable(key: str, level: L) -> bool:
    if key.startswith("chip_"):
        return level == L.DOCUMENT_FACE_LIVENESS_NFC
    return key not in LEVEL_ONLY or key in LEVEL_REQUIRED[level]


def check_results(checks: dict[str, str], level: L, assessment: RiskAssessmentRecord | None,
                  coverage: dict | None = None) -> list[dict]:
    results = []
    for name, key, group in CATALOG:
        value = checks.get(key)
        if value is not None:
            status = STORED.get(value, "REVIEW")
        else:
            status = "NOT_RUN" if _applicable(key, level) else "NOT_APPLICABLE"
        results.append({"check_name": name, "group": group, "status": status})
    sources = None if assessment is None else assessment.check_summary.get("authenticity_sources")
    results.append({"check_name": "DOCUMENT_AUTHENTICITY", "group": "DOCUMENT",
                    "status": "NOT_RUN" if sources is None else "PASS" if sources else "REVIEW",
                    "reason_codes": [] if sources is None else (list(sources) or ["DOCUMENT_AUTHENTICITY_UNVERIFIED"])})
    table = coverage or {name: {"status": status} for name, (status, _) in COVERAGE.items()}
    for name in FORENSICS:
        support = table.get(name, {}).get("status", "NOT_SUPPORTED")
        if support == "NOT_SUPPORTED":
            results.append({"check_name": f"FORENSICS_{name}", "group": "FRAUD", "status": "NOT_SUPPORTED"})
    return results


def latest_assessment(db: Session, record: KYCSession) -> RiskAssessmentRecord | None:
    return db.scalar(sa.select(RiskAssessmentRecord).where(RiskAssessmentRecord.organization_id == record.organization_id,
                                                           RiskAssessmentRecord.session_id == record.id)
                     .order_by(RiskAssessmentRecord.created_at.desc()).limit(1))


def _latest_audit(db: Session, record: KYCSession) -> AuditLog | None:
    return db.scalar(sa.select(AuditLog).where(AuditLog.organization_id == record.organization_id,
                                               AuditLog.session_id == record.id,
                                               AuditLog.action.not_in(READ_ONLY_ACTIONS))
                     .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))


def final_result(db: Session, record: KYCSession) -> dict:
    """final_result, decision (identity outcome or None) and the end-user message key."""
    if record.status in FINAL:
        result, decision = FINAL[record.status]
        message = decision or "EXPIRED"
        return {"final_result": result, "decision": decision, "retry_allowed": False,
                "end_user_message": END_USER[message], "end_user_message_code": message}
    latest = _latest_audit(db, record)
    if latest is not None and latest.action in TECHNICAL_ACTIONS:
        return {"final_result": "TECHNICAL_ERROR", "decision": None, "retry_allowed": True,
                "end_user_message": END_USER["TECHNICAL"], "end_user_message_code": "TECHNICAL"}
    if latest is not None and record.status in RECAPTURE_STATUSES and latest.action in {
            "DOCUMENT_RECAPTURE_REQUESTED", "DOCUMENT_CAPTURE_REJECTED", "SELFIE_CAPTURE_RECAPTURE",
            "SELFIE_CAPTURE_REJECTED", "LIVENESS_RETRY_REQUIRED", "NFC_RETRY_REQUIRED"}:
        return {"final_result": "IN_PROGRESS", "decision": None, "retry_allowed": True,
                "end_user_message": END_USER["RECAPTURE"], "end_user_message_code": "RECAPTURE"}
    return {"final_result": "IN_PROGRESS", "decision": None, "retry_allowed": record.status in RECAPTURE_STATUSES,
            "end_user_message": END_USER["IN_PROGRESS"], "end_user_message_code": "IN_PROGRESS"}


EXPLAIN = {
    "LOW_OCR_CONFIDENCE": "Text read from the document has low confidence",
    "FIELD_MISMATCH": "Independent sources on the document disagree",
    "DOCUMENT_AUTHENTICITY_UNVERIFIED": "Document authenticity is unverified",
    "FACE_MATCH_UNCALIBRATED": "Face matching is not calibrated, so the face comparison needs a person",
    "FACE_SCORE_BORDERLINE": "Face comparison is borderline",
    "LIVENESS_INCONCLUSIVE": "Liveness is inconclusive",
    "MRZ_INVALID": "The machine-readable zone did not validate",
    "EXPIRY_UNKNOWN": "The expiry date could not be confirmed",
    "LOW_CLASSIFICATION_CONFIDENCE": "The document type was not recognized with confidence",
    "NFC_NOT_VERIFIED": "The passport chip was not verified",
    "FRAUD_SIGNALS_PRESENT": "Fraud signals were raised",
}
PLAIN = {"PASS": "passed", "FAIL": "failed", "REVIEW": "needs review", "NOT_RUN": "was not run",
         "ERROR": "could not run", "NOT_APPLICABLE": "does not apply", "NOT_SUPPORTED": "is not supported"}


def review_summary(assessment: RiskAssessmentRecord | None, results: list[dict]) -> list[str]:
    """Deterministic 'why' lines for a reviewer, built only from recorded evidence (never a model)."""
    if assessment is None:
        return ["No risk assessment has been made yet."]
    lines = [EXPLAIN.get(code, code.replace("_", " ").capitalize()) for code in assessment.reason_codes
             if assessment.decision.value != "PASS"]
    by_name = {item["check_name"]: item["status"] for item in results}
    for name in ("MRZ_VISUAL_CONSISTENCY", "FACE_MATCH", "LIVENESS", "DOCUMENT_AUTHENTICITY"):
        if name in by_name and by_name[name] != "NOT_APPLICABLE":
            lines.append(f"{name.replace('_', ' ').capitalize()} {PLAIN[by_name[name]]}")
    return list(dict.fromkeys(lines))


TIMELINE_EVENTS = {
    "SESSION_CREATED": "Session created", "DOCUMENT_CONSENT_GRANTED": "Consent received",
    "DOCUMENT_CAPTURE_ACCEPTED": "Document uploaded and quality passed", "DOCUMENT_CAPTURE_REJECTED": "Document photo refused",
    "DOCUMENT_EXTRACTED": "OCR completed", "DOCUMENT_RECAPTURE_REQUESTED": "Document recapture requested",
    "DOCUMENT_PROCESSING_FAILED": "Document processing failed (technical)",
    "BIOMETRIC_CONSENT_GRANTED": "Biometric consent received", "FACE_COMPARISON_RECORDED": "Face comparison completed",
    "LIVENESS_CHALLENGE_ISSUED": "Liveness started", "LIVENESS_RECORDED": "Liveness completed",
    "LIVENESS_RETRY_REQUIRED": "Liveness retry requested", "NFC_RECORDED": "NFC completed",
    "FRAUD_ANALYZED": "Fraud analysis completed", "RISK_ASSESSED": "Risk engine decided",
    "RISK_ASSESSMENT_FAILED": "Risk assessment failed (technical)", "REVIEW_DECISION": "Reviewer decided",
    "SESSION_EXPIRED": "Session expired", "SESSION_DATA_ERASED": "Personal data erased",
}


def timeline(db: Session, record: KYCSession) -> list[dict]:
    rows = db.scalars(sa.select(AuditLog).where(AuditLog.organization_id == record.organization_id,
                                                AuditLog.session_id == record.id,
                                                AuditLog.action.in_(tuple(TIMELINE_EVENTS)))
                      .order_by(AuditLog.created_at, AuditLog.id)).all()
    events, previous = [], None
    for row in rows:
        at = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        events.append({"event": row.action, "label": TIMELINE_EVENTS[row.action], "at": at,
                       "since_previous_ms": None if previous is None else int((at - previous).total_seconds() * 1000)})
        previous = at
    return events
