"""Organization profile and API key management, shared by the API and the admin script."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, AuditLog, Organization
from kyc.tenancy.keys import ALL_SCOPES, API_KEY_PREFIX, display_prefix, new_secret


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
            "rate_limit_per_minute": row.rate_limit_per_minute, "status": key_status(row),
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
               expires_in_days: int | None = None, rate_limit_per_minute: int = 600) -> tuple[ApiKey, str]:
    """Returns the row and the secret, which is never stored and cannot be shown again."""
    unknown = set(scopes) - ALL_SCOPES
    if unknown or not scopes:
        raise HTTPException(422, detail=f"Unknown or missing scopes: {sorted(unknown)}.")
    token, token_hash = new_secret(API_KEY_PREFIX)
    now = datetime.now(timezone.utc)
    row = ApiKey(organization_id=organization_id, name=name, key_prefix=display_prefix(token), key_sha256=token_hash,
                 scopes=sorted(set(scopes)), rate_limit_per_minute=rate_limit_per_minute, created_by=actor_id,
                 created_at=now, expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None)
    db.add(row)
    db.flush()
    audit(db, organization_id, actor_id, "API_KEY_CREATED", request_id, key_id=row.id, scopes=",".join(row.scopes))
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
