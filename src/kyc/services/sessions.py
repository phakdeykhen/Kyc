from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.api.schemas import SessionCreate, SessionResponse
from kyc.db.models import AuditLog, KYCSession, Organization
from kyc.domain.enums import SessionStatus
from kyc.domain.state_machine import Event, TERMINAL_STATUSES, VerificationEvidence, transition
from kyc.tenancy.keys import CLIENT_TOKEN_PREFIX, new_secret


def aware(value: datetime) -> datetime:
    # SQLite's test driver drops timezone metadata; PostgreSQL uses TIMESTAMPTZ.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def respond(record: KYCSession) -> SessionResponse:
    return SessionResponse(session_id=record.id, organization_id=record.organization_id,
                           user_id=record.user_id, country=record.country,
                           expected_document_type=record.expected_document_type,
                           verification_level=record.verification_level, status=record.status,
                           created_at=aware(record.created_at), updated_at=aware(record.updated_at),
                           expires_at=aware(record.expires_at), version=record.version)


def audit(db: Session, record: KYCSession, tenant: TenantContext, request_id: UUID,
          action: str, previous: SessionStatus | None = None) -> None:
    db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id,
                    actor_id=tenant.actor_id, action=action, request_id=request_id,
                    from_status=previous.value if previous else None, to_status=record.status.value,
                    reason_codes=[action], event_metadata={"version": record.version}))


def _fingerprint(body: SessionCreate) -> str:
    return hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _replay(db: Session, tenant: TenantContext, existing: KYCSession, fingerprint: str, request_id: UUID) -> tuple[KYCSession, bool]:
    if existing.request_fingerprint != fingerprint:
        raise HTTPException(409, detail="This Idempotency-Key was already used with a different request.")
    audit(db, existing, tenant, request_id, "SESSION_CREATE_REPLAYED")
    db.flush()
    return existing, True


def create_session(db: Session, tenant: TenantContext, body: SessionCreate,
                   ttl: int, request_id: UUID, idempotency_key: str | None = None) -> tuple[KYCSession, bool]:
    """Create a session; with an Idempotency-Key, a retried identical request returns the same session.

    Returns the session and whether it was a replay of an earlier request.
    """
    if not db.scalar(sa.select(Organization.id).where(Organization.id == tenant.organization_id)):
        raise HTTPException(409, detail="Organization has not been provisioned. Run the local bootstrap command.")
    fingerprint = _fingerprint(body) if idempotency_key else None
    if idempotency_key:
        existing = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == tenant.organization_id,
                                                         KYCSession.idempotency_key == idempotency_key))
        if existing is not None:
            return _replay(db, tenant, existing, fingerprint, request_id)
    now = datetime.now(timezone.utc)
    record = KYCSession(id=uuid4(), organization_id=tenant.organization_id,
                        user_id=body.user_id, country=body.country,
                        expected_document_type=body.expected_document_type,
                        verification_level=body.verification_level, status=SessionStatus.CREATED,
                        created_at=now, updated_at=now, expires_at=now + timedelta(seconds=ttl), version=1,
                        created_by=tenant.actor_id, idempotency_key=idempotency_key, request_fingerprint=fingerprint)
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except sa.exc.IntegrityError:
        # A concurrent request with the same key committed first.
        existing = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == tenant.organization_id,
                                                         KYCSession.idempotency_key == idempotency_key)) if idempotency_key else None
        if existing is None:
            raise
        return _replay(db, tenant, existing, fingerprint, request_id)
    audit(db, record, tenant, request_id, "SESSION_CREATED")
    db.flush()
    return record, False


def issue_client_token(db: Session, tenant: TenantContext, record: KYCSession, request_id: UUID) -> str:
    """A new device token for this session; it replaces (and so revokes) any earlier one."""
    if record.status in TERMINAL_STATUSES:
        raise HTTPException(409, detail="The session is closed; create a new session.")
    token, token_hash = new_secret(CLIENT_TOKEN_PREFIX)
    record.client_token_sha256 = token_hash
    audit(db, record, tenant, request_id, "CLIENT_TOKEN_ISSUED")
    db.flush()
    return token


def get_session(db: Session, tenant: TenantContext, session_id: UUID, request_id: UUID) -> KYCSession:
    record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                        KYCSession.organization_id == tenant.organization_id).with_for_update())
    if record is None:
        raise HTTPException(404, detail="Session not found.")
    if record.status not in TERMINAL_STATUSES and aware(record.expires_at) <= datetime.now(timezone.utc):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
    audit(db, record, tenant, request_id, "SESSION_ACCESSED")
    db.flush()
    return record


def apply_event(db: Session, record: KYCSession, tenant: TenantContext, event: Event,
                request_id: UUID, evidence: VerificationEvidence | None = None) -> None:
    """Internal orchestrator operation; callers must load the row under FOR UPDATE."""
    if record.organization_id != tenant.organization_id:
        raise HTTPException(404, detail="Session not found.")
    previous = record.status
    if event != Event.EXPIRE and aware(record.expires_at) <= datetime.now(timezone.utc):
        # The worker transaction owner must commit this EXPIRE event and stop processing.
        event = Event.EXPIRE
    record.status = transition(previous, event, record.verification_level, evidence)
    record.updated_at = datetime.now(timezone.utc)
    record.version += 1
    audit(db, record, tenant, request_id, "SESSION_EXPIRED" if event == Event.EXPIRE else event.value, previous)
    db.flush()
