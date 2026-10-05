from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.api.schemas import SessionCreate, SessionResponse
from kyc.db.models import AuditLog, KYCSession, Organization
from kyc.domain.enums import SessionStatus
from kyc.domain.state_machine import Event, TERMINAL_STATUSES, VerificationEvidence, transition


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


def create_session(db: Session, tenant: TenantContext, body: SessionCreate,
                   ttl: int, request_id: UUID) -> KYCSession:
    if not db.scalar(sa.select(Organization.id).where(Organization.id == tenant.organization_id)):
        raise HTTPException(409, detail="Organization has not been provisioned. Run the local bootstrap command.")
    now = datetime.now(timezone.utc)
    record = KYCSession(id=uuid4(), organization_id=tenant.organization_id,
                        user_id=body.user_id, country=body.country,
                        expected_document_type=body.expected_document_type,
                        verification_level=body.verification_level, status=SessionStatus.CREATED,
                        created_at=now, updated_at=now, expires_at=now + timedelta(seconds=ttl), version=1)
    db.add(record)
    db.flush()
    audit(db, record, tenant, request_id, "SESSION_CREATED")
    db.flush()
    return record


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
