"""Active liveness orchestration: challenge issuance and frame assessment.

Liveness frames are biometric data; they are assessed in memory and never stored. Only
per-step outcomes, geometry metrics, frame hashes' count and the result are retained.
A final outcome (PASS/REVIEW/FAIL) records the evidence and moves the session on; the
decision itself belongs to the risk engine (Phase 13).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.biometrics.embeddings import compare_embeddings, deserialize_embedding, serialize_embedding
from kyc.biometrics.types import FaceEngineUnavailable, InvalidFaceEmbedding
from kyc.biometrics.quality import assess_face_quality
from kyc.core.errors import error_response
from kyc.db.models import (AuditLog, BiometricTemplate, FaceComparison, FaceQualityCheck, IdentityDocument,
                           KYCSession, LivenessChallenge, LivenessCheck, Organization)
from kyc.domain.enums import CheckResult, SessionStatus, VerificationLevel
from kyc.domain.state_machine import Event
from kyc.engines.capture_quality import CaptureRejected, decode_capture
from kyc.liveness import challenge as challenges
from kyc.liveness.active import COVERAGE, ActiveLivenessPolicy, assess, guide
from kyc.liveness.geometry import pose
from kyc.services.biometrics import _quality_metadata, _template_context
from kyc.services.sessions import apply_event, aware, is_expired

METHOD = "ACTIVE_LIVENESS"
MODEL_NAME = "KYC active geometry (YuNet landmarks + SFace continuity)"
LIVENESS_LEVELS = {VerificationLevel.DOCUMENT_FACE_LIVENESS, VerificationLevel.DOCUMENT_FACE_LIVENESS_NFC}


@dataclass(frozen=True)
class LivenessLimits:
    challenge_ttl_seconds: int
    max_attempts: int
    max_frame_bytes: int
    max_frame_pixels: int


def _error(code: int, reason: str, detail: str, remaining: int | None = None) -> JSONResponse:
    return error_response(code, reason, detail, attempts_remaining=remaining)


def _audit(db, record, tenant, request_id, action, reasons=(), **metadata):
    db.add(AuditLog(organization_id=record.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action=action, request_id=request_id, from_status=record.status.value, to_status=record.status.value,
                    reason_codes=list(reasons), event_metadata={"version": record.version, **metadata}))


def _session(db: Session, tenant: TenantContext, session_id: UUID, request_id: UUID):
    record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                       KYCSession.organization_id == tenant.organization_id).with_for_update())
    if record is None:
        raise HTTPException(404, detail="Session not found.")
    if is_expired(record):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
        return record, _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.")
    if record.verification_level not in LIVENESS_LEVELS:
        return record, _error(409, "LIVENESS_NOT_REQUIRED", "This verification level has no liveness step.")
    if record.status != SessionStatus.LIVENESS_REQUIRED:
        return record, _error(409, "LIVENESS_NOT_OPEN", f"Liveness is not open while the session is {record.status.value}.")
    return record, None


def _attempts(db, record) -> int:
    return db.scalar(sa.select(sa.func.count()).select_from(LivenessChallenge).where(
        LivenessChallenge.organization_id == record.organization_id, LivenessChallenge.session_id == record.id)) or 0


def issue_challenge(db: Session, tenant: TenantContext, session_id: UUID, limits: LivenessLimits,
                    policy: ActiveLivenessPolicy, request_id: UUID) -> dict | JSONResponse:
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    attempts = _attempts(db, record)
    if attempts >= limits.max_attempts:
        _audit(db, record, tenant, request_id, "LIVENESS_ATTEMPTS_EXCEEDED", ("LIVENESS_ATTEMPTS_EXCEEDED",))
        return _error(429, "LIVENESS_ATTEMPTS_EXCEEDED", "Too many liveness attempts. Create a new session.", 0)
    now = datetime.now(timezone.utc)
    # Only the newest challenge is valid; earlier unused ones are voided.
    db.execute(sa.update(LivenessChallenge).where(LivenessChallenge.organization_id == record.organization_id,
                                                  LivenessChallenge.session_id == record.id,
                                                  LivenessChallenge.used_at.is_(None)).values(used_at=now))
    issued = challenges.issue()
    item = LivenessChallenge(id=uuid4(), organization_id=record.organization_id, session_id=record.id,
                             steps=list(issued.steps), nonce_hash=issued.nonce_hash, policy_version=policy.version,
                             created_at=now, expires_at=now + timedelta(seconds=limits.challenge_ttl_seconds))
    db.add(item)
    _audit(db, record, tenant, request_id, "LIVENESS_CHALLENGE_ISSUED", (), steps=len(issued.steps))
    db.flush()
    return {"session_id": record.id, "challenge_id": item.id, "nonce": issued.nonce,
            "steps": [{"index": index, "step": step, "instruction": challenges.INSTRUCTIONS[step]}
                      for index, step in enumerate(issued.steps)],
            "expires_at": item.expires_at,
            "frames": {"min": max(policy.min_frames, len(item.steps) * policy.stable_frames_per_step),
                       "max": policy.max_frames,
                       "per_step": f"{policy.stable_frames_per_step}-3 frames, tagged with the step index"},
            "attempts_remaining": limits.max_attempts - attempts - 1}


def _open_challenge(db, record, challenge_id: UUID, nonce: str, now: datetime,
                    remaining: int | None) -> tuple[LivenessChallenge | None, JSONResponse | None]:
    """The session's challenge, if the nonce matches and it is still usable; read-only."""
    item = db.scalar(sa.select(LivenessChallenge).where(
        LivenessChallenge.id == challenge_id, LivenessChallenge.organization_id == record.organization_id,
        LivenessChallenge.session_id == record.id))
    if item is None or not hmac.compare_digest(item.nonce_hash, challenges.nonce_hash(nonce or "")):
        return None, _error(409, "CHALLENGE_INVALID", "Unknown challenge or nonce. Request a new challenge.", remaining)
    if item.used_at is not None:
        return None, _error(409, "CHALLENGE_ALREADY_USED", "This challenge was already used. Request a new one.", remaining)
    if aware(item.expires_at) <= now:
        return None, _error(409, "CHALLENGE_EXPIRED", "The challenge expired. Request a new one.", remaining)
    return item, None


