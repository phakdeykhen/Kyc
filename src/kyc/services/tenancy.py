"""Organization profile and API key management, shared by the API and the admin script."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, AuditLog, Organization
from kyc.tenancy.keys import ALL_SCOPES, API_KEY_PREFIX, display_prefix, new_secret
from kyc.tenancy.network import parse_networks


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def key_status(row: ApiKey, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if row.revoked_at is not None:
        return "REVOKED"
    if row.expires_at is not None and _aware(row.expires_at) <= now:
        return "EXPIRED"
    return "ACTIVE"


def key_view(row: ApiKey) -> dict:
    return {"id": row.id, "name": row.name, "key_prefix": row.key_prefix, "scopes": sorted(row.scopes),
            "rate_limit_per_minute": row.rate_limit_per_minute, "allowed_cidrs": list(row.allowed_cidrs or []),
            "status": key_status(row),
            "created_at": _aware(row.created_at), "created_by": row.created_by, "expires_at": _aware(row.expires_at),
            "revoked_at": _aware(row.revoked_at), "last_used_at": _aware(row.last_used_at)}


def organization_view(organization: Organization) -> dict:
    return {"organization_id": organization.id, "name": organization.name, "active": organization.active,
            "retention": {"pii_retention_days": organization.pii_retention_days,
                          "capture_retention_hours": organization.capture_retention_hours,
                          "template_retention_hours": organization.template_retention_hours}}


def audit(db: Session, organization_id: UUID, actor_id: str, action: str, request_id: UUID, **metadata) -> None:
    db.add(AuditLog(organization_id=organization_id, actor_id=actor_id, action=action, request_id=request_id,
                    reason_codes=[action], event_metadata={key: str(value) for key, value in metadata.items()}))


def create_key(db: Session, organization_id: UUID, name: str, scopes: list[str], actor_id: str, request_id: UUID,
               expires_in_days: int | None = None, rate_limit_per_minute: int = 600,
               allowed_cidrs: list[str] | None = None, *, expires_at: datetime | None = None) -> tuple[ApiKey, str]:
    """Returns the row and the secret, which is never stored and cannot be shown again."""
    unknown = set(scopes) - ALL_SCOPES
    if unknown or not scopes:
        raise HTTPException(422, detail="Choose at least one supported API key scope.")
    if expires_in_days is not None and not 1 <= expires_in_days <= 730:
        raise HTTPException(422, detail="expires_in_days must be between 1 and 730.")
    if not 1 <= rate_limit_per_minute <= 100_000:
        raise HTTPException(422, detail="rate_limit_per_minute must be between 1 and 100000.")
    try:
        networks = parse_networks(allowed_cidrs)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None
    token, token_hash = new_secret(API_KEY_PREFIX)
    now = datetime.now(timezone.utc)
    expiry = _aware(expires_at) if expires_at is not None else now + timedelta(days=expires_in_days) if expires_in_days is not None else None
    if expiry is not None and expiry <= now:
        raise HTTPException(422, detail="API key expiration must be in the future.")
    row = ApiKey(organization_id=organization_id, name=name, key_prefix=display_prefix(token), key_sha256=token_hash,
                 scopes=sorted(set(scopes)), rate_limit_per_minute=rate_limit_per_minute, created_by=actor_id,
                 allowed_cidrs=networks,
                 created_at=now, expires_at=expiry)
    db.add(row)
    db.flush()
    audit(db, organization_id, actor_id, "API_KEY_CREATED", request_id, key_id=row.id, scopes=",".join(row.scopes),
          allowed_cidrs=",".join(networks) or "any")
    db.flush()
    return row, token


def list_keys(db: Session, organization_id: UUID) -> list[ApiKey]:
    return list(db.scalars(sa.select(ApiKey).where(ApiKey.organization_id == organization_id)
                           .order_by(ApiKey.created_at.desc())))


def revoke_key(db: Session, organization_id: UUID, key_id: UUID, actor_id: str, request_id: UUID) -> ApiKey:
    row = db.scalar(sa.select(ApiKey).where(ApiKey.organization_id == organization_id, ApiKey.id == key_id).with_for_update())
    if row is None:
        raise HTTPException(404, detail="API key not found.")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        audit(db, organization_id, actor_id, "API_KEY_REVOKED", request_id, key_id=row.id)
        db.flush()
    return row
