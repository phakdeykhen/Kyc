"""Data-subject erasure (Phase 17, spec §22–23).

Erasing a session removes every personal and biometric artifact at once instead of
waiting for retention: document photos and selfies, extracted and encrypted fields,
MRZ/barcode/chip results, face templates, comparisons and liveness evidence. The
integrator's `user_id` is replaced, consents are revoked, and webhook payloads that
carried the `user_id` are rewritten.

What stays is what the spec prefers to keep: the verification result (status, the risk
decision with its reason codes, fraud signal codes) and the audit trail, none of which
hold identity values. Review decisions retain their coded outcome, but their free-text
notes are erased because they may contain identifying information.

A session still in progress is closed (EXPIRED) first, so nothing can be added after.
Encrypted objects are deleted by the caller after the transaction commits; the retention
sweep removes any the process did not reach.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from uuid import UUID

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.db.models import (AuditLog, BiometricTemplate, Consent, DocumentImage, FaceComparison, FaceQualityCheck,
                           IdentityDocument, KYCSession, LivenessChallenge, LivenessCheck, NFCChallenge, NFCResult,
                           ManualReview, SelfieCapture, WebhookDelivery)
from kyc.domain.state_machine import Event, TERMINAL_STATUSES
from kyc.services.sessions import apply_event, aware

log = logging.getLogger("kyc.privacy")

ERASED_USER = "ERASED"
# Deleted in this order; identity_documents cascades to images, fields, checks, MRZ, barcode and chip results.
SESSION_TABLES = (FaceComparison, BiometricTemplate, FaceQualityCheck, SelfieCapture, LivenessCheck,
                  LivenessChallenge, NFCResult, NFCChallenge, IdentityDocument)


@dataclass
class ErasureReport:
    session_id: UUID
    erased_at: datetime
    already_erased: bool
    deleted: dict[str, int] = field(default_factory=dict)
    object_refs: list[str] = field(default_factory=list)

    def view(self, status: str) -> dict:
        return {"session_id": self.session_id, "status": status, "erased_at": aware(self.erased_at),
                "already_erased": self.already_erased, "deleted": self.deleted,
                "encrypted_objects": len(self.object_refs)}


def erase_session(db: Session, tenant: TenantContext, record: KYCSession, request_id: UUID,
                  reason: str = "DATA_SUBJECT_REQUEST") -> ErasureReport:
    """Callers load `record` FOR UPDATE inside the tenant's transaction."""
    if record.organization_id != tenant.organization_id or tenant.session_id not in (None, record.id):
        raise HTTPException(404, detail="Session not found.")
    if record.erased_at is not None:
        return ErasureReport(record.id, record.erased_at, True)
    if record.status not in TERMINAL_STATUSES:
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
    scope = dict(organization_id=record.organization_id, session_id=record.id)
    images = list(db.scalars(sa.select(DocumentImage.encrypted_object_ref).filter_by(**scope)))
    selfies = list(db.scalars(sa.select(SelfieCapture.encrypted_object_ref).filter_by(**scope)))
    refs = list(dict.fromkeys(images + selfies))
    deleted = {"document_images": len(images)}  # removed by the identity_documents cascade
    for model in SESSION_TABLES:
        deleted[model.__tablename__] = db.execute(sa.delete(model).filter_by(**scope)).rowcount
    now = datetime.now(timezone.utc)
    for consent in db.scalars(sa.select(Consent).filter_by(**scope)):
        consent.user_id = ERASED_USER
        if consent.revoked_at is None:
            consent.revoked_at = now
    for delivery in db.scalars(sa.select(WebhookDelivery).where(WebhookDelivery.organization_id == record.organization_id,
                                                                WebhookDelivery.session_id == record.id)):
        data = dict(delivery.payload.get("data") or {})
        if "user_id" in data:
            data["user_id"] = ERASED_USER
            delivery.payload = {**delivery.payload, "data": data}
    record.user_id = ERASED_USER
    record.idempotency_key = None  # Caller-provided keys can themselves contain a customer identifier.
    record.request_fingerprint = None
    record.client_token_sha256 = None
    record.erased_at = now
    record.updated_at = now
    record.version += 1
    db.flush()
    if db.get_bind().dialect.name == "postgresql":
        # The API role cannot edit decision history. This function only removes notes for
        # this tenant's erased session and preserves the immutable decision and reason code.
        deleted["review_notes"] = db.scalar(sa.text("SELECT erase_review_notes(:session_id)"),
                                             {"session_id": str(record.id)})
    else:
        deleted["review_notes"] = db.execute(sa.update(ManualReview).filter_by(**scope).where(
            ManualReview.reason_ciphertext.is_not(None)).values(reason_ciphertext=None, key_version=None)).rowcount
    db.add(AuditLog(organization_id=record.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action="SESSION_DATA_ERASED", request_id=request_id, reason_codes=[reason],
                    from_status=record.status.value, to_status=record.status.value,
                    event_metadata={key: value for key, value in deleted.items() if value}))
    db.flush()
    return ErasureReport(record.id, now, False, {key: value for key, value in deleted.items() if value}, refs)


def subject_sessions(db: Session, organization_id: UUID, user_id: str) -> list[KYCSession]:
    return list(db.scalars(sa.select(KYCSession).where(KYCSession.organization_id == organization_id,
                                                       KYCSession.user_id == user_id)
                           .order_by(KYCSession.created_at).with_for_update()))


def delete_objects(store, report: ErasureReport) -> int:
    """After commit: remove the encrypted capture objects. Failures are left to the retention sweep."""
    removed = 0
    failed = 0
    for ref in report.object_refs:
        try:
            store.delete(ref)
            removed += 1
        except Exception:  # noqa: BLE001 — the orphan sweep in purge_captures.py is the backstop
            failed += 1
    if failed:
        # Object references and exception messages can contain sensitive data; log counts only.
        log.warning("Encrypted capture cleanup incomplete: session_id=%s failed_objects=%d; retention sweep required",
                    report.session_id, failed)
    return removed
