"""Version 1 backend integration and tenant administration API."""
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
import sqlalchemy as sa

from kyc.api.dependencies import Database, Tenant, require, SessionReader, SessionWriter, Eraser, StatusReader
from kyc.api.webhook_routes import EndpointCreate, EndpointWithSecret
from kyc.db.models import (ApiKey, Application, ExternalSubject, IntegrationUsage, KYCSession, RegisteredDevice,
                          VerificationBinding, VerificationDecision, WebhookDelivery)
from kyc.services import tenancy, webhooks
from kyc.services.erasure import erase_session, delete_objects
from kyc.services.sessions import aware
from kyc.tenancy.keys import new_secret
from . import service
from .schemas import (ApplicationCreate, CredentialCreate, DeviceCreate, VerificationCreate, VerificationCreated,
                      VerificationResult, VerificationStatus, Outcome, Strict)

router = APIRouter(prefix="/v1", tags=["integrations"])
Admin = require("applications:manage")
WebhookAdmin = require("webhooks:manage")
DeviceAdmin = require("devices:manage")
UsageReader = require("usage:read")


def app_view(row):
    return {"id": row.id, "tenant_id": row.organization_id, "name": row.name, "mode": row.mode, "active": row.active,
            "workflows": row.workflows, "return_urls": row.return_urls, "branding": row.branding,
            "identity_fields": row.identity_fields, "check_fields": row.check_fields, "populations": row.populations,
            "purposes": row.purposes, "profile_fields": row.profile_fields,
            "daily_session_quota": row.daily_session_quota, "rate_limit_per_minute": row.rate_limit_per_minute}


@router.post("/applications", status_code=201)
def create_application(body: ApplicationCreate, request: Request, tenant: Admin, db: Database):
    for url in body.return_urls:
        service.destination(url)
    row = Application(organization_id=tenant.organization_id, **body.model_dump(mode="json"))
    db.add(row)
    db.flush()
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "APPLICATION_CREATED", request.state.request_id, application_id=row.id)
    return app_view(row)


@router.get("/applications")
def applications(tenant: Admin, db: Database):
    return [app_view(row) for row in db.scalars(sa.select(Application).where(Application.organization_id == tenant.organization_id))]


@router.put("/applications/{application_id}")
def configure_application(application_id: UUID, body: ApplicationCreate, request: Request, tenant: Admin, db: Database):
    row = service.application(db, tenant, application_id, lock=True)
    if row.mode != body.mode:
        raise HTTPException(409, detail="Create another application to change sandbox/production mode.")
    for url in body.return_urls:
        service.destination(url)
    for key, value in body.model_dump(mode="json").items():
        setattr(row, key, value)
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "APPLICATION_CONFIGURED", request.state.request_id, application_id=row.id)
    return app_view(row)


@router.delete("/applications/{application_id}")
def disable_application(application_id: UUID, request: Request, tenant: Admin, db: Database):
    row = service.application(db, tenant, application_id, lock=True)
    row.active = False
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "APPLICATION_DISABLED", request.state.request_id, application_id=row.id)
    return app_view(row)


@router.post("/applications/{application_id}/credentials", status_code=201)
def credential(application_id: UUID, body: CredentialCreate, request: Request, tenant: Admin, db: Database):
    app = service.application(db, tenant, application_id)
    if not app.active:
        raise HTTPException(409, detail="Application disabled.")
    if set(body.scopes) - service.APPLICATION_SCOPES or set(body.scopes) - tenant.scopes:
        raise HTTPException(403, detail="Application scopes cannot exceed the grantor's scopes or include tenant administration.")
    limit = min(app.rate_limit_per_minute, tenant.rate_limit or 600)
    if body.rate_limit_per_minute > limit:
        raise HTTPException(422, detail="Credential rate limit exceeds application or grantor limit.")
    expiry = service.utcnow() + timedelta(days=body.expires_in_days)
    if tenant.expires_at:
        expiry = min(expiry, tenant.expires_at)
    row, token = tenancy.create_key(db, tenant.organization_id, body.name, body.scopes, tenant.actor_id, request.state.request_id,
        rate_limit_per_minute=body.rate_limit_per_minute, allowed_cidrs=list(tenant.allowed_networks), expires_at=expiry,
        application_id=app.id)
    return tenancy.key_view(row) | {"application_id": app.id, "api_key": token}


