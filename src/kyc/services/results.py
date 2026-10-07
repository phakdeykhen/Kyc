"""Client-facing session result. Masks the document number; never returns raw OCR or templates."""

from dataclasses import dataclass
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.schemas import FaceComparisonSummary, FraudSignalSummary, ResultDecision, ResultDocument, ResultReview, ResultIdentity, ResultMRZ, SessionResult
from kyc.core.crypto import FieldCipher
from kyc.db.models import BiometricTemplate, DocumentCheck, DocumentField, FaceComparison, FaceQualityCheck, FraudSignal, IdentityDocument, KYCSession, LivenessCheck, ManualReview, MRZResult, NFCResult, RiskAssessmentRecord
from kyc.domain.enums import CheckResult
from kyc.services.captures import side_progress
from kyc.services.government import build_government_verification

# NFC_VERIFIED means signed by a trusted issuer and unaltered; it is still evidence, not a decision.
NFC_CHECK = {"NFC_VERIFIED": "PASS", "NFC_READ": "REVIEW", "NFC_FAILED": "FAIL", "NFC_NOT_AVAILABLE": "REVIEW",
             "NFC_NOT_SUPPORTED": "NOT_APPLICABLE"}

SEVERITY = {CheckResult.FAIL: 3, CheckResult.REVIEW: 2, CheckResult.PASS: 1, CheckResult.NOT_APPLICABLE: 0}
CHECK_GROUPS = {"CLASSIFICATION": "document_classification", "EXPIRY": "expiry", "MRZ": "mrz", "PORTRAIT": "document_portrait",
                "MRZ_CONSISTENCY": "mrz_consistency", "BARCODE": "barcode", "ISSUING_COUNTRY": "issuing_country",
                "CROSS_CHECK": "cross_check", "FRAUD_ANALYSIS": "fraud"}
DATA_CHECKS = {"REQUIRED_FIELDS", "DOCUMENT_NUMBER_FORMAT", "NATIONAL_ID_NUMBER_FORMAT", "DATE_CONSISTENCY",
               "OCR_CONFIDENCE", "SCRIPT_CONSISTENCY", "KHMER_NAME", "KHMER_TEXT"}


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "*" * max(0, len(value) - 4) + value[-4:]


def mask_name(value: str | None) -> str | None:
    """Keep the first character of each word: enough to confirm a match, not to read the name."""
    if not value:
        return None
    return " ".join(word[0] + "*" * (len(word) - 1) for word in value.split())


def mask_date(value: str | None) -> str | None:
    return None if not value else value[:4] + "-**-**"


@dataclass
class Evidence:
    """Everything the result shows and the risk engine decides on: one source, so they never disagree."""

    checks: dict[str, str]
    flags: list[str]
    face_comparison: FaceComparisonSummary | None
    signals: list[FraudSignalSummary]
    document: IdentityDocument | None


