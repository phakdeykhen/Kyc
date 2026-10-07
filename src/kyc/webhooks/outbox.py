"""Transactional outbox for webhook events.

`apply_event` records each session state change on the database session. Just before
that transaction commits, the changes become `webhook_deliveries` rows, one per
subscribed endpoint, in the same transaction. A committed state change therefore always
has its deliveries, and a rolled-back one never does. After the commit, the
organization is handed to the dispatcher, if the session factory has one.

Payloads carry identifiers, status and decision codes only, never identity data; the
receiver fetches the result with its API key.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import KYCSession, ManualReview, RiskAssessmentRecord, WebhookDelivery, WebhookEndpoint
from kyc.domain.enums import SessionStatus
from kyc.webhooks.events import events_for

PENDING_KEY = "kyc_webhook_events"
NOTIFY_KEY = "kyc_webhook_notify"
DISPATCHER_KEY = "kyc_webhook_dispatcher"
API_VERSION = "2026-10-06"
DECIDED = {SessionStatus.MANUAL_REVIEW, SessionStatus.VERIFIED, SessionStatus.REJECTED}


@dataclass(frozen=True)
class PendingEvent:
    organization_id: UUID
    session_id: UUID
    event_type: str
    previous_status: SessionStatus
    status: SessionStatus
    session_version: int
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: UUID = field(default_factory=uuid4)


def record_transition(db: Session, record: KYCSession, event, previous: SessionStatus) -> None:
    for event_type in events_for(event, previous, record.status):
        db.info.setdefault(PENDING_KEY, []).append(
            PendingEvent(record.organization_id, record.id, event_type, previous, record.status, record.version))


def notify_after_commit(db: Session, organization_id: UUID) -> None:
    db.info.setdefault(NOTIFY_KEY, set()).add(organization_id)


def envelope(event_id: UUID, event_type: str, organization_id: UUID, occurred_at: datetime, data: dict) -> dict:
    return {"id": str(event_id), "type": event_type, "api_version": API_VERSION,
            "created_at": occurred_at.isoformat(), "organization_id": str(organization_id), "data": data}


def _latest(db: Session, model, record: KYCSession):
    return db.scalar(sa.select(model).where(model.organization_id == record.organization_id, model.session_id == record.id)
                     .order_by(model.created_at.desc()).limit(1))


def _payload(db: Session, item: PendingEvent) -> dict:
    record = db.get(KYCSession, item.session_id)
    data = {"session_id": str(item.session_id), "user_id": record.user_id, "status": item.status.value,
            "previous_status": item.previous_status.value, "session_version": item.session_version,
            "verification_level": record.verification_level.value}
    if item.status in DECIDED:
        assessment = _latest(db, RiskAssessmentRecord, record)
        review = _latest(db, ManualReview, record)
        data["decision"] = None if assessment is None else {
            "result": assessment.decision.value, "reason_codes": list(assessment.reason_codes),
            "policy_version": assessment.policy_version}
        data["review"] = None if review is None else {"action": review.action.value, "reason_code": review.reason_code}
    return envelope(item.event_id, item.event_type, item.organization_id, item.occurred_at, data)


def materialize(db: Session) -> set[UUID]:
    """Write deliveries for the recorded events; return the organizations that got any."""
    pending: list[PendingEvent] = db.info.pop(PENDING_KEY, [])
    organizations: set[UUID] = set()
    endpoints_by_org: dict[UUID, list[WebhookEndpoint]] = {}
    now = datetime.now(timezone.utc)
    for item in pending:
        if item.organization_id not in endpoints_by_org:
            endpoints_by_org[item.organization_id] = list(db.scalars(sa.select(WebhookEndpoint).where(
                WebhookEndpoint.organization_id == item.organization_id, WebhookEndpoint.active.is_(True))))
        subscribed = [endpoint for endpoint in endpoints_by_org[item.organization_id]
                      if not endpoint.event_types or item.event_type in endpoint.event_types]
        if not subscribed:
            continue
        payload = _payload(db, item)
        for endpoint in subscribed:
            db.add(WebhookDelivery(organization_id=item.organization_id, endpoint_id=endpoint.id, event_id=item.event_id,
                                   event_type=item.event_type, session_id=item.session_id, payload=payload,
                                   status="PENDING", attempts=0, next_attempt_at=now))
        organizations.add(item.organization_id)
    if organizations:
        db.flush()
    return organizations


@sa.event.listens_for(Session, "before_commit")
def _before_commit(db: Session) -> None:
    if db.info.get(PENDING_KEY):
        for organization_id in materialize(db):
            notify_after_commit(db, organization_id)


@sa.event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    organizations = db.info.pop(NOTIFY_KEY, None)
    dispatcher = db.info.get(DISPATCHER_KEY)
    if organizations and dispatcher is not None:
        dispatcher.notify(organizations)


@sa.event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db: Session, transaction) -> None:
    if transaction.parent is None:
        # Rolled back (or already consumed by the commit): nothing may leak into the next transaction.
        db.info.pop(PENDING_KEY, None)
        db.info.pop(NOTIFY_KEY, None)
