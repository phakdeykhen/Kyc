"""API key format, scopes, issuing, revocation and rotation.

A key looks like ``kyc_<16 hex>_<43 url-safe chars>``. The ``kyc_<16 hex>`` prefix is public:
it is stored, listed and logged so a key can be identified. The whole key carries 256 random
bits after the prefix and is never stored; only its SHA-256 is. Keys belong to one
organization, and row-level security means a key can only be looked up inside that
organization's context, so the X-Organization-ID header is part of every credential.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, AuditLog, Organization

KEY_PATTERN = re.compile(r"^(kyc_[0-9a-f]{16})_([A-Za-z0-9_-]{43})$")

SCOPES = {
    "sessions:create": "Create KYC sessions.",
    "sessions:read": "Read session status (no identity data).",
    "captures:write": "Upload document photos, selfies, liveness frames and chip data.",
    "sessions:verify": "Run the risk decision for a session.",
    "results:read": "Read results, including identity fields.",
    "keys:manage": "List, issue and revoke this organization's API keys.",
}
# A backend integration's usual set. keys:manage is never granted unless asked for.
DEFAULT_SCOPES = ("sessions:create", "sessions:read", "captures:write", "sessions:verify", "results:read")
ALL_SCOPES = frozenset(SCOPES)
MAX_KEY_LIFETIME_DAYS = 730


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    # SQLite's test driver drops timezone metadata; PostgreSQL uses TIMESTAMPTZ.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def secret_hash(key: str) -> str:
    # 256 random bits are not guessable, so a fast hash is enough (unlike a password).
    return hashlib.sha256(key.encode()).hexdigest()


def parse_key(key: str) -> str | None:
    """The public prefix of a well-formed key, else None."""
    match = KEY_PATTERN.match(key)
    return match.group(1) if match else None


def normalize_scopes(scopes) -> list[str]:
    values = sorted(set(scopes))
    unknown = [value for value in values if value not in SCOPES]
    if unknown or not values:
        raise ValueError(f"Unknown or empty scopes: {', '.join(unknown) or 'none'}.")
    return values


def is_usable(record: ApiKey, at: datetime | None = None) -> bool:
    at = at or now()
    expires = aware(record.expires_at)
    return record.revoked_at is None and (expires is None or expires > at)


@dataclass(frozen=True)
class IssuedKey:
    record: ApiKey
    key: str     # shown once; never stored


def issue_key(db: Session, organization_id: UUID, name: str, scopes, created_by: str, request_id: UUID,
              expires_at: datetime | None = None) -> IssuedKey:
    """Create a key. The caller sets the tenant context and owns the transaction."""
    if db.get(Organization, organization_id) is None:
        raise LookupError("Organization not found.")
    name = name.strip()[:120]
    if not name:
        raise ValueError("A key name is required.")
    if expires_at is not None:
        expires_at = aware(expires_at)
        if expires_at <= now() or expires_at > now() + timedelta(days=MAX_KEY_LIFETIME_DAYS):
            raise ValueError(f"Expiry must be in the future and within {MAX_KEY_LIFETIME_DAYS} days.")
    prefix = f"kyc_{secrets.token_hex(8)}"
    key = f"{prefix}_{secrets.token_urlsafe(32)}"
    record = ApiKey(organization_id=organization_id, name=name, key_prefix=prefix, secret_sha256=secret_hash(key),
                    scopes=normalize_scopes(scopes), expires_at=expires_at, created_by=created_by[:128])
    db.add(record)
    db.flush()
    db.add(AuditLog(organization_id=organization_id, actor_id=created_by[:128], action="API_KEY_CREATED",
                    request_id=request_id, reason_codes=record.scopes,
                    event_metadata={"api_key_id": str(record.id), "key_prefix": prefix,
                                    "expires_at": expires_at.isoformat() if expires_at else None}))
    db.flush()
    return IssuedKey(record, key)


def find_key(db: Session, organization_id: UUID, key_id: UUID) -> ApiKey | None:
    return db.scalar(sa.select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == organization_id))


def revoke_key(db: Session, record: ApiKey, actor: str, request_id: UUID) -> ApiKey:
    if record.revoked_at is None:
        record.revoked_at = now()
        db.add(AuditLog(organization_id=record.organization_id, actor_id=actor[:128], action="API_KEY_REVOKED",
                        request_id=request_id, reason_codes=["API_KEY_REVOKED"],
                        event_metadata={"api_key_id": str(record.id), "key_prefix": record.key_prefix}))
        db.flush()
    return record


def rotate_key(db: Session, record: ApiKey, actor: str, request_id: UUID, grace: timedelta) -> IssuedKey:
    """Issue a replacement with the same name and scopes; the old key keeps working for ``grace`` only."""
    if not is_usable(record):
        raise ValueError("Only an active key can be rotated.")
    replacement = issue_key(db, record.organization_id, record.name, record.scopes, actor, request_id,
                            aware(record.expires_at))
    cutoff = now() + grace
    if grace <= timedelta(0):
        revoke_key(db, record, actor, request_id)
    elif record.expires_at is None or aware(record.expires_at) > cutoff:
        record.expires_at = cutoff
    db.add(AuditLog(organization_id=record.organization_id, actor_id=actor[:128], action="API_KEY_ROTATED",
                    request_id=request_id, reason_codes=["API_KEY_ROTATED"],
                    event_metadata={"api_key_id": str(record.id), "replacement_id": str(replacement.record.id),
                                    "old_key_valid_until": cutoff.isoformat()}))
    db.flush()
    return replacement
