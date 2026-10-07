"""Organization profile and self-service API keys (spec §26). Keys never grant more than their creator holds."""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from kyc.api.dependencies import AnyApiKey, Database, KeyManager, refuse
from kyc.db.models import Organization
from kyc.services import tenancy
from kyc.tenancy.keys import SCOPES
from kyc.tenancy.network import networks_within, parse_networks

router = APIRouter(prefix="/v1", tags=["organization"])


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    scopes: Annotated[list[str], Field(min_length=1, max_length=len(SCOPES))]
    expires_in_days: int | None = Field(default=None, ge=1, le=730)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    allowed_cidrs: list[str] | None = Field(default=None, max_length=20, description=
        "Networks the key may be used from (CIDR or address). Omitted: the creator's own allow-list, if any.")


class ApiKeyView(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_per_minute: int
    allowed_cidrs: list[str] = []
    status: str
    created_at: datetime
    created_by: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeyView):
    api_key: str = Field(description="Shown once. Store it in your server's secret manager.")


@router.get("/organization")
def organization(tenant: AnyApiKey, db: Database):
    """The calling organization and the credential in use."""
    row = db.get(Organization, tenant.organization_id)
    if row is None:
        raise HTTPException(409, detail="Organization has not been provisioned.")
    return tenancy.organization_view(row) | {"credential": {
        "type": tenant.credential_type, "key_id": tenant.credential_id, "scopes": sorted(tenant.scopes),
        "rate_limit_per_minute": tenant.rate_limit}, "available_scopes": SCOPES}


@router.get("/api-keys", response_model=list[ApiKeyView])
def list_api_keys(tenant: KeyManager, db: Database):
    return [tenancy.key_view(row) for row in tenancy.list_keys(db, tenant.organization_id)]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(body: ApiKeyCreate, request: Request, tenant: KeyManager, db: Database):
    """Create a key bounded by the caller's scopes, rate limit, networks and expiry."""
    excess = set(body.scopes) - tenant.scopes
    if excess:
        raise refuse(request, 403, "API_KEY_SCOPE_ESCALATION", "A key cannot grant scopes its creator lacks.")
    ceiling = tenant.rate_limit or request.app.state.settings.api_rate_limit_per_minute
    limit = body.rate_limit_per_minute or ceiling
    if limit > ceiling:
        raise refuse(request, 422, "API_KEY_RATE_LIMIT_ESCALATION", f"rate_limit_per_minute cannot exceed your own limit ({ceiling}).")
    networks = list(tenant.allowed_networks) if body.allowed_cidrs is None else body.allowed_cidrs
    try:
        within = networks_within(parse_networks(networks), tenant.allowed_networks)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from None
    if not within or (tenant.allowed_networks and not networks):
        raise refuse(request, 403, "API_KEY_NETWORK_ESCALATION", "A key cannot be usable from networks its creator is not allowed from.")
    expiry = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    if tenant.expires_at is not None:
        if expiry is not None and expiry > tenant.expires_at:
            raise refuse(request, 403, "API_KEY_EXPIRY_ESCALATION", "A key cannot outlive its creator's expiration.")
        expiry = expiry or tenant.expires_at
    row, token = tenancy.create_key(db, tenant.organization_id, body.name, body.scopes, tenant.actor_id,
                                    request.state.request_id, rate_limit_per_minute=limit,
                                    allowed_cidrs=networks, expires_at=expiry)
    return tenancy.key_view(row) | {"api_key": token}


@router.delete("/api-keys/{key_id}", response_model=ApiKeyView)
def revoke_api_key(key_id: UUID, request: Request, tenant: KeyManager, db: Database):
    """Revoke immediately. To rotate: create the new key, deploy it, then revoke the old one."""
    return tenancy.key_view(tenancy.revoke_key(db, tenant.organization_id, key_id, tenant.actor_id,
                                               request.state.request_id))