def _single_face(engine, data: bytes, limits: LivenessLimits):
    """(face status, pose or None). Face status: OK, NO_FACE, MULTIPLE_FACES or UNCLEAR."""
    capture = decode_capture(data, max_bytes=limits.max_frame_bytes, max_pixels=limits.max_frame_pixels)
    faces = engine.detect(Image.fromarray(capture.pixels))
    if len(faces) != 1:
        return ("MULTIPLE_FACES" if faces else "NO_FACE"), None
    try:
        return "OK", pose(faces[0].landmarks)
    except ValueError:
        return "UNCLEAR", None


def position_face(db: Session, tenant: TenantContext, session_id: UUID, frame: bytes, engine,
                  policy: ActiveLivenessPolicy, limits: LivenessLimits, request_id: UUID) -> dict | JSONResponse:
    """Check positioning before issuing a challenge. No attempt or biometric evidence is stored."""
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    remaining = max(0, limits.max_attempts - _attempts(db, record))
    if remaining == 0:
        return _error(429, "LIVENESS_ATTEMPTS_EXCEEDED", "Too many liveness attempts. Create a new session.", 0)
    reason = engine.unavailable_reason() if engine is not None else "FACE_MODELS_UNAVAILABLE"
    if reason:
        return _error(503, reason, "Face checking is unavailable. Retry later.", remaining)
    try:
        capture = decode_capture(frame, max_bytes=limits.max_frame_bytes, max_pixels=limits.max_frame_pixels)
        image = Image.fromarray(capture.pixels)
        faces = engine.detect(image)
        quality = assess_face_quality(image, faces)
        face = "OK" if len(faces) == 1 else "MULTIPLE_FACES" if faces else "NO_FACE"
        instructions = list(quality.instructions)
        ready = quality.accepted
        if ready and abs(pose(faces[0].landmarks).a) > policy.movement / 2:
            ready, instructions = False, ["LOOK_STRAIGHT"]
    except CaptureRejected as rejected:
        return _error(422, rejected.reason_code, str(rejected), remaining)
    except ValueError:
        face, ready, instructions = "UNCLEAR", False, ["FACE_CAMERA"]
    except FaceEngineUnavailable:
        return _error(503, "FACE_MODELS_UNAVAILABLE", "Face checking is unavailable. Retry later.", remaining)
    return {"face": face, "state": "READY" if ready else "POSITIONING", "instructions": instructions,
            "attempts_remaining": remaining}