def collect_evidence(db: Session, record: KYCSession) -> Evidence:
    checks: dict[str, str] = {}
    flags: list[str] = []
    now = datetime.now(timezone.utc)
    current_document = db.scalar(sa.select(IdentityDocument).where(
        IdentityDocument.organization_id == record.organization_id,
        IdentityDocument.session_id == record.id, IdentityDocument.processed_at.is_not(None)))
    for source, key in (("LIVE_SELFIE", "face_quality"), ("DOCUMENT_PORTRAIT", "portrait_quality")):
        query = sa.select(FaceQualityCheck).where(
            FaceQualityCheck.organization_id == record.organization_id,
            FaceQualityCheck.session_id == record.id, FaceQualityCheck.source == source,
            FaceQualityCheck.delete_after > now)
        if current_document is not None:
            query = query.where(FaceQualityCheck.created_at >= current_document.processed_at)
        quality = db.scalar(query.order_by(FaceQualityCheck.created_at.desc()).limit(1))
        if quality is not None:
            checks[key] = quality.result.value
            if quality.result in (CheckResult.REVIEW, CheckResult.FAIL):
                flags.extend(quality.evidence_metadata.get("reason_codes", []))
                unverified_codes = {"EYES_VISIBLE": "FACE_EYE_VISIBILITY_UNVERIFIED",
                                    "SEVERE_OCCLUSION": "FACE_OCCLUSION_UNVERIFIED"}
                flags.extend(unverified_codes[item] for item in quality.evidence_metadata.get("unverified_checks", [])
                             if item in unverified_codes)
    nfc = next((row for row in db.scalars(sa.select(NFCResult).where(
        NFCResult.organization_id == record.organization_id, NFCResult.session_id == record.id)
        .order_by(NFCResult.created_at.desc())) if not row.evidence_metadata.get("retryable")), None)
    if nfc is not None:
        checks["nfc"] = NFC_CHECK[nfc.status.value]
        checks["nfc_status"] = nfc.status.value
        consistency = nfc.evidence_metadata.get("document_consistency") or {}
        if consistency:
            checks["chip_document_consistency"] = "FAIL" if "MISMATCH" in consistency.values() else "PASS"
        if nfc.active_authentication is not None:
            checks["chip_active_authentication"] = "PASS" if nfc.active_authentication else "FAIL"
        face = nfc.evidence_metadata.get("chip_face_match")
        if face:
            checks["chip_face_match"] = face["result"]
        if checks["nfc"] in ("REVIEW", "FAIL") or "MISMATCH" in consistency.values():
            flags.extend(f"NFC_{code}" for code in nfc.evidence_metadata.get("reason_codes", []))
    # Retry-only attempts (challenge not completed) are not results; report the latest final one.
    liveness = next((row for row in db.scalars(sa.select(LivenessCheck).where(
        LivenessCheck.organization_id == record.organization_id, LivenessCheck.session_id == record.id)
        .order_by(LivenessCheck.created_at.desc())) if not row.evidence_metadata.get("retryable")), None)
    if liveness is not None:
        checks["liveness"] = liveness.result.value
        if liveness.result in (CheckResult.REVIEW, CheckResult.FAIL):
            flags.extend(f"LIVENESS_{code}" if not code.startswith("LIVENESS") else code
                         for code in liveness.evidence_metadata.get("reason_codes", []))
    reference = sa.orm.aliased(BiometricTemplate)
    live = sa.orm.aliased(BiometricTemplate)
    comparison = db.scalar(sa.select(FaceComparison).join(reference, FaceComparison.reference_template_id == reference.id)
        .join(live, FaceComparison.live_template_id == live.id).where(
            FaceComparison.organization_id == record.organization_id, FaceComparison.session_id == record.id,
            reference.organization_id == record.organization_id, live.organization_id == record.organization_id,
            reference.delete_after > now, live.delete_after > now)
        .order_by(FaceComparison.created_at.desc()).limit(1))
    summary = None
    if comparison is not None:
        checks["face_match"] = comparison.result.value
        flags.extend(comparison.evidence_metadata.get("reason_codes", []))
        summary = FaceComparisonSummary(score=comparison.comparison_score, metric=comparison.comparison_metric,
            result=comparison.result, policy_version=comparison.threshold_policy_version,
            model_name=comparison.model_name, model_version=comparison.model_version,
            calibrated=bool(comparison.evidence_metadata.get("calibrated", False)))
    if all(state == "ACCEPTED" for state in side_progress(db, record).values()) or record.status.value not in {"CREATED", "DOCUMENT_REQUIRED"}:
        if db.scalar(sa.select(sa.func.count()).select_from(DocumentCheck).where(
                DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id,
                DocumentCheck.check_type == "CAPTURE_QUALITY", DocumentCheck.result == CheckResult.PASS)):
            checks["document_quality"] = "PASS"
    document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.organization_id == record.organization_id,
                                                           IdentityDocument.session_id == record.id,
                                                           IdentityDocument.processed_at.is_not(None)))
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    signals = [FraudSignalSummary(signal=row.signal, severity=row.severity, category=row.category)
               for row in sorted(db.scalars(sa.select(FraudSignal).where(FraudSignal.organization_id == record.organization_id,
                                                                         FraudSignal.session_id == record.id)),
                                 key=lambda row: (order[row.severity], row.signal))]
    if document is not None:
        rows = db.scalars(sa.select(DocumentCheck).where(DocumentCheck.organization_id == record.organization_id,
                                                         DocumentCheck.document_id == document.id,
                                                         DocumentCheck.created_at >= document.processed_at)).all()
        data_result = None
        for row in rows:
            if row.check_type in CHECK_GROUPS:
                checks[CHECK_GROUPS[row.check_type]] = row.result.value
            if row.check_type in DATA_CHECKS and (data_result is None or SEVERITY.get(row.result, 0) > SEVERITY.get(data_result, 0)):
                data_result = row.result
            if row.result in (CheckResult.REVIEW, CheckResult.FAIL):
                flags.extend(row.evidence_metadata.get("reason_codes", []))
        if data_result is not None:
            checks["document_data"] = data_result.value
    return Evidence(checks, flags, summary, signals, document)


