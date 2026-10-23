"""Session-bound public bridge to the independently gated biometric service."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
import sqlalchemy as sa

from kyc.api.dependencies import Database, TenantContext, require
from kyc.db.models import (Application, ExternalSubject, FaceActor, FaceCaptureSession, FaceEnrollment, FaceSubject,
                          IntegrationFaceSession, KYCSession, Organization, RegisteredDevice, VerificationBinding)
from kyc.db.session import set_tenant
from kyc.services.sessions import aware
from kyc.tenancy.keys import digest, new_secret
from .schemas import FaceSessionCreate, RecognitionConsent, FaceSessionCreated, RecognitionResult
from kyc.face_identity.routes import CaptureReply, EnrollmentReply, RecognitionReply
from .service import application, binding, consume_session_quota, utcnow

router = APIRouter(prefix="/v1", tags=["integration-face-identity"])
EnrollmentWriter = require("enrollments:write")
RecognitionWriter = require("recognition:write")
RecognitionReader = require("recognition:read")


def live_device(db, row, token=None, require_token=False):
    device = db.scalar(sa.select(RegisteredDevice).where(RegisteredDevice.organization_id == row.organization_id,
        RegisteredDevice.application_id == row.application_id, RegisteredDevice.id == row.device_id).with_for_update())
    if (device is None or device.revoked_at or aware(device.expires_at) <= utcnow()
            or row.population not in device.populations or row.purpose not in device.purposes
            or require_token and (token is None or not secrets.compare_digest(device.token_hash, digest(token)))):
        raise HTTPException(403, detail="Device expired, revoked or not authorized.")
    return device


def eligible_subject(db, tenant, row):
    subject = db.scalar(sa.select(ExternalSubject).where(ExternalSubject.organization_id == tenant,
                                                       ExternalSubject.id == row.subject_id))
    kyc = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == tenant,
                                               KYCSession.id == row.verification_session_id))
    link = db.scalar(sa.select(VerificationBinding).where(VerificationBinding.organization_id == tenant,
        VerificationBinding.application_id == row.application_id, VerificationBinding.session_id == row.verification_session_id,
        VerificationBinding.subject_id == row.subject_id, VerificationBinding.mode == "production"))
    if subject is None or subject.erased_at or kyc is None or kyc.status.value != "VERIFIED" or kyc.erased_at or link is None or link.technical_error:
        raise HTTPException(403, detail="A real VERIFIED KYC session linked to the authorized subject is required.")
    return subject, kyc


def create_face(body, request, tenant, db, kind):
    app = application(db, tenant, lock=True)
    if app.mode != "production":
        raise HTTPException(403, detail="Sandbox simulations cannot enroll or identify real faces.")
    request.app.state.face_identity.require_ready()
    if body.purpose not in app.purposes or body.population not in app.populations:
        raise HTTPException(403, detail="Application purpose or population not authorized.")
    if ((body.purpose == "staff_lookup" and body.population != "STAFF")
            or (body.purpose == "customer_service" and body.population != "CUSTOMER")):
        raise HTTPException(403, detail="Population does not match the authorized purpose.")
    external = None
    if body.external_subject_id:
        external = db.scalar(sa.select(ExternalSubject).where(ExternalSubject.organization_id == tenant.organization_id,
            ExternalSubject.external_id == body.external_subject_id, ExternalSubject.erased_at.is_(None)))
        if external is None:
            raise HTTPException(403, detail="Authorized subject not found.")
    token, token_hash = new_secret("fct_")
    row = IntegrationFaceSession(organization_id=tenant.organization_id, application_id=app.id,
        subject_id=external.id if external else None, verification_session_id=body.verification_session_id,
        device_id=body.device_id, kind=kind, purpose=body.purpose, population=body.population,
        capture_token_hash=token_hash, expires_at=utcnow() + timedelta(seconds=300))
    if body.device_id:
        if kind != "RECOGNITION" or external or body.verification_session_id or body.purpose == "self_profile":
            raise HTTPException(422, detail="Device recognition must use only a registered device and its purpose.")
        live_device(db, row)
    else:
        if body.purpose != "self_profile" or external is None or body.verification_session_id is None:
            raise HTTPException(422, detail="A subject and its VERIFIED session are required for self capture.")
        eligible_subject(db, tenant.organization_id, row)
    consume_session_quota(db, app)
    db.add(row)
    db.flush()
    return {"session_id": row.id, "capture_token": token, "expires_at": aware(row.expires_at),
            "purpose": row.purpose, "population": row.population, "consent_required": kind == "ENROLLMENT"}


@router.post("/face-enrollment-sessions", status_code=201, response_model=FaceSessionCreated)
def enrollment(body: FaceSessionCreate, request: Request, tenant: EnrollmentWriter, db: Database):
    return create_face(body, request, tenant, db, "ENROLLMENT")


@router.post("/recognition-sessions", status_code=201, response_model=FaceSessionCreated)
def recognition(body: FaceSessionCreate, request: Request, tenant: RecognitionWriter, db: Database):
    return create_face(body, request, tenant, db, "RECOGNITION")


@dataclass
class CaptureContext:
    db: object
    row: IntegrationFaceSession
    app: Application
    service: object


def capture_context(request: Request, session_id: UUID,
                    x_organization_id: Annotated[UUID, Header()], authorization: Annotated[str, Header()],
                    x_device_token: Annotated[str | None, Header(max_length=200)] = None):
    token = authorization.removeprefix("Bearer ")
    if not token.startswith("fct_") or not 40 <= len(token) <= 100:
        raise HTTPException(401, detail="A face capture token is required.")
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, x_organization_id)
        row = db.scalar(sa.select(IntegrationFaceSession).where(IntegrationFaceSession.organization_id == x_organization_id,
            IntegrationFaceSession.id == session_id).with_for_update())
        if row is None or not secrets.compare_digest(row.capture_token_hash, digest(token)):
            raise HTTPException(401, detail="Face capture token invalid.")
        app = db.get(Application, row.application_id)
        organization = db.get(Organization, x_organization_id)
        if not app or not app.active or not organization or not organization.active:
            raise HTTPException(401, detail="Face capture revoked.")
        if aware(row.expires_at) <= utcnow():
            raise HTTPException(410, detail="EXPIRED")
        expected = "ENROLLMENT" if request.url.path.startswith("/v1/face-enrollment-sessions/") else "RECOGNITION"
        if row.kind != expected or row.population not in app.populations or row.purpose not in app.purposes:
            raise HTTPException(403, detail="Capture purpose not authorized.")
        if row.device_id:
            live_device(db, row, x_device_token, require_token=True)
        else:
            eligible_subject(db, x_organization_id, row)
        wait = request.app.state.rate_limiter.hit(f"face-capture/{x_organization_id}/{session_id}", 120)
        if wait:
            raise HTTPException(429, detail="Face capture rate limit exceeded.", headers={"Retry-After": "60"})
        yield CaptureContext(db, row, app, request.app.state.face_identity)


Capture = Annotated[CaptureContext, Depends(capture_context, scope="function")]


@contextmanager
def biometric_transaction(ctx):
    ctx.service.require_ready()
    with ctx.service.factory() as db, db.begin():
        set_tenant(db, ctx.row.organization_id)
        if db.get_bind().dialect.name == "postgresql":
            db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                       {"scope": "face-identity/" + str(ctx.row.organization_id)})
        yield db


@router.post("/face-enrollment-sessions/{session_id}/capture", response_model=CaptureReply)
@router.post("/recognition-sessions/{session_id}/capture", response_model=CaptureReply)
def begin_capture(session_id: UUID, ctx: Capture, body: RecognitionConsent | None = None):
    if ctx.row.face_session_id:
        raise HTTPException(409, detail="Capture already started. Restart with a new session if the frame ticket was lost.")
    with biometric_transaction(ctx) as db:
        person = None
        if ctx.row.subject_id:
            person = db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == ctx.row.organization_id,
                FaceSubject.kyc_session_id == ctx.row.verification_session_id))
            if person is None:
                person = FaceSubject(organization_id=ctx.row.organization_id, kyc_session_id=ctx.row.verification_session_id,
                                     population=ctx.row.population, active=True)
                db.add(person)
                db.flush()
            if person.population != ctx.row.population or not person.active:
                raise HTTPException(403, detail="Subject population not authorized.")
        kind = "PERSON" if person else "KIOSK" if ctx.row.purpose == "staff_lookup" else "OPERATOR"
        actor = FaceActor(organization_id=ctx.row.organization_id, application_id=ctx.app.id,
            subject_id=person.id if person else None, kind=kind, token_hash=digest(secrets.token_urlsafe(32)),
            purposes=[ctx.row.purpose], populations=[ctx.row.population], profile_fields=ctx.app.profile_fields,
            branch=str(ctx.row.device_id) if kind == "KIOSK" else None, expires_at=ctx.row.expires_at)
        db.add(actor)
        db.flush()
        result = ctx.service.create(db, actor, ctx.row.kind, ctx.row.purpose,
                                    body.notice_version if body else None, body.consent if body else False)
        ctx.row.actor_id, ctx.row.face_session_id = actor.id, UUID(result["session_id"])
        ctx.row.expires_at = min(aware(ctx.row.expires_at), aware(actor.expires_at), aware(db.get(FaceCaptureSession, ctx.row.face_session_id).expires_at))
        # Expose only the public authorization-envelope ID to the client.
        return {**result, "session_id": str(session_id), "expires_at": aware(ctx.row.expires_at).isoformat()}


def actor_session(ctx, db):
    actor = db.scalar(sa.select(FaceActor).where(FaceActor.organization_id == ctx.row.organization_id,
        FaceActor.id == ctx.row.actor_id, FaceActor.application_id == ctx.app.id))
    if actor is None or actor.revoked_at or aware(actor.expires_at) <= utcnow():
        raise HTTPException(403, detail="Capture actor revoked or expired.")
    return actor, ctx.service.session(db, actor, ctx.row.face_session_id, ctx.row.kind)


@router.post("/face-enrollment-sessions/{session_id}/frames", response_model=CaptureReply | RecognitionReply, response_model_exclude_none=True)
@router.post("/recognition-sessions/{session_id}/frames", response_model=CaptureReply | RecognitionReply, response_model_exclude_none=True)
def frame(session_id: UUID, request: Request, ctx: Capture, ticket: Annotated[str, Form(min_length=32, max_length=128)],
          frame: Annotated[UploadFile, File()]):
    with biometric_transaction(ctx) as db:
        actor, session = actor_session(ctx, db)
        result = ctx.service.frame(db, actor, session, ticket, frame.file.read(request.app.state.settings.max_selfie_bytes + 1))
        return {**result, "session_id": str(session_id)} if "session_id" in result else result


@router.post("/face-enrollment-sessions/{session_id}/complete", response_model=EnrollmentReply | RecognitionReply, response_model_exclude_none=True)
@router.post("/recognition-sessions/{session_id}/complete", response_model=RecognitionReply, response_model_exclude_none=True)
def complete(session_id: UUID, ctx: Capture,
             idempotency_key: Annotated[str, Header(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]):
    with biometric_transaction(ctx) as db:
        actor, session = actor_session(ctx, db)
        # Completion never accepts embeddings, similarity values, or a caller's liveness assertion.
        return ctx.service.complete(db, actor, session, idempotency_key)


@router.get("/recognition-sessions/{session_id}/result", response_model=RecognitionResult, response_model_exclude_none=True)
def recognition_result(session_id: UUID, request: Request, tenant: RecognitionReader, db: Database):
    app = application(db, tenant)
    row = db.scalar(sa.select(IntegrationFaceSession).where(IntegrationFaceSession.organization_id == tenant.organization_id,
        IntegrationFaceSession.application_id == app.id, IntegrationFaceSession.id == session_id,
        IntegrationFaceSession.kind == "RECOGNITION").with_for_update())
    if row is None:
        raise HTTPException(404, detail="Recognition session not found.")
    if aware(row.expires_at) <= utcnow():
        return {"outcome": "EXPIRED"}
    if row.population not in app.populations or row.purpose not in app.purposes:
        raise HTTPException(403, detail="Recognition authorization revoked.")
    if row.device_id:
        live_device(db, row)
    else:
        eligible_subject(db, tenant.organization_id, row)
    if not row.face_session_id:
        return {"outcome": "RETRY_REQUIRED"}
    ctx = CaptureContext(db, row, app, request.app.state.face_identity)
    with biometric_transaction(ctx) as biometric_db:
        actor, session = actor_session(ctx, biometric_db)
        reply = ctx.service.result(biometric_db, actor, session)
        if reply.get("outcome") != "MATCHED":
            return reply
        enrollment = biometric_db.scalar(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == tenant.organization_id,
            FaceEnrollment.id == session.matched_enrollment_id))
        face_subject = biometric_db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == tenant.organization_id,
            FaceSubject.id == enrollment.subject_id))
        external = db.scalar(sa.select(ExternalSubject).join(VerificationBinding, sa.and_(
            VerificationBinding.organization_id == ExternalSubject.organization_id, VerificationBinding.subject_id == ExternalSubject.id))
            .where(ExternalSubject.organization_id == tenant.organization_id, ExternalSubject.erased_at.is_(None),
                   VerificationBinding.session_id == face_subject.kyc_session_id, VerificationBinding.mode == "production"))
        if external is None:
            return {"outcome": "NO_MATCH"}
        profile = {}
        if "kyc_status" in app.profile_fields:
            profile["kyc_status"] = "VERIFIED"
        from kyc.face_identity.service import profile_context, KIOSK_FIELDS, PROFILE_FIELDS
        permitted = (set(app.profile_fields) & set(actor.profile_fields)
                     & (KIOSK_FIELDS if actor.kind == "KIOSK" else PROFILE_FIELDS)) - {"kyc_status"}
        if permitted and face_subject.profile_ciphertext and face_subject.profile_expires_at and aware(face_subject.profile_expires_at) > utcnow():
            from cryptography.exceptions import InvalidTag
            try:
                values = json.loads(request.app.state.field_cipher.open(face_subject.profile_ciphertext,
                    face_subject.profile_key_version, profile_context(face_subject)))
                if not isinstance(values, dict):
                    raise ValueError()
                profile.update({key: values[key] for key in permitted if isinstance(values.get(key), str)})
            except (InvalidTag, ValueError, KeyError, TypeError):
                return {"outcome": "TECHNICAL_ERROR"}
        return {**reply, "external_subject_id": external.external_id, "profile": profile}
