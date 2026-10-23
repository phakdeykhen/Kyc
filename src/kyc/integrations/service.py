"""Tenant/application authorization, quotas, durable results and integration outbox."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import HTTPException
import sqlalchemy as sa

from kyc.api.schemas import SessionCreate
from kyc.db.models import (Application, DocumentField, ExternalSubject, IntegrationUsage, KYCSession,
                          Organization, VerificationBinding, VerificationDecision, WebhookDelivery, WebhookEndpoint)
from kyc.services.results import collect_evidence
from kyc.services.sessions import aware, create_session, get_session, issue_client_token
from kyc.tenancy.keys import new_secret
from kyc.webhooks.delivery import TargetNotAllowed, resolve, check_url
from kyc.webhooks.outbox import envelope, notify_after_commit

FINAL = {"VERIFIED", "MANUAL_REVIEW", "REJECTED", "EXPIRED", "TECHNICAL_ERROR"}
RESULT_EVENT = "verification.result.updated"
APPLICATION_SCOPES = {"sessions:write", "sessions:read", "results:identity", "results:checks", "usage:read",
                      "enrollments:write", "recognition:write", "recognition:read", "data:erase"}


def utcnow():
    return datetime.now(timezone.utc)


def kyc_readiness(settings):
    reasons = []
    if not settings.integration_kyc_production_enabled:
        reasons.append("KYC_RELEASE_GATE_CLOSED")
    if settings.environment != "test":
        if settings.environment != "production":
            reasons.append("PRODUCTION_ENVIRONMENT_REQUIRED")
        if not settings.face_match_calibrated or not settings.face_match_calibration_reference:
            reasons.append("VALIDATED_DOCUMENT_FACE_COMPARISON_REQUIRED")
        if not settings.liveness_calibrated:
            reasons.append("VALIDATED_KYC_LIVENESS_REQUIRED")
        if settings.integration_public_base_url.endswith(".invalid"):
            reasons.append("PUBLIC_ORIGIN_NOT_CONFIGURED")
    return {"ready": not reasons, "reason_codes": reasons, "feature": "kyc-integration",
            "production_ready": not reasons and settings.environment == "production"}


def destination(url):
    # No private destinations, even for sandbox applications. Callback URLs are never fetched.
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise HTTPException(422, detail="Invalid destination.")
    try:
        resolve(url, False)
    except TargetNotAllowed:
        raise HTTPException(422, detail="Destination must be an HTTPS URL resolving only to public addresses.") from None


def application(db, tenant, identifier=None, lock=False):
    identifier = identifier or tenant.application_id
    if identifier is None:
        raise HTTPException(403, detail="An application credential is required.")
    if tenant.application_id is not None and identifier != tenant.application_id:
        raise HTTPException(404, detail="Application not found.")
    query = sa.select(Application).where(Application.organization_id == tenant.organization_id, Application.id == identifier)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail="Application not found.")
    return row


def consume_counter(db, organization_id, bucket, maximum, expires_at):
    query = sa.select(IntegrationUsage).where(IntegrationUsage.organization_id == organization_id, IntegrationUsage.bucket == bucket)
    row = db.scalar(query.with_for_update())
    if row is None:
        try:
            with db.begin_nested():
                row = IntegrationUsage(organization_id=organization_id, bucket=bucket, count=0, expires_at=expires_at)
                db.add(row)
                db.flush()
        except sa.exc.IntegrityError:
            row = db.scalar(query.with_for_update())
    if row.count >= maximum:
        retry = max(1, int((aware(expires_at) - utcnow()).total_seconds()))
        raise HTTPException(429, detail="Application rate limit or tenant session quota exceeded.", headers={"Retry-After": str(retry)})
    row.count += 1
    db.flush()


def consume_session_quota(db, app):
    now = utcnow()
    expiry = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tenant_limit = db.get(Organization, app.organization_id).daily_session_quota
    consume_counter(db, app.organization_id, f"sessions/tenant/{now:%Y%m%d}", tenant_limit, expiry)
    consume_counter(db, app.organization_id, f"sessions/{app.id}/{now:%Y%m%d}", app.daily_session_quota, expiry)


def subject(db, tenant_id, external_id):
    query = sa.select(ExternalSubject).where(ExternalSubject.organization_id == tenant_id, ExternalSubject.external_id == external_id)
    row = db.scalar(query)
    if row is None:
        try:
            with db.begin_nested():
                row = ExternalSubject(organization_id=tenant_id, external_id=external_id)
                db.add(row)
                db.flush()
        except sa.exc.IntegrityError:
            row = db.scalar(query)
    if row.erased_at:
        raise HTTPException(409, detail="This subject was erased.")
    return row


def binding(db, tenant, session_id, request_id, lock=True):
    query = sa.select(VerificationBinding).where(VerificationBinding.organization_id == tenant.organization_id,
                                                VerificationBinding.session_id == session_id)
    if tenant.application_id is not None:
        query = query.where(VerificationBinding.application_id == tenant.application_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail="Verification session not found.")
    return row, get_session(db, tenant, session_id, request_id, lock=lock)


def create_verification(db, tenant, body, settings, request_id, idempotency_key=None):
    app = application(db, tenant, lock=True)  # serializes creation/idempotency and quota within an application
    if not app.active:
        raise HTTPException(403, detail="Application disabled.")
    if app.mode == "production" and not kyc_readiness(settings)["ready"]:
        raise HTTPException(503, detail="KYC production readiness gate is closed.")
    workflow = app.workflows.get(body.workflow_id)
    if workflow is None or body.return_url not in app.return_urls:
        raise HTTPException(422, detail="Workflow or exact return URL is not configured for this application.")
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    row = db.scalar(sa.select(VerificationBinding).where(VerificationBinding.organization_id == tenant.organization_id,
        VerificationBinding.application_id == app.id, VerificationBinding.idempotency_key == idempotency_key)) if idempotency_key else None
    replay = row is not None
    if replay:
        if row.request_fingerprint != fingerprint:
            raise HTTPException(409, detail="Idempotency-Key already used with another request.")
        record = get_session(db, tenant, row.session_id, request_id)
    else:
        consume_session_quota(db, app)
        external = subject(db, tenant.organization_id, body.external_subject_id)
        # Core engines see a tenant pseudonym, not a business identifier or workflow implementation.
        record, _ = create_session(db, tenant, SessionCreate(user_id=str(external.id), **workflow),
                                   settings.session_ttl_seconds, request_id)
        row = VerificationBinding(organization_id=tenant.organization_id, session_id=record.id,
            application_id=app.id, subject_id=external.id, workflow_id=body.workflow_id, reference=body.reference,
            return_url=body.return_url, locale=body.locale, mode=app.mode, request_fingerprint=fingerprint,
            idempotency_key=idempotency_key, capture_expires_at=min(aware(record.expires_at),
                utcnow() + timedelta(seconds=settings.integration_capture_ttl_seconds)))
        db.add(row)
        db.flush()
    token = None
    # Retrying creation preserves the session; issuing a fresh token revokes any lost token.
    if record.status.value not in FINAL and not row.technical_error and not record.erased_at:
        row.capture_expires_at = min(aware(record.expires_at), utcnow() + timedelta(seconds=settings.integration_capture_ttl_seconds))
        token = issue_client_token(db, tenant, record, request_id)
    url = settings.integration_public_base_url.rstrip("/") + "/verify/" + str(record.id)
    if token:
        url += "#" + urlencode({"token": token, "organization": str(tenant.organization_id)})
    return {"session_id": record.id, "hosted_verification_url": url, "capture_token": token,
            "expires_at": aware(record.expires_at), "capture_expires_at": aware(row.capture_expires_at),
            "sandbox": row.mode == "sandbox"}, replay


def snapshot(db, record, outcome=None):
    """Called before commit, after engine/reviewer evidence has been written, even with no subscribers."""
    row = db.scalar(sa.select(VerificationBinding).where(VerificationBinding.organization_id == record.organization_id,
                                                        VerificationBinding.session_id == record.id))
    if row is None:
        return None
    outcome = outcome or ("TECHNICAL_ERROR" if row.technical_error else record.status.value)
    if outcome not in FINAL:
        return None
    existing = db.scalar(sa.select(VerificationDecision).where(VerificationDecision.organization_id == record.organization_id,
        VerificationDecision.session_id == record.id, VerificationDecision.result_version == record.version))
    if existing:
        return existing
    app = db.get(Application, row.application_id)
    evidence = collect_evidence(db, record)
    fields = {}
    if evidence.document is not None and not record.erased_at:
        for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
            DocumentField.session_id == record.id, DocumentField.document_id == evidence.document.id,
            DocumentField.field_name.in_(app.identity_fields))):
            if field.normalized_value_ciphertext is not None:
                fields[field.field_name] = {"value": base64.b64encode(field.normalized_value_ciphertext).decode(),
                    "key": field.key_version,
                    "context": f"field/{record.organization_id}/{record.id}/{field.document_id}/{field.field_name}/normalized"}
    result = VerificationDecision(id=uuid4(), organization_id=record.organization_id, session_id=record.id,
        result_version=record.version, outcome=outcome,
        checks={key: value for key, value in evidence.checks.items() if key in app.check_fields}, encrypted_fields=fields,
        identity_delete_after=utcnow() + timedelta(days=db.get(Organization, record.organization_id).pii_retention_days), event_id=uuid4())
    db.add(result)
    db.flush()
    data = {"session_id": str(record.id), "application_id": str(app.id), "outcome": outcome,
            "result_version": result.result_version, "sandbox": row.mode == "sandbox"}
    payload = envelope(result.event_id, RESULT_EVENT, record.organization_id, utcnow(), data)
    endpoints = db.scalars(sa.select(WebhookEndpoint).where(WebhookEndpoint.organization_id == record.organization_id,
        WebhookEndpoint.application_id == app.id, WebhookEndpoint.active.is_(True)))
    for endpoint in endpoints:
        if endpoint.event_types and RESULT_EVENT not in endpoint.event_types:
            continue
        db.add(WebhookDelivery(organization_id=record.organization_id, endpoint_id=endpoint.id, event_id=result.event_id,
            event_type=RESULT_EVENT, session_id=record.id, payload=payload, status="PENDING", attempts=0, next_attempt_at=utcnow()))
        notify_after_commit(db, record.organization_id)
    return result


def read_result(db, tenant, row, record, cipher):
    result = db.scalar(sa.select(VerificationDecision).where(VerificationDecision.organization_id == tenant.organization_id,
        VerificationDecision.session_id == record.id).order_by(VerificationDecision.result_version.desc()).limit(1))
    # Expiry may have been recorded in this request; make it readable before before_commit runs.
    if result is None or record.status.value in FINAL and result.result_version < record.version:
        result = snapshot(db, record) or result
    app = application(db, tenant, row.application_id)
    identity, checks = {}, {}
    if result:
        if "results:identity" in tenant.scopes and cipher and not record.erased_at and aware(result.identity_delete_after) > utcnow():
            for key, sealed in result.encrypted_fields.items():
                if key in app.identity_fields:
                    identity[key] = cipher.open(base64.b64decode(sealed["value"]), sealed["key"], sealed["context"])
        if "results:checks" in tenant.scopes:
            checks = {key: value for key, value in result.checks.items() if key in app.check_fields}
    return {"session_id": record.id, "outcome": result.outcome if result else None,
            "result_version": result.result_version if result else 0, "sandbox": row.mode == "sandbox",
            "identity": identity, "checks": checks}


def technical_failure(db, record):
    """Internal provider/worker hook for an unrecoverable failure; no public success override."""
    row = db.scalar(sa.select(VerificationBinding).where(VerificationBinding.organization_id == record.organization_id,
        VerificationBinding.session_id == record.id).with_for_update())
    if row is None or record.status.value in FINAL:
        raise HTTPException(409, detail="Verification is already decided or not an integration session.")
    row.technical_error = True
    record.version += 1
    return snapshot(db, record, "TECHNICAL_ERROR")
