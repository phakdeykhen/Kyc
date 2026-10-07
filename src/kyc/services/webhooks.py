"""Webhook endpoint management: create, change, disable, rotate secrets, test, deliveries."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.webhooks.secrets import WebhookSecretCipher
from kyc.db.models import WebhookDelivery, WebhookEndpoint
from kyc.services.tenancy import audit
from kyc.webhooks.delivery import TargetNotAllowed, resolve
from kyc.webhooks.dispatcher import secret_context
from kyc.webhooks.events import EVENT_TYPES, TEST_EVENT
from kyc.webhooks.outbox import envelope, notify_after_commit
from kyc.webhooks.signing import new_signing_secret

MAX_ENDPOINTS_PER_ORGANIZATION = 10


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def endpoint_view(row: WebhookEndpoint) -> dict:
    return {"id": row.id, "url": row.url, "description": row.description, "event_types": sorted(row.event_types),
            "active": row.active, "created_at": _aware(row.created_at), "disabled_at": _aware(row.disabled_at),
            "consecutive_failures": row.consecutive_failures,
            "previous_secret_expires_at": _aware(row.previous_secret_expires_at)}


def delivery_view(row: WebhookDelivery) -> dict:
    return {"id": row.id, "event_id": row.event_id, "event_type": row.event_type, "session_id": row.session_id,
            "status": row.status, "attempts": row.attempts, "next_attempt_at": _aware(row.next_attempt_at),
            "last_attempt_at": _aware(row.last_attempt_at), "last_status_code": row.last_status_code,
            "last_error": row.last_error, "delivered_at": _aware(row.delivered_at), "created_at": _aware(row.created_at)}


def _require_cipher(cipher: WebhookSecretCipher | None) -> WebhookSecretCipher:
    if cipher is None:
        raise HTTPException(503, detail="Webhook secrets need WEBHOOK_SECRET_KEYS (or, in development, PII_ENCRYPTION_KEYS).")
    return cipher


def _check_target(url: str, allow_private: bool) -> None:
    try:
        resolve(url, allow_private)
    except TargetNotAllowed as error:
        raise HTTPException(422, detail=str(error)) from None


def _check_events(event_types: list[str]) -> list[str]:
    unknown = set(event_types) - set(EVENT_TYPES)
    if unknown:
        raise HTTPException(422, detail=f"Unknown event types: {sorted(unknown)}.")
    return sorted(set(event_types))


def _seal(cipher: WebhookSecretCipher, row: WebhookEndpoint, secret: str) -> tuple[bytes, str]:
    return cipher.seal(secret, secret_context(row.organization_id, row.id))


def get_endpoint(db: Session, organization_id: UUID, endpoint_id: UUID, lock: bool = False) -> WebhookEndpoint:
    query = sa.select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id, WebhookEndpoint.id == endpoint_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail="Webhook endpoint not found.")
    return row


def list_endpoints(db: Session, organization_id: UUID, include_deleted: bool = False) -> list[WebhookEndpoint]:
    query = sa.select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id)
    if not include_deleted:
        query = query.where(WebhookEndpoint.active.is_(True))
    return list(db.scalars(query.order_by(WebhookEndpoint.created_at)))


def create_endpoint(db: Session, tenant: TenantContext, url: str, event_types: list[str], description: str | None,
                    cipher: WebhookSecretCipher | None, allow_private: bool, request_id: UUID) -> tuple[WebhookEndpoint, str]:
    cipher = _require_cipher(cipher)
    count = db.scalar(sa.select(sa.func.count()).select_from(WebhookEndpoint).where(
        WebhookEndpoint.organization_id == tenant.organization_id, WebhookEndpoint.active.is_(True)))
    if count >= MAX_ENDPOINTS_PER_ORGANIZATION:
        raise HTTPException(409, detail=f"An organization can have at most {MAX_ENDPOINTS_PER_ORGANIZATION} active endpoints.")
    _check_target(url, allow_private)
    secret = new_signing_secret()
    row = WebhookEndpoint(id=uuid4(), organization_id=tenant.organization_id, url=url, description=description,
                          event_types=_check_events(event_types), active=True, created_by=tenant.actor_id,
                          created_at=datetime.now(timezone.utc))
    row.secret_ciphertext, row.key_version = _seal(cipher, row, secret)
    db.add(row)
    db.flush()
    audit(db, tenant.organization_id, tenant.actor_id, "WEBHOOK_ENDPOINT_CREATED", request_id, endpoint_id=row.id)
    db.flush()
    return row, secret


def update_endpoint(db: Session, tenant: TenantContext, endpoint_id: UUID, changes: dict, allow_private: bool,
                    request_id: UUID) -> WebhookEndpoint:
    row = get_endpoint(db, tenant.organization_id, endpoint_id, lock=True)
    if not row.active:
        raise HTTPException(409, detail="This endpoint was deleted; create a new one.")
    if "url" in changes:
        _check_target(changes["url"], allow_private)
        row.url = changes["url"]
    if "event_types" in changes:
        row.event_types = _check_events(changes["event_types"])
    if "description" in changes:
        row.description = changes["description"]
    audit(db, tenant.organization_id, tenant.actor_id, "WEBHOOK_ENDPOINT_UPDATED", request_id, endpoint_id=row.id,
          fields=",".join(sorted(changes)))
    db.flush()
    return row


def delete_endpoint(db: Session, tenant: TenantContext, endpoint_id: UUID, request_id: UUID) -> WebhookEndpoint:
    """Disable permanently; history is kept and pending deliveries are abandoned."""
    row = get_endpoint(db, tenant.organization_id, endpoint_id, lock=True)
    if row.active:
        row.active, row.disabled_at = False, datetime.now(timezone.utc)
        db.execute(sa.update(WebhookDelivery).where(WebhookDelivery.organization_id == tenant.organization_id,
                                                    WebhookDelivery.endpoint_id == row.id, WebhookDelivery.status == "PENDING")
                   .values(status="ABANDONED", last_error="ENDPOINT_DISABLED"))
        audit(db, tenant.organization_id, tenant.actor_id, "WEBHOOK_ENDPOINT_DELETED", request_id, endpoint_id=row.id)
        db.flush()
    return row


def rotate_secret(db: Session, tenant: TenantContext, endpoint_id: UUID, cipher: WebhookSecretCipher | None, overlap_hours: int,
                  request_id: UUID) -> tuple[WebhookEndpoint, str]:
    """New secret now; the old one keeps signing alongside it for `overlap_hours`, so receivers can switch over."""
    cipher = _require_cipher(cipher)
    row = get_endpoint(db, tenant.organization_id, endpoint_id, lock=True)
    if not row.active:
        raise HTTPException(409, detail="This endpoint was deleted; create a new one.")
    secret = new_signing_secret()
    if overlap_hours > 0:
        row.previous_secret_ciphertext, row.previous_key_version = row.secret_ciphertext, row.key_version
        row.previous_secret_expires_at = datetime.now(timezone.utc) + timedelta(hours=overlap_hours)
    else:
        row.previous_secret_ciphertext = row.previous_key_version = row.previous_secret_expires_at = None
    row.secret_ciphertext, row.key_version = _seal(cipher, row, secret)
    audit(db, tenant.organization_id, tenant.actor_id, "WEBHOOK_SECRET_ROTATED", request_id, endpoint_id=row.id)
    db.flush()
    return row, secret


def send_test(db: Session, tenant: TenantContext, endpoint_id: UUID, request_id: UUID) -> WebhookDelivery:
    row = get_endpoint(db, tenant.organization_id, endpoint_id)
    if not row.active:
        raise HTTPException(409, detail="This endpoint was deleted.")
    now, event_id = datetime.now(timezone.utc), uuid4()
    delivery = WebhookDelivery(organization_id=tenant.organization_id, endpoint_id=row.id, event_id=event_id,
                               event_type=TEST_EVENT, session_id=None, status="PENDING", attempts=0, next_attempt_at=now,
                               payload=envelope(event_id, TEST_EVENT, tenant.organization_id, now,
                                                {"endpoint_id": str(row.id), "message": "Test delivery."}))
    db.add(delivery)
    db.flush()
    notify_after_commit(db, tenant.organization_id)
    return delivery


def list_deliveries(db: Session, organization_id: UUID, endpoint_id: UUID, status: str | None, limit: int) -> list[WebhookDelivery]:
    get_endpoint(db, organization_id, endpoint_id)
    query = sa.select(WebhookDelivery).where(WebhookDelivery.organization_id == organization_id,
                                             WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        query = query.where(WebhookDelivery.status == status)
    return list(db.scalars(query.order_by(WebhookDelivery.created_at.desc()).limit(limit)))


def redeliver(db: Session, tenant: TenantContext, endpoint_id: UUID, delivery_id: UUID, request_id: UUID) -> WebhookDelivery:
    """Queue an abandoned or delivered event again, with the same event ID (receivers deduplicate on it)."""
    endpoint = get_endpoint(db, tenant.organization_id, endpoint_id)
    if not endpoint.active:
        raise HTTPException(409, detail="This endpoint was deleted.")
    row = db.scalar(sa.select(WebhookDelivery).where(WebhookDelivery.organization_id == tenant.organization_id,
                                                     WebhookDelivery.endpoint_id == endpoint_id,
                                                     WebhookDelivery.id == delivery_id).with_for_update())
    if row is None:
        raise HTTPException(404, detail="Delivery not found.")
    if row.status == "PENDING":
        raise HTTPException(409, detail="This delivery is already queued.")
    row.status, row.attempts, row.next_attempt_at = "PENDING", 0, datetime.now(timezone.utc)
    row.last_error = None
    audit(db, tenant.organization_id, tenant.actor_id, "WEBHOOK_REDELIVERY_REQUESTED", request_id, delivery_id=row.id)
    db.flush()
    notify_after_commit(db, tenant.organization_id)
    return row