def latest_decision(db: Session, record: KYCSession) -> ResultDecision | None:
    row = db.scalar(sa.select(RiskAssessmentRecord).where(RiskAssessmentRecord.organization_id == record.organization_id,
                                                          RiskAssessmentRecord.session_id == record.id)
                    .order_by(RiskAssessmentRecord.created_at.desc()).limit(1))
    if row is None:
        return None
    return ResultDecision(result=row.decision, reason_codes=row.reason_codes, policy_version=row.policy_version,
                          assessed_at=row.created_at)


def latest_review(db: Session, record: KYCSession) -> ResultReview | None:
    row = db.scalar(sa.select(ManualReview).where(ManualReview.organization_id == record.organization_id,
                                                  ManualReview.session_id == record.id)
                    .order_by(ManualReview.created_at.desc()).limit(1))
    return None if row is None else ResultReview(action=row.action.value, reason_code=row.reason_code,
                                                 decided_at=row.created_at)


def build_result(db: Session, record: KYCSession, cipher: FieldCipher | None, reveal_identity: bool = True) -> SessionResult:
    """`reveal_identity` is False for credentials without the results:identity scope (spec §25)."""
    evidence = collect_evidence(db, record)
    checks, flags, summary, signals, document = (evidence.checks, evidence.flags, evidence.face_comparison,
                                                 evidence.signals, evidence.document)
    decision, review = latest_decision(db, record), latest_review(db, record)
    erased_at = record.erased_at.replace(tzinfo=record.erased_at.tzinfo or timezone.utc) if record.erased_at else None
    government = build_government_verification(db, record, cipher, reveal_link=reveal_identity)
    if document is None or cipher is None:
        return SessionResult(session_id=record.id, status=record.status, checks=checks, fraud_signals=signals,
                             face_comparison=summary, review_flags=sorted(set(flags)), decision=decision, review=review,
                             erased_at=erased_at, government_verification=government)

    values: dict[str, str] = {}
    for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                           DocumentField.document_id == document.id)):
        if field.normalized_value_ciphertext is not None:
            context = f"field/{record.organization_id}/{record.id}/{document.id}/{field.field_name}/normalized"
            values[field.field_name] = cipher.open(field.normalized_value_ciphertext, field.key_version, context)
    expiry = values.get("expiry_date")
    expiry_status = ("NOT_APPLICABLE" if checks.get("expiry") == "NOT_APPLICABLE" else "UNKNOWN") if not expiry \
        else "EXPIRED" if date.fromisoformat(expiry) < date.today() else "VALID"
    mrz = db.scalar(sa.select(MRZResult).where(MRZResult.organization_id == record.organization_id,
                                             MRZResult.session_id == record.id,
                                             MRZResult.document_id == document.id))
    return SessionResult(
        session_id=record.id, status=record.status, checks=checks, review_flags=sorted(set(flags)), face_comparison=summary,
        fraud_signals=signals, decision=decision, review=review,
        document=ResultDocument(country=document.issuing_country, type=document.document_type,
                                document_number_masked=mask(values.get("document_number")), expiry_status=expiry_status),
        identity=ResultIdentity(full_name=values.get("full_name"), full_name_local=values.get("full_name_local"),
                                date_of_birth=values.get("date_of_birth"), sex=values.get("sex"),
                                nationality=values.get("nationality")) if reveal_identity else
        ResultIdentity(full_name=mask_name(values.get("full_name")), full_name_local=mask_name(values.get("full_name_local")),
                       date_of_birth=mask_date(values.get("date_of_birth")), sex=values.get("sex"),
                       nationality=values.get("nationality")),
        identity_masked=not reveal_identity, government_verification=government,
        mrz=ResultMRZ(format=mrz.format, mrz_valid=mrz.mrz_valid, check_digit_results=mrz.check_digit_results,
                      field_consistency=mrz.field_consistency) if mrz else None)
