"""Webhook endpoints (spec §27). Secrets are shown once, at creation and rotation."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from kyc.api.dependencies import Database, WebhookManager
from kyc.services import webhooks as service
from kyc.webhooks.events import EVENT_TYPES

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
URL = Annotated[str, Field(min_length=8, max_length=2048)]


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: URL
    event_types: list[str] = Field(default_factory=list, max_length=len(EVENT_TYPES),
                                   description="Empty means every kyc.* event.")
    description: str | None = Field(default=None, max_length=200)


class EndpointUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: URL | None = None
    event_types: list[str] | None = Field(default=None, max_length=len(EVENT_TYPES))
    description: str | None = Field(default=None, max_length=200)


class EndpointView(BaseModel):
    id: UUID
    url: str
    description: str | None
    event_types: list[str]
    active: bool
    created_at: datetime
    disabled_at: datetime | None
    consecutive_failures: int
    previous_secret_expires_at: datetime | None


class EndpointWithSecret(EndpointView):
    secret: str = Field(description="Shown once. Verify the KYC-Signature header with it.")


class DeliveryView(BaseModel):
    id: UUID
    event_id: UUID
    event_type: str
    session_id: UUID | None
    status: str
    attempts: int
    next_attempt_at: datetime
    last_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime


@router.get("/event-types")
def event_types(tenant: WebhookManager):
    return {"event_types": EVENT_TYPES}


@router.post("", response_model=EndpointWithSecret, status_code=status.HTTP_201_CREATED)
def create(body: EndpointCreate, request: Request, tenant: WebhookManager, db: Database):
    """Register an https endpoint. Its host must resolve only to public addresses."""
    state = request.app.state
    row, secret = service.create_endpoint(db, tenant, body.url, body.event_types, body.description, state.webhook_cipher,
                                          state.settings.webhook_allow_private_targets, request.state.request_id)
    return service.endpoint_view(row) | {"secret": secret}


@router.get("", response_model=list[EndpointView])
def list_endpoints(tenant: WebhookManager, db: Database, include_deleted: bool = False):
    return [service.endpoint_view(row) for row in service.list_endpoints(db, tenant.organization_id, include_deleted)]


@router.get("/{endpoint_id}", response_model=EndpointView)
def read(endpoint_id: UUID, tenant: WebhookManager, db: Database):
    return service.endpoint_view(service.get_endpoint(db, tenant.organization_id, endpoint_id))


@router.patch("/{endpoint_id}", response_model=EndpointView)
def update(endpoint_id: UUID, body: EndpointUpdate, request: Request, tenant: WebhookManager, db: Database):
    changes = body.model_dump(exclude_unset=True)
    return service.endpoint_view(service.update_endpoint(db, tenant, endpoint_id, changes,
                                                         request.app.state.settings.webhook_allow_private_targets,
                                                         request.state.request_id))


@router.delete("/{endpoint_id}", response_model=EndpointView)
def delete(endpoint_id: UUID, request: Request, tenant: WebhookManager, db: Database):
    return service.endpoint_view(service.delete_endpoint(db, tenant, endpoint_id, request.state.request_id))


@router.post("/{endpoint_id}/rotate-secret", response_model=EndpointWithSecret)
def rotate_secret(endpoint_id: UUID, request: Request, tenant: WebhookManager, db: Database):
    """The old secret keeps signing alongside the new one for WEBHOOK_SECRET_OVERLAP_HOURS."""
    state = request.app.state
    row, secret = service.rotate_secret(db, tenant, endpoint_id, state.webhook_cipher,
                                        state.settings.webhook_secret_overlap_hours, request.state.request_id)
    return service.endpoint_view(row) | {"secret": secret}


@router.post("/{endpoint_id}/test", response_model=DeliveryView, status_code=status.HTTP_202_ACCEPTED)
def send_test(endpoint_id: UUID, request: Request, tenant: WebhookManager, db: Database):
    """Queue a signed `webhook.test` event to this endpoint only."""
    return service.delivery_view(service.send_test(db, tenant, endpoint_id, request.state.request_id))


@router.get("/{endpoint_id}/deliveries", response_model=list[DeliveryView])
def deliveries(endpoint_id: UUID, tenant: WebhookManager, db: Database,
               status_filter: Annotated[Literal["PENDING", "DELIVERED", "ABANDONED"] | None, Query(alias="status")] = None,
               limit: Annotated[int, Query(ge=1, le=100)] = 25):
    return [service.delivery_view(row) for row in service.list_deliveries(db, tenant.organization_id, endpoint_id,
                                                                          status_filter, limit)]


@router.post("/{endpoint_id}/deliveries/{delivery_id}/redeliver", response_model=DeliveryView,
             status_code=status.HTTP_202_ACCEPTED)
def redeliver(endpoint_id: UUID, delivery_id: UUID, request: Request, tenant: WebhookManager, db: Database):
    return service.delivery_view(service.redeliver(db, tenant, endpoint_id, delivery_id, request.state.request_id))
