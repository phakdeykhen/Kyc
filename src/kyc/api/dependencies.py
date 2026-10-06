"""Client authentication (Phase 15): per-organization API keys with scopes and rate limits.

Every request names its organization (X-Organization-ID) and presents a key issued to that
organization (X-API-Key). The key is looked up inside the organization's row-level-security
context, so a key from another organization is simply not found. The optional
DEVELOPMENT_API_KEY from earlier phases still works for the development organization in
non-production environments only.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, Organization
from kyc.db.session import set_tenant, tenant_transaction
from kyc.tenancy.keys import ALL_SCOPES, aware, is_usable, now, parse_key, secret_hash

DEVELOPMENT_ACTOR = "development-partner"
LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_id: str = DEVELOPMENT_ACTOR
    api_key_id: UUID | None = None
    scopes: frozenset[str] = ALL_SCOPES

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(403, detail=f"This API key does not have the {scope} scope.")


def _rate_limit(request: Request, key: str, limit: int) -> None:
    decision = request.app.state.rate_limiter.hit(key, limit)
    request.state.rate_limit = decision
    if not decision.allowed:
        raise HTTPException(429, detail="Rate limit exceeded for this API key.",
                            headers={"Retry-After": str(decision.retry_after)})


def _organization_key(request: Request, organization_id: UUID, key: str, prefix: str) -> TenantContext:
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, organization_id)
        organization = db.get(Organization, organization_id)
        record = db.scalar(sa.select(ApiKey).where(ApiKey.organization_id == organization_id, ApiKey.key_prefix == prefix))
        # Compare even when nothing matched, so response time does not reveal whether a prefix exists.
        expected = record.secret_sha256 if record is not None else "0" * 64
        matches = secrets.compare_digest(secret_hash(key), expected)
        if not matches or record is None or not is_usable(record):
            raise HTTPException(401, detail="Valid credentials are required.")
        if organization is None or not organization.active:
            raise HTTPException(403, detail="This organization is suspended.")
        current = now()
        if record.last_used_at is None or current - aware(record.last_used_at) >= LAST_USED_RESOLUTION:
            record.last_used_at = current
        context = TenantContext(organization_id, actor_id=f"api_key:{record.id}", api_key_id=record.id,
                                scopes=frozenset(record.scopes) & ALL_SCOPES)
        limit = organization.api_rate_limit_per_minute
    _rate_limit(request, str(context.api_key_id), limit)
    return context


def authenticate_tenant(
    request: Request,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    organization: Annotated[str | None, Header(alias="X-Organization-ID")] = None,
) -> TenantContext:
    if not api_key or len(api_key) > 200:
        raise HTTPException(401, detail="Valid credentials are required.")
    try:
        organization_id = UUID(organization or "")
    except ValueError:
        raise HTTPException(400, detail="A valid X-Organization-ID is required.") from None
    prefix = parse_key(api_key)
    if prefix is not None:
        return _organization_key(request, organization_id, api_key, prefix)
    settings = request.app.state.settings
    development = settings.development_api_key
    if settings.environment == "production" or development is None \
            or not secrets.compare_digest(api_key.encode(), development.get_secret_value().encode()):
        raise HTTPException(401, detail="Valid credentials are required.")
    if organization_id != settings.development_organization_id:
        raise HTTPException(403, detail="The credential is not authorized for this organization.")
    return TenantContext(organization_id)


Tenant = Annotated[TenantContext, Depends(authenticate_tenant)]


def Scope(scope: str):
    """Route dependency: the authenticated key must hold ``scope``."""
    def check(tenant: Tenant) -> None:
        tenant.require(scope)
    return Depends(check)


def get_db(request: Request, tenant: Tenant) -> Iterator[Session]:
    yield from tenant_transaction(request.app.state.session_factory, tenant.organization_id)


Database = Annotated[Session, Depends(get_db)]
