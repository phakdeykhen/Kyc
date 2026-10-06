"""Tenant authentication for the client API (spec §26).

Every request names its organization in `X-Organization-ID` and proves it with one of:

* a provisioned API key (`X-API-Key: kyc_…`), looked up by hash inside that organization's
  row-level-security context, so a key only ever works for its own organization;
* a session client token (`Authorization: Bearer kst_…`) that works only on the session
  in the request path, for capture and status;
* the optional development key from settings, with every scope, in development and tests.

Routes then require scopes with `require(...)`. Reviewer tokens are not accepted here.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, KYCSession, Organization
from kyc.db.session import set_tenant, tenant_transaction
from kyc.tenancy.keys import ALL_SCOPES, API_KEY_PREFIX, CLIENT_SCOPE, CLIENT_TOKEN_PREFIX, MAX_CREDENTIAL_LENGTH, digest
from kyc.tenancy.network import address_allowed

# last_used_at is a coarse signal; refreshing it at most once a minute avoids a write per request.
LAST_USED_RESOLUTION = timedelta(seconds=60)


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_id: str = "development-partner"
    # Internal actors (workers, reviewers) and the development key hold every scope.
    scopes: frozenset[str] = ALL_SCOPES
    credential_id: UUID | None = None   # the API key, when one was used
    session_id: UUID | None = None      # set only for a session client token
    rate_limit: int | None = None
    allowed_networks: tuple[str, ...] = ()  # the API key's network allow-list (Phase 17)
    expires_at: datetime | None = None     # child keys cannot outlive their creator

    @property
    def credential_type(self) -> str:
        if self.session_id is not None:
            return "session_client_token"
        return "api_key" if self.credential_id is not None else "development_key"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def refuse(request: Request, status: int, reason: str, detail: str, headers: dict | None = None) -> HTTPException:
    """An HTTP error whose reason code the request middleware logs as a security event (Phase 17)."""
    request.state.security_reason = reason
    return HTTPException(status, detail=detail, headers=headers)


def _active_organization(request: Request, db: Session, organization_id: UUID) -> None:
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise refuse(request, 401, "ORGANIZATION_UNKNOWN", "Valid credentials are required.")
    if not organization.active:
        raise refuse(request, 403, "ORGANIZATION_SUSPENDED", "This organization is suspended.")


def _development_key(request: Request, settings, api_key: str, organization_id: UUID) -> TenantContext:
    configured = settings.development_api_key
    if settings.environment == "production" or configured is None or not secrets.compare_digest(api_key.encode(), configured.get_secret_value().encode()):
        raise refuse(request, 401, "CREDENTIAL_INVALID", "Valid credentials are required.")
    if organization_id != settings.development_organization_id:
        raise refuse(request, 403, "CREDENTIAL_WRONG_ORGANIZATION", "The credential is not authorized for this organization.")
    return TenantContext(organization_id, rate_limit=settings.api_rate_limit_per_minute)


def _provisioned_key(request: Request, api_key: str, organization_id: UUID) -> TenantContext:
    now = datetime.now(timezone.utc)
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, organization_id)  # RLS: a key can only be found inside its own organization
        row = db.scalar(sa.select(ApiKey).where(ApiKey.organization_id == organization_id,
                                                ApiKey.key_sha256 == digest(api_key)))
        if row is None:
            raise refuse(request, 401, "CREDENTIAL_INVALID", "Valid credentials are required.")
        if row.revoked_at is not None or (row.expires_at is not None and _aware(row.expires_at) <= now):
            raise refuse(request, 401, "API_KEY_REVOKED_OR_EXPIRED", "Valid credentials are required.")
        _active_organization(request, db, organization_id)
        networks = tuple(row.allowed_cidrs or ())
        if not address_allowed(request.client.host if request.client else None, networks):
            raise refuse(request, 403, "API_KEY_NETWORK_DENIED", "This API key is not allowed from this network.")
        if row.last_used_at is None or _aware(row.last_used_at) <= now - LAST_USED_RESOLUTION:
            row.last_used_at = now
        return TenantContext(organization_id, actor_id=f"api_key:{row.id}", scopes=frozenset(row.scopes) & ALL_SCOPES,
                             credential_id=row.id, rate_limit=row.rate_limit_per_minute, allowed_networks=networks,
                             expires_at=_aware(row.expires_at) if row.expires_at else None)


def _path_session(request: Request) -> UUID | None:
    try:
        return UUID(request.path_params.get("session_id", ""))
    except ValueError:
        return None


def _client_token(request: Request, token: str, organization_id: UUID) -> TenantContext:
    session_id = _path_session(request)
    if session_id is None:
        raise refuse(request, 403, "CLIENT_TOKEN_OUT_OF_SCOPE", "A session client token only works on its own session's endpoints.")
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, organization_id)
        row = db.execute(sa.select(KYCSession.client_token_sha256, KYCSession.expires_at).where(
            KYCSession.id == session_id, KYCSession.organization_id == organization_id)).first()
        if row is None or row.client_token_sha256 is None or not secrets.compare_digest(row.client_token_sha256, digest(token)) \
                or _aware(row.expires_at) <= datetime.now(timezone.utc):
            raise refuse(request, 401, "CLIENT_TOKEN_INVALID", "Valid credentials are required.")
        _active_organization(request, db, organization_id)
    return TenantContext(organization_id, actor_id=f"session_client:{session_id}", scopes=frozenset({CLIENT_SCOPE}),
                         session_id=session_id, rate_limit=request.app.state.settings.client_token_rate_limit_per_minute)


def authenticate_tenant(
    request: Request,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
    organization: Annotated[str | None, Header(alias="X-Organization-ID")] = None,
) -> TenantContext:
    bearer = (authorization or "").removeprefix("Bearer ").strip()
    credential = api_key or (bearer if bearer.startswith(CLIENT_TOKEN_PREFIX) else "")
    if not credential or len(credential) > MAX_CREDENTIAL_LENGTH:
        raise refuse(request, 401, "CREDENTIAL_MISSING", "Valid credentials are required.")
    try:
        organization_id = UUID(organization or "")
    except ValueError:
        raise refuse(request, 400, "ORGANIZATION_HEADER_INVALID", "A valid X-Organization-ID is required.") from None
    state = request.app.state
    if not api_key:
        tenant = _client_token(request, credential, organization_id)
    elif api_key.startswith(API_KEY_PREFIX):
        tenant = _provisioned_key(request, api_key, organization_id)
    else:
        tenant = _development_key(request, state.settings, api_key, organization_id)
    bucket = str(tenant.session_id or tenant.credential_id or "development")
    wait = state.rate_limiter.hit(bucket, tenant.rate_limit or state.settings.api_rate_limit_per_minute)
    if wait is not None:
        raise refuse(request, 429, "RATE_LIMITED", "Rate limit exceeded for this credential.",
                     {"Retry-After": str(math.ceil(wait))})
    return tenant


Tenant = Annotated[TenantContext, Depends(authenticate_tenant)]


def require(*accepted: str):
    """A route dependency that admits credentials holding any of the accepted scopes.

    A session client token is admitted only where CLIENT_SCOPE is accepted, and only for
    the session named in the path.
    """
    def check(request: Request, tenant: Tenant) -> TenantContext:
        if tenant.session_id is not None:
            if CLIENT_SCOPE in accepted and _path_session(request) == tenant.session_id:
                return tenant
            raise refuse(request, 403, "CLIENT_TOKEN_OUT_OF_SCOPE", "A session client token cannot call this endpoint.")
        if not tenant.scopes.intersection(accepted):
            raise refuse(request, 403, "SCOPE_MISSING", f"This credential lacks the required scope: {' or '.join(accepted)}.")
        return tenant
    return Annotated[TenantContext, Depends(check)]


SessionWriter = require("sessions:write")
SessionReader = require("sessions:read")
Capture = require("sessions:write", CLIENT_SCOPE)
StatusReader = require("sessions:read", CLIENT_SCOPE)
AnyApiKey = require(*ALL_SCOPES)
KeyManager = require("keys:manage")
WebhookManager = require("webhooks:manage")
Eraser = require("data:erase")


def get_db(request: Request, tenant: Tenant) -> Iterator[Session]:
    yield from tenant_transaction(request.app.state.session_factory, tenant.organization_id)


# Commit before sending the response or running its background work. A failed commit
# must still be able to return an error instead of exposing a successful transaction.
Database = Annotated[Session, Depends(get_db, scope="function")]
