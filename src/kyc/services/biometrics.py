"""Tenant-scoped selfie quality and document-to-selfie comparison orchestration.

Captures and templates are encrypted separately. Only measured quality and
comparison evidence enter responses, checks, and audit logs. A face comparison
never grants a final KYC decision or supplies liveness evidence.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.biometrics import FaceEngineUnavailable, InvalidFaceEmbedding, compare_embeddings, serialize_embedding
from kyc.db.models import (AuditLog, BiometricTemplate, Consent, DocumentCheck, DocumentField, DocumentImage,
                           FaceComparison, FaceQualityCheck, IdentityDocument, KYCSession, MRZResult,
                           Organization, SelfieCapture)
from kyc.documents.adapters import adapter_for
from kyc.documents.preprocess import prepare_portrait_side
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import CheckResult, SessionStatus
from kyc.domain.state_machine import Event, TERMINAL_STATUSES
from kyc.engines.capture_quality import CaptureRejected, decode_capture
from kyc.services.sessions import apply_event, aware, is_expired


@dataclass(frozen=True)
class SelfieLimits:
    max_bytes: int
    max_pixels: int
    max_attempts: int
    reference_max_pixels: int | None = None


def _error(code: int, reason: str, detail: str, remaining: int | None = None) -> JSONResponse:
    return JSONResponse(status_code=code, content={"detail": detail, "reason_code": reason,
                                                  "attempts_remaining": remaining})


def _audit(db, record, tenant, request_id, action, reasons=(), **metadata):
    db.add(AuditLog(organization_id=record.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action=action, request_id=request_id, from_status=record.status.value,
                    to_status=record.status.value, reason_codes=list(reasons),
                    event_metadata={"version": record.version, **metadata}))


def _quality_metadata(assessment) -> dict:
    return {"scores": assessment.scores, "reason_codes": list(assessment.reason_codes),
            "instructions": list(assessment.instructions), "face_count": assessment.face_count,
            "policy_version": assessment.policy_version, "detector_name": assessment.detector_name,
            "detector_version": assessment.detector_version, "detector_sha256": assessment.detector_sha256,
            "unverified_checks": list(assessment.unverified_checks)}


def _quality_response(assessment) -> dict:
    return {**assessment.scores, "policy_version": assessment.policy_version,
            "face_count": assessment.face_count, "unverified_checks": list(assessment.unverified_checks)}


def _response(record, assessment, remaining, *, accepted=False, reasons=None, instructions=None,
              comparison=None, next_step="CAPTURE_SELFIE") -> dict:
    return {"session_id": str(record.id), "status": record.status.value,
            "capture_status": "ACCEPTED" if accepted else "RECAPTURE", "quality": _quality_response(assessment),
            "reason_codes": list(assessment.reason_codes if reasons is None else reasons),
            "instructions": list(assessment.instructions if instructions is None else instructions),
            "next_step": next_step, "attempts_remaining": remaining, "comparison": comparison}


def _capture_side(db, record, document, logical_side):
    classification = document.side_classification.get(logical_side, {})
    if classification.get("capture_side"):
        return classification["capture_side"]
    # Older accepted documents did not retain the physical-to-logical side map.
    check = db.scalar(sa.select(DocumentCheck).where(
        DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id,
        DocumentCheck.document_id == document.id, DocumentCheck.check_type == "CLASSIFICATION")
        .order_by(DocumentCheck.created_at.desc()).limit(1))
    if check is not None and "SIDES_SWAPPED" in check.evidence_metadata.get("notes", []):
        return {"FRONT": "BACK", "BACK": "FRONT"}.get(logical_side, logical_side)
    return logical_side


def _reference_image(db, record, document, store, limits):
    """Load a current original; rectify color pixels without the OCR normalizer."""
    adapter = adapter_for(document.document_type)
    layout = getattr(adapter, "layout", None)
    regions = getattr(layout, "portrait_regions", {}) if layout is not None else {}
    logical_side = next(iter(regions), "DATA_PAGE" if "DATA_PAGE" in requirement_for(document.document_type).sides
                        else "FRONT")
    capture_side = _capture_side(db, record, document, logical_side)
    image = db.scalar(sa.select(DocumentImage).where(
        DocumentImage.organization_id == record.organization_id, DocumentImage.session_id == record.id,
        DocumentImage.document_id == document.id, DocumentImage.side == capture_side))
    if image is None or aware(image.delete_after) <= datetime.now(timezone.utc):
        return None, None
    data = store.get(image.encrypted_object_ref, record.organization_id, record.id, image.id)
    # The original was already admitted under the document upload byte limit.
    capture = decode_capture(data, max_bytes=len(data), max_pixels=limits.reference_max_pixels or limits.max_pixels)
    color, _ = prepare_portrait_side(capture.pixels, requirement_for(document.document_type).aspect_ratio)
    region = regions.get(logical_side)
    if region is not None:
        left, top, right, bottom = region
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("Invalid document portrait region.")
        color = color.crop((round(left * color.width), round(top * color.height),
                            round(right * color.width), round(bottom * color.height)))
    return color, image


def _portrait_check(db, record, document, result, reasons, metadata=None):
    # Supersede the old phase placeholder so result reads current portrait evidence.
    db.execute(sa.delete(DocumentCheck).where(
        DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id,
        DocumentCheck.document_id == document.id, DocumentCheck.check_type == "PORTRAIT"))
    db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                         check_type="PORTRAIT", result=result,
                         evidence_metadata={**(metadata or {}), "reason_codes": list(reasons)}))


def _template_context(record, template_id, source, embedding):
    return (f"biometric/{record.organization_id}/{record.id}/{template_id}/{source}/"
            f"{embedding.model_name}/{embedding.model_version}/{embedding.model_sha256}")


def _recapture_reference(db, record, document, tenant, request_id):
    """Invalidate stale identity evidence while preserving capture attempt history."""
    clear_identity_evidence(db, record, document)
    apply_event(db, record, tenant, Event.REFERENCE_RECAPTURE_REQUIRED, request_id)
    return "CAPTURE_" + requirement_for(record.expected_document_type).sides[0]


def clear_identity_evidence(db, record, document) -> None:
    """Remove photos, extracted fields and templates so a recapture starts clean.
    Checks, signals, assessments, reviews and audit history are kept."""
    for model in (DocumentImage, DocumentField, MRZResult):
        db.execute(sa.delete(model).where(model.organization_id == record.organization_id,
                                         model.session_id == record.id, model.document_id == document.id))
    for model in (SelfieCapture, BiometricTemplate):
        db.execute(sa.delete(model).where(model.organization_id == record.organization_id,
                                         model.session_id == record.id))
    document.processed_at = None
    document.classification_confidence = None
    document.side_classification = {}
    document.document_number_hmac = None
    document.extraction_version = None


def submit_selfie(db: Session, tenant: TenantContext, session_id: UUID, data: bytes, engine, store,
                  template_cipher, policy, limits: SelfieLimits, request_id: UUID,
                  biometric_consent: bool = False,
                  consent_policy_version: str = "BIOMETRIC-CONSENT-2026.10.1") -> dict | JSONResponse:
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
    if record.status != SessionStatus.SELFIE_REQUIRED:
        return _error(409, "SELFIE_NOT_OPEN", f"Selfie capture is not open while the session is {record.status.value}.")
    if not biometric_consent:
        return _error(422, "BIOMETRIC_CONSENT_REQUIRED", "Explicit consent is required to process face biometrics.")
    consent = db.scalar(sa.select(Consent).where(
        Consent.organization_id == record.organization_id, Consent.session_id == record.id,
        Consent.user_id == record.user_id, Consent.scope == "BIOMETRIC_VERIFICATION",
        Consent.policy_version == consent_policy_version, Consent.granted.is_(True), Consent.revoked_at.is_(None)))
    if consent is None:
        db.add(Consent(organization_id=record.organization_id, session_id=record.id, user_id=record.user_id,
                       scope="BIOMETRIC_VERIFICATION", policy_version=consent_policy_version, granted=True))
        _audit(db, record, tenant, request_id, "BIOMETRIC_CONSENT_GRANTED", ("EXPLICIT_BIOMETRIC_CONSENT",),
               policy_version=consent_policy_version)
        db.flush()
    if store is None or template_cipher is None or engine is None:
        return _error(503, "BIOMETRIC_ENGINE_UNAVAILABLE", "Biometric processing is not configured.")
    reason = engine.unavailable_reason()
    if reason:
        _audit(db, record, tenant, request_id, "BIOMETRIC_PROCESSING_UNAVAILABLE", (reason,))
        return _error(503, reason, "Biometric processing is unavailable. Retry later.")
    attempts = db.scalar(sa.select(sa.func.count()).select_from(FaceQualityCheck).where(
        FaceQualityCheck.organization_id == record.organization_id, FaceQualityCheck.session_id == record.id,
        FaceQualityCheck.source == "LIVE_SELFIE")) or 0
    if attempts >= limits.max_attempts:
        _audit(db, record, tenant, request_id, "SELFIE_ATTEMPTS_EXCEEDED", ("SELFIE_ATTEMPTS_EXCEEDED",))
        return _error(429, "SELFIE_ATTEMPTS_EXCEEDED", "Too many selfie attempts. Create a new session.", 0)
    remaining = limits.max_attempts - attempts - 1
    organization = db.get(Organization, record.organization_id)
    delete_after = now + timedelta(hours=organization.template_retention_hours)
    digest = hashlib.sha256(data).hexdigest()
    try:
        capture = decode_capture(data, max_bytes=limits.max_bytes, max_pixels=limits.max_pixels)
    except CaptureRejected as rejected:
        db.add(FaceQualityCheck(organization_id=record.organization_id, session_id=record.id, source="LIVE_SELFIE",
                                result=CheckResult.FAIL, delete_after=delete_after,
                                evidence_metadata={"outcome": "REJECTED_INPUT", "reason_codes": [rejected.reason_code],
                                                   "size_bytes": len(data), "sha256": digest}))
        _audit(db, record, tenant, request_id, "SELFIE_CAPTURE_REJECTED", (rejected.reason_code,))
        return _error(422, rejected.reason_code, str(rejected), remaining)
    live_image = Image.fromarray(capture.pixels).convert("RGB")
    try:
        live_quality = engine.assess(live_image, source="LIVE_SELFIE")
    except FaceEngineUnavailable:
        _audit(db, record, tenant, request_id, "BIOMETRIC_PROCESSING_UNAVAILABLE", ("FACE_ENGINE_UNAVAILABLE",))
        return _error(503, "FACE_ENGINE_UNAVAILABLE", "Face processing is unavailable. Retry later.")
    if not live_quality.accepted or live_quality.face_count != 1 or live_quality.detection is None:
        db.add(FaceQualityCheck(organization_id=record.organization_id, session_id=record.id, source="LIVE_SELFIE",
                                result=CheckResult.FAIL, delete_after=delete_after,
                                evidence_metadata={"outcome": "RECAPTURE", "sha256": digest,
                                                   **_quality_metadata(live_quality)}))
        _audit(db, record, tenant, request_id, "SELFIE_CAPTURE_RECAPTURE", live_quality.reason_codes)
        return _response(record, live_quality, remaining)
    document = db.scalar(sa.select(IdentityDocument).where(
        IdentityDocument.organization_id == record.organization_id, IdentityDocument.session_id == record.id)
        .order_by(IdentityDocument.created_at.desc()).limit(1))
    if document is None or document.processed_at is None or aware(document.delete_after) <= now:
        return _error(503, "REFERENCE_FACE_UNAVAILABLE", "The accepted document portrait is unavailable. Create a new session and capture the document again.",
                      limits.max_attempts - attempts)

    try:
        reference_image, reference_capture = _reference_image(db, record, document, store, limits)
        if reference_image is None:
            _portrait_check(db, record, document, CheckResult.UNAVAILABLE, ("REFERENCE_FACE_UNAVAILABLE",))
            return _error(503, "REFERENCE_FACE_UNAVAILABLE", "The accepted document portrait is unavailable. Create a new session and capture the document again.",
                          limits.max_attempts - attempts)
        reference_quality = engine.assess(reference_image, source="DOCUMENT_PORTRAIT")
        if not reference_quality.accepted or reference_quality.face_count != 1 \
                or reference_quality.detection is None:
            codes = ("REFERENCE_FACE_UNAVAILABLE",)
            detail = _quality_metadata(reference_quality)
            _portrait_check(db, record, document, CheckResult.FAIL, codes, detail)
            db.add(FaceQualityCheck(organization_id=record.organization_id, session_id=record.id,
                                    source="DOCUMENT_PORTRAIT", result=CheckResult.FAIL,
                                    evidence_metadata=detail, delete_after=delete_after))
            _audit(db, record, tenant, request_id, "REFERENCE_FACE_UNAVAILABLE", codes)
            next_step = _recapture_reference(db, record, document, tenant, request_id)
            return _response(record, live_quality, limits.max_attempts - attempts, reasons=codes,
                             instructions=("RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT",), next_step=next_step)
        reference_embedding = engine.embed(reference_image, reference_quality.detection)
        live_embedding = engine.embed(live_image, live_quality.detection)
        comparison = compare_embeddings(reference_embedding, live_embedding, policy)
        reference_payload = serialize_embedding(reference_embedding)
        live_payload = serialize_embedding(live_embedding)
    except (FaceEngineUnavailable, InvalidFaceEmbedding):
        _audit(db, record, tenant, request_id, "BIOMETRIC_PROCESSING_UNAVAILABLE", ("FACE_ENGINE_UNAVAILABLE",))
        return _error(503, "FACE_ENGINE_UNAVAILABLE", "Face processing is unavailable. Retry later.", limits.max_attempts - attempts)
    except Exception:  # No decrypted capture, embedding, exception text, or PII goes into logs.
        _portrait_check(db, record, document, CheckResult.UNAVAILABLE, ("REFERENCE_FACE_UNAVAILABLE",))
        _audit(db, record, tenant, request_id, "REFERENCE_FACE_UNAVAILABLE", ("REFERENCE_FACE_UNAVAILABLE",))
        return _error(503, "REFERENCE_FACE_UNAVAILABLE", "The document portrait could not be processed. Retry later.",
                      limits.max_attempts - attempts)

    now = datetime.now(timezone.utc)
    if is_expired(record, now):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
        return _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.")
    if aware(reference_capture.delete_after) <= now or aware(document.delete_after) <= now:
        return _error(503, "REFERENCE_FACE_UNAVAILABLE", "The accepted document portrait has expired. Create a new session and capture the document again.",
                      limits.max_attempts - attempts)

    # Seal everything before changing session progress; failed infrastructure is retryable.
    reference_id, live_id, capture_id = uuid4(), uuid4(), uuid4()
    try:
        reference_sealed, reference_key = template_cipher.seal(
            reference_payload, _template_context(record, reference_id, "DOCUMENT_PORTRAIT", reference_embedding))
        live_sealed, live_key = template_cipher.seal(
            live_payload, _template_context(record, live_id, "LIVE_SELFIE", live_embedding))
        stored = store.put(record.organization_id, record.id, capture_id, data)
    except Exception:
        _audit(db, record, tenant, request_id, "BIOMETRIC_PROCESSING_UNAVAILABLE", ("BIOMETRIC_STORAGE_UNAVAILABLE",))
        return _error(503, "BIOMETRIC_STORAGE_UNAVAILABLE", "Biometric storage is unavailable. Retry later.",
                      limits.max_attempts - attempts)
    delete_after = min(now + timedelta(hours=organization.template_retention_hours), aware(document.delete_after))
    for template_id, source, embedding, sealed, key in (
        (reference_id, "DOCUMENT_PORTRAIT", reference_embedding, reference_sealed, reference_key),
        (live_id, "LIVE_SELFIE", live_embedding, live_sealed, live_key),
    ):
        db.add(BiometricTemplate(id=template_id, organization_id=record.organization_id, session_id=record.id,
                                 source=source, model_name=embedding.model_name, model_version=embedding.model_version,
                                 document_id=document.id if source == "DOCUMENT_PORTRAIT" else None,
                                 model_sha256=embedding.model_sha256, embedding_dimension=len(embedding.vector),
                                 template_ciphertext=sealed, key_version=key,
                                 delete_after=delete_after))
    height, width = capture.pixels.shape[:2]
    db.add(SelfieCapture(id=capture_id, organization_id=record.organization_id, session_id=record.id,
                         encrypted_object_ref=stored.ref, key_version=stored.key_version, media_type=capture.media_type,
                         sha256=stored.sha256, quality_scores=live_quality.scores,
                         quality_policy_version=live_quality.policy_version,
                         delete_after=now + timedelta(hours=organization.capture_retention_hours)))
    for source, assessment in (("DOCUMENT_PORTRAIT", reference_quality), ("LIVE_SELFIE", live_quality)):
        db.add(FaceQualityCheck(organization_id=record.organization_id, session_id=record.id, source=source,
                                result=CheckResult.REVIEW if assessment.unverified_checks else CheckResult.PASS,
                                delete_after=delete_after,
                                evidence_metadata={"outcome": "ACCEPTED", **_quality_metadata(assessment)}))
    db.flush()
    db.add(FaceComparison(organization_id=record.organization_id, session_id=record.id,
                          reference_template_id=reference_id, live_template_id=live_id,
                          model_name=comparison.model_name, model_version=comparison.model_version,
                          model_sha256=comparison.model_sha256, threshold_policy_version=comparison.threshold_policy_version,
                          comparison_score=comparison.score, comparison_metric="COSINE_SIMILARITY", result=comparison.decision,
                          evidence_metadata={"reason_codes": list(comparison.reason_codes), "metric": "COSINE_SIMILARITY",
                                             "calibrated": comparison.calibrated,
                                             "calibration_reference": policy.calibration_reference}))
    _portrait_check(db, record, document, CheckResult.REVIEW if reference_quality.unverified_checks else CheckResult.PASS,
                    ("DOCUMENT_PORTRAIT_EXTRACTED", *reference_quality.reason_codes),
                    _quality_metadata(reference_quality))
    _audit(db, record, tenant, request_id, "FACE_COMPARISON_RECORDED", comparison.reason_codes,
           model_name=comparison.model_name, model_version=comparison.model_version,
           policy_version=comparison.threshold_policy_version, result=CheckResult(comparison.decision).value,
           consent="EXPLICIT", width=width, height=height)
    apply_event(db, record, tenant, Event.SELFIE_ACCEPTED, request_id)
    db.flush()
    next_step = "CAPTURE_LIVENESS" if record.status == SessionStatus.LIVENESS_REQUIRED else "AWAIT_ASSESSMENT"
    reasons = tuple(dict.fromkeys((*live_quality.reason_codes, *reference_quality.reason_codes)))
    return _response(record, live_quality, remaining, accepted=True, reasons=reasons,
                     instructions=(), next_step=next_step,
                     # The capturing device never learns the score or the match result: that would let
                     # an attacker tune selfies against the comparison. The decision comes from the risk engine.
                     comparison={"recorded": True, "metric": "COSINE_SIMILARITY",
                                 "policy_version": comparison.threshold_policy_version,
                                 "model_name": comparison.model_name, "model_version": comparison.model_version,
                                 "calibrated": comparison.calibrated})
