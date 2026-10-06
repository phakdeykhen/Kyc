"""Document-processing consent (Phase 17, spec §22 "consent records").

The person grants consent on their device before any identity document is captured.
With REQUIRE_DOCUMENT_CONSENT (mandatory in production) document uploads are refused
until it is recorded. Biometric consent stays separate and is given with the selfie.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.db.models import AuditLog, Consent, KYCSession
from kyc.domain.state_machine import TERMINAL_STATUSES
from kyc.services.sessions import aware

DOCUMENT_SCOPE = "DOCUMENT_PROCESSING"


def active_consent(db: Session, record: KYCSession, scope: str, policy_version: str | None = None) -> Consent | None:
    if record.erased_at is not None:
        return None
    query = sa.select(Consent).where(
        Consent.organization_id == record.organization_id, Consent.session_id == record.id, Consent.scope == scope,
        Consent.granted.is_(True), Consent.revoked_at.is_(None))
    if policy_version is not None:
        query = query.where(Consent.policy_version == policy_version)
    return db.scalar(query.order_by(Consent.created_at.desc()).limit(1))


def record_document_consent(db: Session, tenant: TenantContext, record: KYCSession, policy_version: str,
                            request_id: UUID) -> tuple[Consent, bool]:
    """Callers hold the session row FOR UPDATE; return the consent and whether it is new."""
    if record.organization_id != tenant.organization_id or tenant.session_id not in (None, record.id):
        raise HTTPException(404, detail="Session not found.")
    now = datetime.now(timezone.utc)
    if record.status in TERMINAL_STATUSES or record.erased_at is not None or aware(record.expires_at) <= now:
        raise HTTPException(409, detail="The session is closed; create a new session.")
    existing = active_consent(db, record, DOCUMENT_SCOPE)
    if existing is not None and existing.policy_version == policy_version:
        return existing, False
    # Preserve the history but supersede the previous notice; uploads must use the current policy.
    for previous in db.scalars(sa.select(Consent).where(
        Consent.organization_id == record.organization_id, Consent.session_id == record.id,
        Consent.scope == DOCUMENT_SCOPE, Consent.revoked_at.is_(None))):
        previous.revoked_at = now
    consent = Consent(organization_id=record.organization_id, session_id=record.id, user_id=record.user_id,
                      scope=DOCUMENT_SCOPE, policy_version=policy_version, granted=True,
                      created_at=now)
    db.add(consent)
    db.add(AuditLog(organization_id=record.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action="DOCUMENT_CONSENT_GRANTED", request_id=request_id, reason_codes=["EXPLICIT_DOCUMENT_CONSENT"],
                    event_metadata={"policy_version": policy_version}))
    db.flush()
    return consent, True