@router.post("/applications/{application_id}/webhooks", response_model=EndpointWithSecret, status_code=201)
def webhook(application_id: UUID, body: EndpointCreate, request: Request, tenant: WebhookAdmin, db: Database):
    app = service.application(db, tenant, application_id)
    service.destination(body.url)
    if set(body.event_types) - {service.RESULT_EVENT}:
        raise HTTPException(422, detail="Application webhooks support verification.result.updated.")
    row, secret = webhooks.create_endpoint(db, tenant, body.url, [service.RESULT_EVENT], body.description,
        request.app.state.webhook_cipher, False, request.state.request_id, application_id=app.id)
    return webhooks.endpoint_view(row) | {"secret": secret}


@router.get("/applications/{application_id}/credentials")
def credentials(application_id: UUID, tenant: Admin, db: Database):
    app = service.application(db, tenant, application_id)
    return [tenancy.key_view(row) for row in db.scalars(sa.select(ApiKey).where(ApiKey.organization_id == tenant.organization_id,
        ApiKey.application_id == app.id))]


@router.delete("/applications/{application_id}/credentials/{credential_id}")
def revoke_credential(application_id: UUID, credential_id: UUID, request: Request, tenant: Admin, db: Database):
    app = service.application(db, tenant, application_id)
    row = db.scalar(sa.select(ApiKey).where(ApiKey.organization_id == tenant.organization_id, ApiKey.id == credential_id,
                                          ApiKey.application_id == app.id))
    if row is None:
        raise HTTPException(404, detail="Credential not found.")
    return tenancy.key_view(tenancy.revoke_key(db, tenant.organization_id, row.id, tenant.actor_id, request.state.request_id))


@router.post("/verification-sessions", response_model=VerificationCreated, status_code=201)
def create(body: VerificationCreate, request: Request, response: Response, tenant: SessionWriter, db: Database,
    idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")] = None):
    result, replay = service.create_verification(db, tenant, body, request.app.state.settings, request.state.request_id, idempotency_key)
    if replay:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return result


@router.get("/verification-sessions/{session_id}", response_model=VerificationStatus)
def status(session_id: UUID, request: Request, tenant: SessionReader, db: Database):
    row, record = service.binding(db, tenant, session_id, request.state.request_id)
    result = service.read_result(db, tenant, row, record, None)
    external = db.get(ExternalSubject, row.subject_id)
    return {"session_id": session_id, "external_subject_id": None if external.erased_at or record.erased_at else external.external_id,
        "reference": row.reference, "workflow_id": row.workflow_id, "status": "TECHNICAL_ERROR" if row.technical_error else record.status.value,
        "expires_at": aware(record.expires_at), "result_version": result["result_version"], "sandbox": row.mode == "sandbox"}


@router.get("/verification-sessions/{session_id}/result", response_model=VerificationResult)
def result(session_id: UUID, request: Request, tenant: SessionReader, db: Database):
    row, record = service.binding(db, tenant, session_id, request.state.request_id)
    return service.read_result(db, tenant, row, record, request.app.state.field_cipher)


@router.get("/verification-sessions/{session_id}/capture-context")
def capture_context(session_id: UUID, request: Request, tenant: StatusReader, db: Database):
    if tenant.session_id != session_id:
        raise HTTPException(403, detail="A capture token is required.")
    row, record = service.binding(db, tenant, session_id, request.state.request_id)
    app = service.application(db, tenant, row.application_id)
    from kyc.documents.requirements import requirement_for
    return {"session_id": str(session_id), "branding": app.branding, "locale": row.locale, "sandbox": row.mode == "sandbox",
            "return_url": row.return_url if row.return_url in app.return_urls else None,
            "document_consent_policy_version": request.app.state.settings.document_consent_policy_version,
            "expected_document_type": record.expected_document_type.value,
            "required_sides": list(requirement_for(record.expected_document_type).sides),
            "verification_level": record.verification_level.value}


@router.get("/verification-sessions/{session_id}/capture-status")
def capture_status(session_id: UUID, request: Request, tenant: StatusReader, db: Database):
    if tenant.session_id != session_id:
        raise HTTPException(403, detail="A capture token is required.")
    row, record = service.binding(db, tenant, session_id, request.state.request_id)
    return {"session_id": str(session_id), "status": "TECHNICAL_ERROR" if row.technical_error else record.status.value,
            "expected_document_type": record.expected_document_type.value, "expires_at": aware(record.expires_at)}


class Simulation(Strict):
    outcome: Outcome


@router.post("/verification-sessions/{session_id}/sandbox-result", response_model=VerificationResult)
def simulate(session_id: UUID, body: Simulation, request: Request, tenant: SessionWriter, db: Database):
    row, record = service.binding(db, tenant, session_id, request.state.request_id)
    if row.mode != "sandbox":
        raise HTTPException(403, detail="Synthetic results are only permitted in sandbox applications.")
    if record.status.value in service.FINAL or row.technical_error or record.erased_at:
        raise HTTPException(409, detail="Session already decided.")
    # Deliberately separate from real verification; no engine or model is credited with this simulation.
    from kyc.domain.enums import SessionStatus
    if body.outcome == "TECHNICAL_ERROR":
        row.technical_error = True
    else:
        record.status = SessionStatus(body.outcome)
    record.version += 1
    service.snapshot(db, record, body.outcome)
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "SANDBOX_SIMULATED_RESULT", request.state.request_id, session_id=session_id)
    return service.read_result(db, tenant, row, record, None)