def guide_step(db: Session, tenant: TenantContext, session_id: UUID, challenge_id: UUID, nonce: str, step_index: int,
               frame: bytes, baseline: bytes | None, engine, policy: ActiveLivenessPolicy, limits: LivenessLimits,
               request_id: UUID) -> dict | JSONResponse:
    """Live feedback for one step of an open challenge, from one frame (and the person's baseline frame).

    Advisory only: it neither uses the challenge nor records evidence, and frames are not kept.
    The submitted frames are judged again by `submit_liveness`.
    """
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    item, problem = _open_challenge(db, record, challenge_id, nonce, datetime.now(timezone.utc), None)
    if problem:
        return problem
    if not 0 <= step_index < len(item.steps):
        return _error(422, "FRAME_STEP_INVALID", "The step index is not part of this challenge.")
    reason = engine.unavailable_reason() if engine is not None else "FACE_MODELS_UNAVAILABLE"
    if reason:
        return _error(503, reason, "Liveness checking is unavailable. Retry later.")
    step = item.steps[step_index]
    try:
        face, sample = _single_face(engine, frame, limits)
        if face != "OK" or baseline is None:
            return {"step": step, "face": face, "state": None, "progress": 0.0}
        base_face, base_sample = _single_face(engine, baseline, limits)
    except CaptureRejected as rejected:
        return _error(422, rejected.reason_code, str(rejected))
    except FaceEngineUnavailable:
        return _error(503, "FACE_MODELS_UNAVAILABLE", "Liveness checking is unavailable. Retry later.")
    if base_face != "OK":
        return _error(422, "BASELINE_UNUSABLE", "The baseline frame must show exactly one clear face.")
    advice = guide(base_sample, sample, step, policy)
    return {"step": step, "face": face, "state": advice.state, "progress": advice.progress}


def _selfie_template(db, record):
    return db.scalar(sa.select(BiometricTemplate).where(
        BiometricTemplate.organization_id == record.organization_id, BiometricTemplate.session_id == record.id,
        BiometricTemplate.source == "LIVE_SELFIE", BiometricTemplate.delete_after > datetime.now(timezone.utc))
        .order_by(BiometricTemplate.created_at.desc()).limit(1))


def _selfie_reference(db, record, cipher):
    template = _selfie_template(db, record)
    if template is None or cipher is None:
        return None
    payload = cipher.open(template.template_ciphertext, template.key_version,
                          _template_context(record, template.id, "LIVE_SELFIE", template))
    return deserialize_embedding(payload)


