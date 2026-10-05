from collections.abc import Iterator
from dataclasses import dataclass
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from kyc.db.session import tenant_transaction


@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_id: str = "development-partner"


def authenticate_tenant(
    request: Request,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    organization: Annotated[str | None, Header(alias="X-Organization-ID")] = None,
) -> TenantContext:
    settings = request.app.state.settings
    if not api_key or not secrets.compare_digest(api_key.encode(), settings.development_api_key.get_secret_value().encode()):
        raise HTTPException(401, detail="Valid credentials are required.")
    try:
        organization_id = UUID(organization or "")
    except ValueError:
        raise HTTPException(400, detail="A valid X-Organization-ID is required.") from None
    if organization_id != settings.development_organization_id:
        raise HTTPException(403, detail="The credential is not authorized for this organization.")
    return TenantContext(organization_id)


Tenant = Annotated[TenantContext, Depends(authenticate_tenant)]


def get_db(request: Request, tenant: Tenant) -> Iterator[Session]:
    yield from tenant_transaction(request.app.state.session_factory, tenant.organization_id)


Database = Annotated[Session, Depends(get_db)]
