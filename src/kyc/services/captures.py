"""Document capture orchestration: quality gate, encrypted storage, session progress.

Outcomes that must persist (attempt evidence, expiry) are returned as responses
rather than raised, so the request transaction still commits them.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.api.schemas import CaptureGeometry, CaptureQuality, CaptureResponse
from kyc.db.models import AuditLog, DocumentCheck, DocumentImage, IdentityDocument, KYCSession, Organization
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import CheckResult, SessionStatus
from kyc.domain.state_machine import Event, TERMINAL_STATUSES
from kyc.engines.capture_quality import CaptureRejected, decode_capture
from kyc.engines.contracts import DocumentQualityEngine
from kyc.services.sessions import apply_event, is_expired
from kyc.storage.captures import CaptureStore

CHECK_TYPE = "CAPTURE_QUALITY"


@dataclass(frozen=True)
class CaptureLimits:
    max_bytes: int
    max_pixels: int
    max_attempts: int


def _error(status_code: int, reason_code: str, detail: str, attempts_remaining: int | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "reason_code": reason_code,
                                                          "attempts_remaining": attempts_remaining})


def _audit(db: Session, record: KYCSession, tenant: TenantContext, request_id: UUID, action: str,
           reason_codes: list[str], metadata: dict) -> None:
    db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action=action, request_id=request_id, from_status=record.status.value,
                    to_status=record.status.value, reason_codes=reason_codes,
                    event_metadata={"version": record.version, **metadata}))


def current_images(db: Session, record: KYCSession) -> dict[str, DocumentImage]:
    images = db.scalars(sa.select(DocumentImage).where(DocumentImage.organization_id == record.organization_id,
                                                       DocumentImage.session_id == record.id)).all()
    return {image.side: image for image in images}


def side_progress(db: Session, record: KYCSession) -> dict[str, str]:
    accepted = current_images(db, record)
    return {side: "ACCEPTED" if side in accepted else "REQUIRED"
            for side in requirement_for(record.expected_document_type).sides}


def _document(db: Session, record: KYCSession, now: datetime) -> IdentityDocument:
    document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.organization_id == record.organization_id,
                                                           IdentityDocument.session_id == record.id)
                         .order_by(IdentityDocument.created_at))
    if document is None:
        retention = db.scalar(sa.select(Organization.pii_retention_days).where(Organization.id == record.organization_id))
        # The claimed type, unclassified: classification arrives with the document engine.
        document = IdentityDocument(id=uuid4(), organization_id=record.organization_id, session_id=record.id,
                                    document_type=record.expected_document_type, issuing_country=record.country,
                                    classification_confidence=None, created_at=now,
                                    delete_after=now + timedelta(days=retention))
        db.add(document)
        db.flush()
    return document


def submit_capture(db: Session, tenant: TenantContext, session_id: UUID, side: str, data: bytes,
                   engine: DocumentQualityEngine, store: CaptureStore, limits: CaptureLimits,
                   request_id: UUID) -> CaptureResponse | JSONResponse:
    record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                       KYCSession.organization_id == tenant.organization_id).with_for_update())
    if record is None:
        raise HTTPException(404, detail="Session not found.")
    now = datetime.now(timezone.utc)
    if is_expired(record, now):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
        return _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.")
    if record.status in TERMINAL_STATUSES:
        return _error(409, "SESSION_CLOSED", "The session is closed. Create a new session.")
    requirement = requirement_for(record.expected_document_type)
    if side not in requirement.sides:
        raise HTTPException(422, detail=f"{side} is not captured for {record.expected_document_type.value}. "
                                        f"Required sides: {', '.join(requirement.sides)}.")
    if record.status == SessionStatus.CREATED:
        apply_event(db, record, tenant, Event.START, request_id)
    if record.status != SessionStatus.DOCUMENT_REQUIRED:
        return _error(409, "CAPTURE_NOT_OPEN", f"Document capture is not open while the session is {record.status.value}.")

    attempts = db.scalar(sa.select(sa.func.count()).select_from(DocumentCheck).where(
        DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id,
        DocumentCheck.check_type == CHECK_TYPE)) or 0
    if attempts >= limits.max_attempts:
        _audit(db, record, tenant, request_id, "CAPTURE_ATTEMPTS_EXCEEDED", ["CAPTURE_ATTEMPTS_EXCEEDED"], {"side": side})
        return _error(429, "CAPTURE_ATTEMPTS_EXCEEDED", "Too many capture attempts. Create a new session.", 0)
    remaining = limits.max_attempts - attempts - 1
    document = _document(db, record, now)
    digest = hashlib.sha256(data).hexdigest()

    try:
        capture = decode_capture(data, max_bytes=limits.max_bytes, max_pixels=limits.max_pixels)
    except CaptureRejected as rejected:
        db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                             check_type=CHECK_TYPE, result=CheckResult.FAIL,
                             evidence_metadata={"side": side, "outcome": "REJECTED_INPUT", "reason_codes": [rejected.reason_code],
                                                "sha256": digest, "size_bytes": len(data)}))
        _audit(db, record, tenant, request_id, "DOCUMENT_CAPTURE_REJECTED", [rejected.reason_code], {"side": side})
        return _error(422, rejected.reason_code, str(rejected), remaining)

    assessment = engine.assess(capture, requirement.aspect_ratio)
    reasons, instructions = list(assessment.reason_codes), list(assessment.instructions)
    images = current_images(db, record)
    if assessment.accepted and any(image.sha256 == digest for other, image in images.items() if other != side):
        reasons, instructions = ["SAME_IMAGE_FOR_MULTIPLE_SIDES"], ["CAPTURE_OTHER_SIDE"]
    accepted = not reasons
    height, width = capture.pixels.shape[:2]
    db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                         check_type=CHECK_TYPE, result=CheckResult.PASS if accepted else CheckResult.FAIL,
                         evidence_metadata={"side": side, "outcome": "ACCEPTED" if accepted else "RECAPTURE",
                                            "scores": assessment.scores, "reason_codes": reasons,
                                            "instructions": instructions, "geometry": assessment.geometry,
                                            "policy_version": assessment.policy_version, "sha256": digest,
                                            "media_type": capture.media_type, "width": width, "height": height}))

    if accepted:
        if side in images:
            # Replaced ciphertext is removed by the orphan sweep after this transaction commits.
            db.delete(images[side])
            db.flush()
        retention = db.scalar(sa.select(Organization.capture_retention_hours).where(Organization.id == record.organization_id))
        image_id = uuid4()
        # Original bytes are kept (encrypted) as evidence for later fraud/metadata checks.
        stored = store.put(record.organization_id, record.id, image_id, data)
        db.add(DocumentImage(id=image_id, organization_id=record.organization_id, session_id=record.id,
                             document_id=document.id, side=side, encrypted_object_ref=stored.ref,
                             key_version=stored.key_version, media_type=capture.media_type, sha256=stored.sha256,
                             quality_scores=assessment.scores, quality_policy_version=assessment.policy_version,
                             created_at=now, delete_after=now + timedelta(hours=retention)))
        db.flush()

    _audit(db, record, tenant, request_id, "DOCUMENT_CAPTURE_ACCEPTED" if accepted else "DOCUMENT_CAPTURE_RECAPTURE",
           reasons or ["CAPTURE_QUALITY_PASS"], {"side": side, "policy_version": assessment.policy_version})
    sides = side_progress(db, record)
    if all(state == "ACCEPTED" for state in sides.values()):
        # Every required side passed the gate: hand off to the document engine (Phase 3+).
        apply_event(db, record, tenant, Event.DOCUMENT_SUBMITTED, request_id)
        next_step = "AWAIT_DOCUMENT_PROCESSING"
    else:
        missing = next(name for name, state in sides.items() if state == "REQUIRED")
        next_step = f"CAPTURE_{missing}"
    db.flush()
    return CaptureResponse(
        session_id=record.id, status=record.status, side=side,
        capture_status="ACCEPTED" if accepted else "RECAPTURE",
        quality=CaptureQuality(**assessment.scores, policy_version=assessment.policy_version),
        geometry=CaptureGeometry(**assessment.geometry), reason_codes=reasons, instructions=instructions,
        sides=sides, next_step=next_step, attempts_remaining=remaining)