def _record_live_face(db, record, tenant, request_id, item, outcome, cipher, match_policy):
    """Compare the best accepted challenge frame to the current encrypted document portrait.

    Retain an encrypted embedding and quality/provenance only; the image stays in memory.
    All preparation finishes before any rows are added, so failures leave no partial evidence.
    """
    now = datetime.now(timezone.utc)
    portrait = db.scalar(sa.select(BiometricTemplate).join(IdentityDocument, sa.and_(
        BiometricTemplate.document_id == IdentityDocument.id,
        BiometricTemplate.organization_id == IdentityDocument.organization_id,
        BiometricTemplate.session_id == IdentityDocument.session_id)).where(
        BiometricTemplate.organization_id == record.organization_id, BiometricTemplate.session_id == record.id,
        BiometricTemplate.source == "DOCUMENT_PORTRAIT", BiometricTemplate.delete_after > now,
        IdentityDocument.processed_at.is_not(None), IdentityDocument.delete_after > now,
        BiometricTemplate.created_at >= IdentityDocument.processed_at)
        .order_by(BiometricTemplate.created_at.desc()).limit(1))
    selfie = _selfie_template(db, record)
    if portrait is None or selfie is None or cipher is None:
        return "REFERENCE_FACE_UNAVAILABLE"
    live = outcome.live_embedding
    quality = outcome.live_quality
    payload = cipher.open(portrait.template_ciphertext, portrait.key_version,
                          _template_context(record, portrait.id, "DOCUMENT_PORTRAIT", portrait))
    comparison = compare_embeddings(deserialize_embedding(payload), live, match_policy)
    template_id = uuid4()
    sealed, key = cipher.seal(serialize_embedding(live), _template_context(record, template_id, "LIVE_SELFIE", live))
    organization = db.get(Organization, record.organization_id)
    delete_after = min(now + timedelta(hours=organization.template_retention_hours),
                       aware(portrait.delete_after), aware(selfie.delete_after))
    provenance = {"live_capture_method": METHOD, "challenge_id": str(item.id),
                  "selected_frame_index": outcome.metrics["selected_frame_index"],
                  "frame_selection_policy": outcome.metrics["frame_selection_policy"]}
    db.add(BiometricTemplate(id=template_id, organization_id=record.organization_id, session_id=record.id,
        source="LIVE_SELFIE", model_name=live.model_name, model_version=live.model_version,
        model_sha256=live.model_sha256, embedding_dimension=live.dimension, template_ciphertext=sealed,
        key_version=key, delete_after=delete_after, created_at=now))
    db.flush()
    db.add(FaceQualityCheck(organization_id=record.organization_id, session_id=record.id, source="LIVE_SELFIE",
        result=CheckResult.REVIEW if quality.unverified_checks else CheckResult.PASS, delete_after=delete_after,
        evidence_metadata={"outcome": "ACCEPTED", **_quality_metadata(quality), **provenance}, created_at=now))
    db.add(FaceComparison(organization_id=record.organization_id, session_id=record.id,
        reference_template_id=portrait.id, live_template_id=template_id, model_name=comparison.model_name,
        model_version=comparison.model_version, model_sha256=comparison.model_sha256,
        threshold_policy_version=comparison.threshold_policy_version, comparison_score=comparison.score,
        comparison_metric=comparison.metric, result=comparison.decision, created_at=now,
        evidence_metadata={"reason_codes": list(comparison.reason_codes), "calibrated": comparison.calibrated,
                           "calibration_reference": match_policy.calibration_reference, **provenance}))
    _audit(db, record, tenant, request_id, "LIVENESS_FACE_COMPARISON_RECORDED", comparison.reason_codes,
           result=comparison.decision, policy_version=comparison.threshold_policy_version, **provenance)
    return None


