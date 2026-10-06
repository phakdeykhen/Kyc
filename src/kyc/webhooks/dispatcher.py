"""Delivers pending webhooks with retries.

Deliveries are claimed in a short transaction (row locks with SKIP LOCKED, plus a lease
that pushes `next_attempt_at` forward), sent with no transaction open, and then
recorded in a second transaction. A crash between the two leaves the row to be retried
after the lease. Delivery is therefore at-least-once; receivers deduplicate on the
event ID.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from kyc import __version__
from kyc.core.crypto import FieldCipher
from kyc.db.models import WebhookDelivery, WebhookEndpoint
from kyc.db.session import set_tenant
from kyc.webhooks.delivery import SendResult
from kyc.webhooks.signing import SIGNATURE_HEADER, sign

log = logging.getLogger(__name__)

# Delay after the n-th failed attempt; with the default 8 attempts the last try is about 11 h after the first.
BACKOFF_SECONDS = (30, 120, 600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600)
LEASE = timedelta(minutes=5)
MAX_DRAIN_ROUNDS = 20


def secret_context(organization_id: UUID, endpoint_id: UUID) -> str:
    return f"webhook-secret/{organization_id}/{endpoint_id}"


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def endpoint_secrets(cipher: FieldCipher, endpoint: WebhookEndpoint, now: datetime) -> list[str]:
    """The current secret, plus the previous one while a rotation's overlap lasts."""
    context = secret_context(endpoint.organization_id, endpoint.id)
    found = [cipher.open(endpoint.secret_ciphertext, endpoint.key_version, context)]
    if endpoint.previous_secret_ciphertext is not None and endpoint.previous_secret_expires_at is not None \
            and _aware(endpoint.previous_secret_expires_at) > now:
        found.append(cipher.open(endpoint.previous_secret_ciphertext, endpoint.previous_key_version, context))
    return found


def serialize(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


@dataclass(frozen=True)
class Claim:
    delivery_id: UUID
    endpoint_id: UUID
    url: str
    body: bytes
    headers: dict[str, str]


class WebhookDispatcher:
    def __init__(self, factory: sessionmaker, cipher: FieldCipher | None, sender, max_attempts: int = 8,
                 background: bool = True, batch_size: int = 25):
        self.factory = factory
        self.cipher = cipher
        self.sender = sender
        self.max_attempts = max_attempts
        self.batch_size = batch_size
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kyc-webhooks") if background else None

    def notify(self, organizations) -> None:
        """Called after a commit that created deliveries; in worker mode the worker picks them up instead."""
        if self.executor is not None:
            for organization_id in organizations:
                self.executor.submit(self.drain, organization_id)

    def drain(self, organization_id: UUID) -> int:
        total = 0
        try:
            for _ in range(MAX_DRAIN_ROUNDS):
                sent = self.deliver_due(organization_id)
                total += sent
                if sent < self.batch_size:
                    break
        except Exception as error:  # noqa: BLE001 - the worker retries; never break the request path
            log.warning("Webhook delivery round failed: %s", type(error).__name__)
        return total

    def deliver_due(self, organization_id: UUID, now: datetime | None = None) -> int:
        if self.cipher is None:
            return 0
        claims = self._claim(organization_id, now or datetime.now(timezone.utc))
        for claim in claims:
            result = self.sender.send(claim.url, claim.body, claim.headers)
            self._record(organization_id, claim, result)
        return len(claims)

    def _claim(self, organization_id: UUID, now: datetime) -> list[Claim]:
        claims = []
        with self.factory() as db, db.begin():
            set_tenant(db, organization_id)
            rows = db.scalars(sa.select(WebhookDelivery).where(
                WebhookDelivery.organization_id == organization_id, WebhookDelivery.status == "PENDING",
                WebhookDelivery.next_attempt_at <= now).order_by(WebhookDelivery.next_attempt_at)
                .limit(self.batch_size).with_for_update(skip_locked=True)).all()
            for row in rows:
                endpoint = db.scalar(sa.select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id,
                                                                      WebhookEndpoint.id == row.endpoint_id))
                if endpoint is None or not endpoint.active:
                    row.status, row.last_error = "ABANDONED", "ENDPOINT_DISABLED"
                    continue
                row.attempts += 1
                row.last_attempt_at = now
                row.next_attempt_at = now + LEASE
                body = serialize(row.payload)
                headers = {"Content-Type": "application/json", "User-Agent": f"KYC-Webhooks/{__version__}",
                           "KYC-Event-ID": str(row.event_id), "KYC-Event-Type": row.event_type,
                           "KYC-Delivery-ID": str(row.id), "KYC-Delivery-Attempt": str(row.attempts),
                           SIGNATURE_HEADER: sign(endpoint_secrets(self.cipher, endpoint, now), body)}
                claims.append(Claim(row.id, endpoint.id, endpoint.url, body, headers))
        return claims

    def _record(self, organization_id: UUID, claim: Claim, result: SendResult) -> None:
        now = datetime.now(timezone.utc)
        with self.factory() as db, db.begin():
            set_tenant(db, organization_id)
            row = db.scalar(sa.select(WebhookDelivery).where(WebhookDelivery.organization_id == organization_id,
                                                             WebhookDelivery.id == claim.delivery_id).with_for_update())
            endpoint = db.scalar(sa.select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id,
                                                                  WebhookEndpoint.id == claim.endpoint_id))
            if row is None or row.status != "PENDING":
                return
            row.last_status_code, row.last_error = result.status_code, result.error
            if result.delivered:
                row.status, row.delivered_at = "DELIVERED", now
                if endpoint is not None:
                    endpoint.consecutive_failures = 0
                return
            if endpoint is not None:
                endpoint.consecutive_failures += 1
            if row.attempts >= self.max_attempts:
                row.status = "ABANDONED"
            else:
                row.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[min(row.attempts, len(BACKOFF_SECONDS)) - 1])

    def close(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
