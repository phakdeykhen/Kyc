"""Client-facing session result. Masks the document number; never returns raw OCR or templates."""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.schemas import ResultDocument, ResultIdentity, SessionResult
from kyc.core.crypto import FieldCipher
from kyc.db.models import DocumentCheck, DocumentField, IdentityDocument, KYCSession
from kyc.domain.enums import CheckResult
from kyc.services.captures import side_progress

SEVERITY = {CheckResult.FAIL: 3, CheckResult.REVIEW: 2, CheckResult.PASS: 1, CheckResult.NOT_APPLICABLE: 0}
CHECK_GROUPS = {"CLASSIFICATION": "document_classification", "EXPIRY": "expiry", "MRZ": "mrz", "BARCODE": "barcode"}
DATA_CHECKS = {"REQUIRED_FIELDS", "DOCUMENT_NUMBER_FORMAT", "NATIONAL_ID_NUMBER_FORMAT", "DATE_CONSISTENCY",
               "OCR_CONFIDENCE", "SCRIPT_CONSISTENCY"}


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "*" * max(0, len(value) - 4) + value[-4:]


def build_result(db: Session, record: KYCSession, cipher: FieldCipher | None) -> SessionResult:
    checks: dict[str, str] = {}
    if all(state == "ACCEPTED" for state in side_progress(db, record).values()) or record.status.value not in {"CREATED", "DOCUMENT_REQUIRED"}:
        if db.scalar(sa.select(sa.func.count()).select_from(DocumentCheck).where(
                DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id,
                DocumentCheck.check_type == "CAPTURE_QUALITY", DocumentCheck.result == CheckResult.PASS)):
            checks["document_quality"] = "PASS"
    document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.organization_id == record.organization_id,
                                                           IdentityDocument.session_id == record.id,
                                                           IdentityDocument.processed_at.is_not(None)))
    if document is None or cipher is None:
        return SessionResult(session_id=record.id, status=record.status, checks=checks)

    rows = db.scalars(sa.select(DocumentCheck).where(DocumentCheck.organization_id == record.organization_id,
                                                     DocumentCheck.document_id == document.id,
                                                     DocumentCheck.created_at >= document.processed_at)).all()
    flags: list[str] = []
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

    values: dict[str, str] = {}
    for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                           DocumentField.document_id == document.id)):
        if field.normalized_value_ciphertext is not None:
            context = f"field/{record.organization_id}/{record.id}/{document.id}/{field.field_name}/normalized"
            values[field.field_name] = cipher.open(field.normalized_value_ciphertext, field.key_version, context)
    expiry = values.get("expiry_date")
    expiry_status = ("NOT_APPLICABLE" if checks.get("expiry") == "NOT_APPLICABLE" else "UNKNOWN") if not expiry \
        else "EXPIRED" if date.fromisoformat(expiry) < date.today() else "VALID"
    return SessionResult(
        session_id=record.id, status=record.status, checks=checks, review_flags=sorted(set(flags)),
        document=ResultDocument(country=document.issuing_country, type=document.document_type,
                                document_number_masked=mask(values.get("document_number")), expiry_status=expiry_status),
        identity=ResultIdentity(full_name=values.get("full_name"), full_name_local=values.get("full_name_local"),
                                date_of_birth=values.get("date_of_birth"), sex=values.get("sex"),
                                nationality=values.get("nationality")))