def submit_liveness(db: Session, tenant: TenantContext, session_id: UUID, challenge_id: UUID, nonce: str,
                    frames: list[bytes], frame_steps: list[int], engine, cipher, match_policy,
                    policy: ActiveLivenessPolicy, limits: LivenessLimits, request_id: UUID) -> dict | JSONResponse:
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    remaining = max(0, limits.max_attempts - _attempts(db, record))
    item = db.scalar(sa.select(LivenessChallenge).where(
        LivenessChallenge.id == challenge_id, LivenessChallenge.organization_id == record.organization_id,
        LivenessChallenge.session_id == record.id).with_for_update())
    now = datetime.now(timezone.utc)
    if item is None or not hmac.compare_digest(item.nonce_hash, challenges.nonce_hash(nonce or "")):
        _audit(db, record, tenant, request_id, "LIVENESS_CHALLENGE_REJECTED", ("CHALLENGE_INVALID",))
        return _error(409, "CHALLENGE_INVALID", "Unknown challenge or nonce. Request a new challenge.", remaining)
    if item.used_at is not None:
        _audit(db, record, tenant, request_id, "LIVENESS_CHALLENGE_REJECTED", ("CHALLENGE_ALREADY_USED",))
        return _error(409, "CHALLENGE_ALREADY_USED", "This challenge was already used. Request a new one.", remaining)
    item.used_at = now  # Single use for evidence outcomes; technical failures below remain retryable.
    if aware(item.expires_at) <= now:
        _audit(db, record, tenant, request_id, "LIVENESS_CHALLENGE_REJECTED", ("CHALLENGE_EXPIRED",))
        return _error(409, "CHALLENGE_EXPIRED", "The challenge expired. Request a new one.", remaining)
    if len(frames) != len(frame_steps):
        return _error(422, "FRAME_STEPS_MISMATCH", "Give one step index per frame.", remaining)
    reason = engine.unavailable_reason() if engine is not None else "FACE_MODELS_UNAVAILABLE"
    if reason:
        item.used_at = None  # Technical unavailability can be retried within this challenge's TTL.
        _audit(db, record, tenant, request_id, "LIVENESS_UNAVAILABLE", (reason,))
        return _error(503, reason, "Liveness checking is unavailable. Retry later.", remaining)

    decoded = []
    for index, data in zip(frame_steps, frames):
        try:
            capture = decode_capture(data, max_bytes=limits.max_frame_bytes, max_pixels=limits.max_frame_pixels)
        except CaptureRejected as rejected:
            return _error(422, rejected.reason_code, str(rejected), remaining)
        decoded.append((index, Image.fromarray(capture.pixels), hashlib.sha256(data).hexdigest()))
    try:
        reference = _selfie_reference(db, record, cipher)
    except (InvalidFaceEmbedding, InvalidTag, ValueError):
        item.used_at = None
        return _error(503, "BIOMETRIC_REFERENCE_UNAVAILABLE", "The biometric reference is unavailable. Retry later.", remaining)
    try:
        outcome = assess(decoded, tuple(item.steps), engine, reference, match_policy, policy)
    except (FaceEngineUnavailable, InvalidFaceEmbedding):
        item.used_at = None
        return _error(503, "FACE_ENGINE_UNAVAILABLE", "Liveness checking is unavailable. Retry later.", remaining)

    now = datetime.now(timezone.utc)
    if is_expired(record, now):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
        return _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.", remaining)
    if aware(item.expires_at) <= now:
        return _error(409, "CHALLENGE_EXPIRED", "The challenge expired. Request a new one.", remaining)
    if outcome.live_embedding is not None:
        try:
            reason = _record_live_face(db, record, tenant, request_id, item, outcome, cipher, match_policy)
        except (InvalidFaceEmbedding, InvalidTag, ValueError, OSError):
            reason = "BIOMETRIC_STORAGE_UNAVAILABLE"
        if reason:
            item.used_at = None
            _audit(db, record, tenant, request_id, "LIVENESS_UNAVAILABLE", (reason,))
            return _error(503, reason, "The live face comparison is unavailable. Retry later.", remaining)

    evidence = {"reason_codes": outcome.reason_codes, "steps": outcome.steps, "metrics": outcome.metrics,
                "policy_version": policy.version, "calibrated": policy.calibrated, "coverage": COVERAGE,
                "retryable": outcome.retryable, "frames_retained": False}
    db.add(LivenessCheck(organization_id=record.organization_id, session_id=record.id, method=METHOD,
                         result=outcome.result, score=outcome.score, attack_type=outcome.attack_type,
                         model_name=MODEL_NAME, model_version=f"{policy.version}; {engine.detector_name} {engine.detector_version}",
                         challenge_hash=item.nonce_hash, evidence_metadata=evidence))
    retry_allowed = outcome.retryable and remaining > 0
    if retry_allowed:
        _audit(db, record, tenant, request_id, "LIVENESS_RETRY_REQUIRED", outcome.reason_codes)
    else:
        _audit(db, record, tenant, request_id, "LIVENESS_RECORDED", outcome.reason_codes, result=outcome.result.value,
               attack_type=outcome.attack_type, policy_version=policy.version)
        apply_event(db, record, tenant, Event.LIVENESS_ACCEPTED, request_id)
    db.flush()
    return {"session_id": record.id, "status": record.status, "method": METHOD,
            "result": outcome.result.value, "score": outcome.score, "attack_type": outcome.attack_type,
            "reason_codes": outcome.reason_codes, "steps": outcome.steps, "instructions": outcome.instructions,
            "retry_allowed": outcome.retryable and remaining > 0, "attempts_remaining": remaining,
            "policy_version": policy.version, "calibrated": policy.calibrated, "coverage": COVERAGE}
