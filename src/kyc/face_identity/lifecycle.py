"""Retention and restore reconciliation. Caller supplies tenant-scoped maintenance access."""

from datetime import timedelta
from uuid import uuid4
import sqlalchemy as sa

from kyc.db.models import (FaceActor, FaceCaptureSession, FaceConsent, FaceDeletion, FaceEnrollment,
                          FaceRecognitionEvent, FaceSampleReceipt, FaceSubject, FaceTemplate)
from .service import now


def maintain(db, service, tenant):
    expired = list(db.scalars(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == tenant,
        FaceEnrollment.status.in_(["ACTIVE", "DUPLICATE_REVIEW"]), FaceEnrollment.expires_at <= now())))
    for row in expired:
        service.revoke(db, row, "RETENTION_EXPIRY", "EXPIRED")
    # Reconcile durable external denials BEFORE permitting any restored population.
    denials = service.ledger.denied(tenant)
    restored = list(db.scalars(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == tenant,
        FaceEnrollment.status.in_(["ACTIVE", "DUPLICATE_REVIEW"]))))
    blocked = [row for row in restored if str(row.id) in denials]
    for row in blocked:
        service.revoke(db, row, "RESTORE_DENIAL")
    synced = 0
    for row in db.scalars(sa.select(FaceDeletion).where(FaceDeletion.organization_id == tenant,
                                                      FaceDeletion.ledger_synced.is_(False))):
        service.ledger.append(tenant, row.enrollment_id, row.reason)
        row.ledger_synced = True
        synced += 1
    db.execute(sa.update(FaceCaptureSession).where(FaceCaptureSession.organization_id == tenant,
        FaceCaptureSession.expires_at <= now()).values(sample_ciphertext=None, sample_key_version=None,
                                                     reference_hash=None, status="EXPIRED"))
    receipts = db.execute(sa.delete(FaceSampleReceipt).where(FaceSampleReceipt.organization_id == tenant,
                                         FaceSampleReceipt.expires_at <= now())).rowcount
    profiles = db.execute(sa.update(FaceSubject).where(FaceSubject.organization_id == tenant,
        FaceSubject.profile_expires_at <= now()).values(profile_ciphertext=None, profile_key_version=None)).rowcount
    db.execute(sa.update(FaceRecognitionEvent).where(FaceRecognitionEvent.organization_id == tenant,
        FaceRecognitionEvent.created_at <= now() - timedelta(days=7)).values(evidence_ciphertext=None, evidence_key_version=None))
    events = db.execute(sa.delete(FaceRecognitionEvent).where(FaceRecognitionEvent.organization_id == tenant,
        FaceRecognitionEvent.created_at <= now() - timedelta(days=30))).rowcount
    return {"expired_enrollments": len(expired), "restored_enrollments_removed": len(blocked),
            "deletions_synced": synced, "expired_replay_receipts": receipts, "expired_events": events,
            "expired_profiles": profiles}


def erase_kyc_identity(db, record, ledger=None):
    """Called inside the KYC erasure transaction. Only metadata is readable by kyc_app."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                   {"scope": "face-identity/" + str(record.organization_id)})
    subjects = sa.select(FaceSubject.id).where(FaceSubject.organization_id == record.organization_id,
                                              FaceSubject.kyc_session_id == record.id)
    enrollments = list(db.scalars(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == record.organization_id,
                                                                FaceEnrollment.subject_id.in_(subjects))))
    synced = set()
    for enrollment in enrollments:
        try:
            if ledger:
                ledger.append(record.organization_id, enrollment.id, "KYC_ERASURE")
                synced.add(enrollment.id)
        except Exception:
            pass  # Pending tombstones make recognition fail closed until synchronized.
    if db.get_bind().dialect.name == "postgresql":
        return db.scalar(sa.text("SELECT erase_face_identity(:session)"), {"session": record.id})
    actors = sa.select(FaceActor.id).where(FaceActor.organization_id == record.organization_id, FaceActor.subject_id.in_(subjects))
    for enrollment in enrollments:
        if not db.scalar(sa.select(FaceDeletion.id).where(FaceDeletion.organization_id == record.organization_id,
                                                        FaceDeletion.enrollment_id == enrollment.id)):
            db.add(FaceDeletion(id=uuid4(), organization_id=record.organization_id, enrollment_id=enrollment.id,
                                reason="KYC_ERASURE", ledger_synced=enrollment.id in synced))
        enrollment.status = "REVOKED"
        db.execute(sa.delete(FaceTemplate).where(FaceTemplate.organization_id == record.organization_id,
                                                FaceTemplate.enrollment_id == enrollment.id))
    db.execute(sa.update(FaceCaptureSession).where(FaceCaptureSession.organization_id == record.organization_id,
        FaceCaptureSession.actor_id.in_(actors)).values(status="REVOKED", reference_hash=None,
                                                      sample_ciphertext=None, sample_key_version=None))
    db.execute(sa.update(FaceRecognitionEvent).where(FaceRecognitionEvent.organization_id == record.organization_id,
        FaceRecognitionEvent.actor_id.in_(actors)).values(evidence_ciphertext=None, evidence_key_version=None))
    db.execute(sa.update(FaceConsent).where(FaceConsent.organization_id == record.organization_id,
        FaceConsent.subject_id.in_(subjects)).values(withdrawn_at=now()))
    db.execute(sa.update(FaceActor).where(FaceActor.organization_id == record.organization_id,
        FaceActor.id.in_(actors)).values(revoked_at=now()))
    db.execute(sa.update(FaceSubject).where(FaceSubject.organization_id == record.organization_id,
        FaceSubject.id.in_(subjects)).values(active=False, profile_ciphertext=None, profile_key_version=None))
    return len(enrollments)