@router.delete("/verification-sessions/{session_id}")
def erase(session_id: UUID, request: Request, tenant: Eraser, db: Database, background: BackgroundTasks):
    _, record = service.binding(db, tenant, session_id, request.state.request_id)
    report = erase_session(db, tenant, record, request.state.request_id, face_ledger=request.app.state.face_identity.ledger)
    db.commit()
    if report.object_refs and request.app.state.capture_store:
        background.add_task(delete_objects, request.app.state.capture_store, report)
    return report.view(record.status.value)


@router.post("/applications/{application_id}/devices", status_code=201)
def register_device(application_id: UUID, body: DeviceCreate, request: Request, tenant: DeviceAdmin, db: Database):
    app = service.application(db, tenant, application_id)
    if set(body.populations) - set(app.populations) or set(body.purposes) - set(app.purposes):
        raise HTTPException(403, detail="Device population/purpose exceeds application permissions.")
    token, token_hash = new_secret("kdev_")
    row = RegisteredDevice(organization_id=tenant.organization_id, application_id=app.id, name=body.name,
        populations=body.populations, purposes=body.purposes, token_hash=token_hash,
        expires_at=service.utcnow() + timedelta(days=body.expires_in_days))
    db.add(row)
    db.flush()
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "DEVICE_REGISTERED", request.state.request_id, device_id=row.id)
    return {"device_id": row.id, "device_token": token, "expires_at": row.expires_at}


@router.delete("/applications/{application_id}/devices/{device_id}")
def revoke_device(application_id: UUID, device_id: UUID, request: Request, tenant: DeviceAdmin, db: Database):
    app = service.application(db, tenant, application_id)
    row = db.scalar(sa.select(RegisteredDevice).where(RegisteredDevice.organization_id == tenant.organization_id,
        RegisteredDevice.application_id == app.id, RegisteredDevice.id == device_id).with_for_update())
    if row is None:
        raise HTTPException(404, detail="Device not found.")
    row.revoked_at = service.utcnow()
    tenancy.audit(db, tenant.organization_id, tenant.actor_id, "DEVICE_REVOKED", request.state.request_id, device_id=row.id)
    return {"device_id": row.id, "revoked_at": row.revoked_at}


