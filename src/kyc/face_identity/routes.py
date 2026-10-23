"""Face-specific credentials. A KYC link, partner API key or anonymous scan is insufficient."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import FaceActor, FaceDeletion, FaceEnrollment, FaceRecognitionEvent, Organization
from kyc.db.session import set_tenant
from kyc.services.sessions import aware
from .service import digest, error, now

router = APIRouter(prefix="/v1", tags=["optional-face-identity"])


@dataclass
class IdentityContext:
    db: Session
    actor: FaceActor
    service: object


def identity_context(request: Request, x_organization_id: Annotated[UUID, Header()],
                     authorization: Annotated[str, Header()]):
    if not authorization.startswith("Bearer fri_") or not 40 <= len(authorization) <= 256:
        error(401, "FACE_AUTHENTICATION_REQUIRED")
    service = request.app.state.face_identity
    if request.app.state.settings.environment == "production" and request.url.scheme != "https":
        error(403, "HTTPS_REQUIRED")
    with service.factory() as db, db.begin():
        set_tenant(db, x_organization_id)
        # Serialize population mutation, withdrawal, search and disclosure per tenant.
        # This initial exact-search implementation favors a clear linearization point
        # over throughput. Capacity must be measured before raising population limits.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                       {"scope": "face-identity/" + str(x_organization_id)})
        organization = db.get(Organization, x_organization_id)
        actor = db.scalar(sa.select(FaceActor).where(FaceActor.organization_id == x_organization_id,
                          FaceActor.token_hash == digest(authorization.removeprefix("Bearer "))))
        if organization is None or not organization.active or actor is None or actor.revoked_at or aware(actor.expires_at) <= now():
            error(401, "FACE_CREDENTIAL_UNUSABLE")
        if (not isinstance(actor.populations, list) or not actor.populations
                or set(actor.populations) - {"STAFF", "CUSTOMER"}
                or actor.kind == "KIOSK" and (set(actor.populations) != {"STAFF"} or not actor.branch)):
            error(403, "POPULATION_NOT_AUTHORIZED")
        if actor.kind == "PERSON":
            subject, _ = service.subject(db, actor, verified=False)
            if set(actor.populations) != {subject.population}:
                error(403, "POPULATION_NOT_AUTHORIZED")
        wait = request.app.state.rate_limiter.hit("face/" + str(x_organization_id) + "/" + str(actor.id), 120)
        if wait:
            request.state.security_reason = "FACE_RATE_LIMITED"
            error(429, "FACE_RATE_LIMITED")
        request.state.credential_type = "face_actor"
        yield IdentityContext(db, actor, service)


Identity = Annotated[IdentityContext, Depends(identity_context, scope="function")]
CompletionKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollmentStart(StrictBody):
    consent: bool
    notice_version: str = Field(min_length=1, max_length=80)


class RecognitionStart(StrictBody):
    purpose: str = Field(min_length=1, max_length=40)


class Reference(StrictBody):
    recognition_reference: str = Field(min_length=32, max_length=128)


class RecognitionReply(BaseModel):
    outcome: Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "RETRY_REQUIRED", "LIVENESS_FAILED", "EXPIRED", "TECHNICAL_ERROR"]
    message: str
    recognition_reference: str | None = None
    expires_at: str | None = None


class CaptureReply(BaseModel):
    session_id: str
    expires_at: str
    step: str | None
    instruction: str
    step_index: int
    steps_total: int
    held: int
    required: int
    state: str
    ticket: str
    instructions: list[str]


class EnrollmentReply(BaseModel):
    status: Literal["ACTIVE", "DUPLICATE_REVIEW"]
    message: str
    expires_at: str


@router.get("/face-recognition/capabilities")
def capabilities(ctx: Identity):
    return ctx.service.capabilities(ctx.db, ctx.actor)


@router.post("/face-enrollments/sessions", status_code=201, response_model=CaptureReply)
def enrollment_session(body: EnrollmentStart, ctx: Identity):
    return ctx.service.create(ctx.db, ctx.actor, "ENROLLMENT", "self_profile", body.notice_version, body.consent)


@router.post("/recognition/sessions", status_code=201, response_model=CaptureReply)
def recognition_session(body: RecognitionStart, ctx: Identity):
    return ctx.service.create(ctx.db, ctx.actor, "RECOGNITION", body.purpose)


def capture(request, identifier, ctx, kind, ticket, frame):
    session = ctx.service.session(ctx.db, ctx.actor, identifier, kind)
    data = frame.file.read(request.app.state.settings.max_selfie_bytes + 1)
    return ctx.service.frame(ctx.db, ctx.actor, session, ticket, data)


@router.post("/face-enrollments/sessions/{identifier}/frames", response_model=CaptureReply | RecognitionReply, response_model_exclude_none=True)
def enrollment_frame(identifier: UUID, request: Request, ctx: Identity,
                     ticket: Annotated[str, Form(min_length=32, max_length=128)], frame: Annotated[UploadFile, File()]):
    return capture(request, identifier, ctx, "ENROLLMENT", ticket, frame)


@router.post("/recognition/sessions/{identifier}/frames", response_model=CaptureReply | RecognitionReply, response_model_exclude_none=True)
def recognition_frame(identifier: UUID, request: Request, ctx: Identity,
                      ticket: Annotated[str, Form(min_length=32, max_length=128)], frame: Annotated[UploadFile, File()]):
    return capture(request, identifier, ctx, "RECOGNITION", ticket, frame)


@router.post("/face-enrollments/sessions/{identifier}/complete", response_model=EnrollmentReply | RecognitionReply, response_model_exclude_none=True)
def enrollment_complete(identifier: UUID, ctx: Identity, idempotency_key: CompletionKey):
    return ctx.service.complete(ctx.db, ctx.actor, ctx.service.session(ctx.db, ctx.actor, identifier, "ENROLLMENT"), idempotency_key)


@router.post("/recognition/sessions/{identifier}/complete", response_model=RecognitionReply, response_model_exclude_none=True)
def recognition_complete(identifier: UUID, ctx: Identity, idempotency_key: CompletionKey):
    return ctx.service.complete(ctx.db, ctx.actor, ctx.service.session(ctx.db, ctx.actor, identifier, "RECOGNITION"), idempotency_key)


@router.get("/me/face-enrollment")
def enrollment_status(ctx: Identity):
    return ctx.service.enrollment_status(ctx.db, ctx.actor)


@router.delete("/me/face-enrollment")
def withdraw(ctx: Identity):
    return ctx.service.withdraw(ctx.db, ctx.actor)


@router.get("/recognition/sessions/{identifier}/result", response_model=RecognitionReply, response_model_exclude_none=True)
def result(identifier: UUID, ctx: Identity):
    return ctx.service.result(ctx.db, ctx.actor, ctx.service.session(ctx.db, ctx.actor, identifier, "RECOGNITION"))


@router.post("/recognition/sessions/{identifier}/profile")
def disclose(identifier: UUID, body: Reference, ctx: Identity, idempotency_key: CompletionKey):
    return ctx.service.disclose(ctx.db, ctx.actor, ctx.service.session(ctx.db, ctx.actor, identifier, "RECOGNITION"),
                                body.recognition_reference, idempotency_key)


def reviewer(ctx):
    if (ctx.actor.kind != "OPERATOR" or "duplicate_resolution" not in ctx.actor.purposes
            or set(ctx.actor.populations) != {"STAFF", "CUSTOMER"}):
        error(403, "DUPLICATE_REVIEW_AUTHORIZATION_REQUIRED")


@router.get("/face-enrollments/duplicates")
def duplicates(ctx: Identity):
    reviewer(ctx)
    # Only explicitly privileged internal review sees pseudonymous raw match evidence.
    rows = ctx.db.scalars(sa.select(FaceRecognitionEvent).where(FaceRecognitionEvent.organization_id == ctx.actor.organization_id,
        FaceRecognitionEvent.outcome == "DUPLICATE_REVIEW").order_by(FaceRecognitionEvent.created_at.desc()).limit(100))
    return {"cases": [{"event_id": str(row.id), "at": row.created_at,
                      "evidence": ctx.service._open(row, row.evidence_ciphertext, row.evidence_key_version, "review-evidence")}
                     for row in rows if row.evidence_ciphertext]}


class DuplicateDecision(StrictBody):
    action: Literal["REJECT", "ALLOW_DISTINCT"]
    reason_code: Literal["CONFIRMED_DUPLICATE", "REVIEWED_DISTINCT_PERSONS", "INSUFFICIENT_EVIDENCE"]


@router.post("/face-enrollments/duplicates/{identifier}/resolve")
def resolve_duplicate(identifier: UUID, body: DuplicateDecision, ctx: Identity, idempotency_key: CompletionKey):
    reviewer(ctx)
    ctx.service.require_ready()
    enrollment = ctx.db.scalar(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == ctx.actor.organization_id,
                                                              FaceEnrollment.id == identifier))
    if enrollment is None:
        error(404, "DUPLICATE_CASE_NOT_FOUND")
    from .policy import sha
    fingerprint = sha(body.model_dump())
    if enrollment.resolution_key:
        if enrollment.resolution_key != digest(idempotency_key) or enrollment.resolution_fingerprint != fingerprint:
            error(409, "DUPLICATE_RESOLUTION_CONFLICT")
        return {"status": enrollment.status}
    if enrollment.status != "DUPLICATE_REVIEW":
        error(409, "DUPLICATE_CASE_CLOSED")
    from kyc.db.models import FaceConsent, FaceSubject, KYCSession
    consent = ctx.db.get(FaceConsent, enrollment.consent_id)
    subject = ctx.db.get(FaceSubject, enrollment.subject_id)
    kyc = ctx.db.get(KYCSession, subject.kyc_session_id)
    if (not subject.active or kyc.status.value != "VERIFIED" or kyc.erased_at or consent.withdrawn_at
            or aware(consent.expires_at) <= now() or aware(enrollment.expires_at) <= now()
            or enrollment.policy_version != ctx.service.policy.binding
            or str(enrollment.id) in ctx.service.ledger.denied(ctx.actor.organization_id)):
        error(409, "DUPLICATE_CASE_NO_LONGER_ELIGIBLE")
    if body.action == "REJECT":
        ctx.service.revoke(ctx.db, enrollment, "DUPLICATE_REJECTED", "REJECTED")
    else:
        ctx.service._replace(ctx.db, ctx.actor, enrollment.subject_id)
        enrollment.status = "ACTIVE"
    enrollment.resolution_key, enrollment.resolution_fingerprint = digest(idempotency_key), fingerprint
    ctx.service._event(ctx.db, ctx.actor, None, "DUPLICATE_" + body.action, [body.reason_code],
                        {"enrollment": str(enrollment.id), "operation_key_hash": digest(idempotency_key)})
    return {"status": enrollment.status}
