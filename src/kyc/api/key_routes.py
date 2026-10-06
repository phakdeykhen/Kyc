"""Phase 15: the calling credential and an organization's own API keys.

Organizations themselves are provisioned by an operator (scripts/manage_tenants.py). Within
an organization, a key holding ``keys:manage`` can list, issue, rotate and revoke keys. It
cannot grant a scope it does not hold, so key management never escalates privileges.
"""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa

from kyc.api.dependencies import Database, Scope, Tenant
from kyc.db.models import ApiKey, Organization
from kyc.tenancy import keys

router = APIRouter(prefix="/v1", tags=["credentials"])
MANAGE = [Scope("keys:manage")]


class ApiKeySummary(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    created_by: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    active: bool


class IssuedApiKey(ApiKeySummary):
    key: str = Field(description="The full key. Shown once; it cannot be retrieved again.")


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: list(keys.DEFAULT_SCOPES), min_length=1, max_length=len(keys.SCOPES))
    expires_in_days: int | None = Field(default=None, ge=1, le=keys.MAX_KEY_LIFETIME_DAYS)


class ApiKeyRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grace_hours: int = Field(default=24, ge=0, le=24 * 30, description="How long the old key keeps working.")


class CallerResponse(BaseModel):
    organization_id: UUID
    organization_name: str | None
    api_key_id: UUID | None
    actor_id: str
    scopes: list[str]
    rate_limit_per_minute: int | None


def summary(record: ApiKey) -> dict:
    return {"id": record.id, "name": record.name, "key_prefix": record.key_prefix, "scopes": list(record.scopes),
            "created_at": keys.aware(record.created_at), "created_by": record.created_by,
            "expires_at": keys.aware(record.expires_at), "revoked_at": keys.aware(record.revoked_at),
            "last_used_at": keys.aware(record.last_used_at), "active": keys.is_usable(record)}


def issued(result: keys.IssuedKey) -> IssuedApiKey:
    return IssuedApiKey(**summary(result.record), key=result.key)


def _owned(db, tenant, key_id: UUID) -> ApiKey:
    record = keys.find_key(db, tenant.organization_id, key_id)
    if record is None:
        raise HTTPException(404, detail="API key not found.")
    return record


@router.get("/me", response_model=CallerResponse)
def me(tenant: Tenant, db: Database):
    """Who is calling: organization, key and scopes. Useful to check an integration's configuration."""
    organization = db.get(Organization, tenant.organization_id)
    return CallerResponse(organization_id=tenant.organization_id, organization_name=organization.name if organization else None,
                          api_key_id=tenant.api_key_id, actor_id=tenant.actor_id, scopes=sorted(tenant.scopes),
                          rate_limit_per_minute=organization.api_rate_limit_per_minute
                          if organization and tenant.api_key_id else None)


@router.get("/api-keys", response_model=list[ApiKeySummary], dependencies=MANAGE)
def list_keys(tenant: Tenant, db: Database, include_inactive: Annotated[bool, Query()] = False):
    records = db.scalars(sa.select(ApiKey).where(ApiKey.organization_id == tenant.organization_id)
                         .order_by(ApiKey.created_at.desc())).all()
    return [summary(record) for record in records if include_inactive or keys.is_usable(record)]


@router.post("/api-keys", response_model=IssuedApiKey, status_code=status.HTTP_201_CREATED, dependencies=MANAGE)
def create_key(body: ApiKeyCreate, request: Request, tenant: Tenant, db: Database):
    try:
        scopes = keys.normalize_scopes(body.scopes)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None
    if not set(scopes) <= tenant.scopes:
        raise HTTPException(403, detail="A key cannot grant scopes its creator does not hold.")
    expires = keys.now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    return issued(keys.issue_key(db, tenant.organization_id, body.name, scopes, tenant.actor_id,
                                 request.state.request_id, expires))


@router.post("/api-keys/{key_id}/rotate", response_model=IssuedApiKey, status_code=status.HTTP_201_CREATED,
             dependencies=MANAGE)
def rotate_key(key_id: UUID, request: Request, tenant: Tenant, db: Database, body: ApiKeyRotate | None = None):
    """Issue a replacement with the same scopes; the old key stops working after the grace period."""
    record = _owned(db, tenant, key_id)
    if not set(record.scopes) <= tenant.scopes:
        raise HTTPException(403, detail="A key cannot grant scopes its creator does not hold.")
    try:
        return issued(keys.rotate_key(db, record, tenant.actor_id, request.state.request_id,
                                      timedelta(hours=(body or ApiKeyRotate()).grace_hours)))
    except ValueError as error:
        raise HTTPException(409, detail=str(error)) from None


@router.delete("/api-keys/{key_id}", response_model=ApiKeySummary, dependencies=MANAGE)
def revoke_key(key_id: UUID, request: Request, tenant: Tenant, db: Database):
    """Revoke immediately. A key may revoke itself; the organization keeps any other keys."""
    return summary(keys.revoke_key(db, _owned(db, tenant, key_id), tenant.actor_id, request.state.request_id))