@router.get("/integration/usage")
def usage(tenant: UsageReader, db: Database):
    sessions = sa.select(VerificationBinding.session_id).where(VerificationBinding.organization_id == tenant.organization_id)
    if tenant.application_id:
        sessions = sessions.where(VerificationBinding.application_id == tenant.application_id)
    count = db.scalar(sa.select(sa.func.count()).select_from(sessions.subquery()))
    deliveries = list(db.scalars(sa.select(WebhookDelivery).where(WebhookDelivery.organization_id == tenant.organization_id,
        WebhookDelivery.session_id.in_(sessions)).order_by(WebhookDelivery.created_at.desc()).limit(100)))
    outcomes = dict(db.execute(sa.select(VerificationDecision.outcome, sa.func.count()).where(
        VerificationDecision.organization_id == tenant.organization_id, VerificationDecision.session_id.in_(sessions))
        .group_by(VerificationDecision.outcome)).all())
    counters = sa.select(IntegrationUsage).where(IntegrationUsage.organization_id == tenant.organization_id,
                                               IntegrationUsage.expires_at > service.utcnow())
    if tenant.application_id:
        counters = counters.where(IntegrationUsage.bucket.like(f"sessions/{tenant.application_id}/%"))
    else:
        counters = counters.where(IntegrationUsage.bucket.like("sessions/%"))
    return {"tenant_id": tenant.organization_id, "application_id": tenant.application_id, "sessions": count,
            "current_usage": [{"bucket": row.bucket, "count": row.count, "resets_at": aware(row.expires_at)} for row in db.scalars(counters)],
            "decision_versions_by_outcome": outcomes, "deliveries": [webhooks.delivery_view(row) for row in deliveries]}


@router.get("/integration/dashboard", response_class=HTMLResponse)
def dashboard(tenant: UsageReader, db: Database):
    """Render behind the client's authenticated admin proxy; secrets are never embedded in this page."""
    from html import escape
    data = usage(tenant, db)
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(row[key]))}</td>" for key in
        ("event_type", "status", "attempts", "last_status_code", "last_error")) + "</tr>" for row in data["deliveries"])
    outcomes = "".join(f"<li>{escape(key)}: {value}</li>" for key, value in data["decision_versions_by_outcome"].items())
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>KYC usage and delivery dashboard</title><main><h1>KYC usage and delivery dashboard</h1>'
        f'<p>Tenant {data["tenant_id"]} · application {data["application_id"] or "all"}</p><p>Sessions: {data["sessions"]}</p>'
        f'<h2>Decision history</h2><ul>{outcomes}</ul><h2>Recent deliveries</h2><table><thead><tr><th>Event</th>'
        f'<th>Status</th><th>Attempts</th><th>HTTP</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table></main></html>')


@router.delete("/integration/subjects/{external_subject_id}")
def erase_subject(external_subject_id: str, request: Request, tenant: Eraser, db: Database, background: BackgroundTasks):
    external = db.scalar(sa.select(ExternalSubject).where(ExternalSubject.organization_id == tenant.organization_id,
        ExternalSubject.external_id == external_subject_id).with_for_update())
    if external is None:
        raise HTTPException(404, detail="Subject not found.")
    query = sa.select(VerificationBinding).where(VerificationBinding.organization_id == tenant.organization_id,
                                                VerificationBinding.subject_id == external.id)
    if tenant.application_id:
        query = query.where(VerificationBinding.application_id == tenant.application_id)
    links = list(db.scalars(query))
    if not links:
        raise HTTPException(404, detail="Subject not found.")
    reports = []
    for link in links:
        record = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == tenant.organization_id,
            KYCSession.id == link.session_id).with_for_update())
        reports.append(erase_session(db, tenant, record, request.state.request_id, face_ledger=request.app.state.face_identity.ledger))
    remaining = db.scalar(sa.select(sa.func.count()).select_from(VerificationBinding).join(KYCSession, sa.and_(
        KYCSession.organization_id == VerificationBinding.organization_id, KYCSession.id == VerificationBinding.session_id)).where(
        VerificationBinding.organization_id == tenant.organization_id, VerificationBinding.subject_id == external.id,
        KYCSession.erased_at.is_(None)))
    if not remaining:
        external.erased_at = service.utcnow()
        external.external_id = "erased/" + str(external.id)
    db.commit()
    if request.app.state.capture_store:
        for report in reports:
            if report.object_refs:
                background.add_task(delete_objects, request.app.state.capture_store, report)
    return {"erased_sessions": len(reports), "subject_erased": not remaining}


@router.get("/integration/capabilities")
def capabilities(request: Request, tenant: SessionReader):
    return {"api_version": "v1", "kyc_production_enabled": request.app.state.settings.integration_kyc_production_enabled,
            "kyc": service.kyc_readiness(request.app.state.settings),
            "face_identification": request.app.state.face_identity.readiness()}
