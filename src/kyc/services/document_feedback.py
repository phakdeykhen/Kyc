"""Applicant-safe document progress derived from persisted checks, never OCR values."""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.schemas import DocumentCaptureFeedback
from kyc.db.models import AuditLog, DocumentCheck, DocumentImage, KYCSession
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import SessionStatus


RECAPTURE_INSTRUCTIONS = {
    "DOCUMENT_NOT_RECOGNIZED": ("SHOW_ALL_DOCUMENT_EDGES", "REDUCE_GLARE", "HOLD_STILL"),
    "DOCUMENT_TYPE_MISMATCH": ("CAPTURE_CORRECT_DOCUMENT",),
    "CRITICAL_FIELD_UNREADABLE": ("USE_HIGHER_RESOLUTION", "REDUCE_GLARE", "HOLD_STILL"),
    "FRONT_SIDE_EXPECTED": ("CAPTURE_FRONT",),
    "BACK_SIDE_EXPECTED": ("CAPTURE_BACK",),
    "DATA_PAGE_SIDE_EXPECTED": ("CAPTURE_DATA_PAGE",),
    "CAPTURES_INCOMPLETE": ("RETAKE_PHOTO",),
    "DOCUMENT_PORTRAIT_UNUSABLE": ("RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT",),
    "DOCUMENT_RECAPTURE_REQUESTED": ("RETAKE_PHOTO",),
}
QUALITY_INSTRUCTIONS = {
    "EMPTY_FILE": ("RETAKE_PHOTO",), "FILE_TOO_LARGE": ("USE_SMALLER_FILE",),
    "UNSUPPORTED_FORMAT": ("USE_SUPPORTED_IMAGE_FORMAT",), "IMAGE_DIMENSIONS_TOO_LARGE": ("USE_SMALLER_FILE",),
    "ANIMATED_IMAGE": ("USE_STILL_IMAGE",), "UNREADABLE_IMAGE": ("RETAKE_PHOTO",),
    "RESOLUTION_TOO_LOW": ("USE_HIGHER_RESOLUTION",), "DOCUMENT_NOT_DETECTED": ("USE_CONTRASTING_BACKGROUND",),
    "MULTIPLE_DOCUMENTS": ("ONE_DOCUMENT_ONLY",), "DOCUMENT_CROPPED": ("SHOW_ALL_DOCUMENT_EDGES", "MOVE_BACK"),
    "DOCUMENT_TOO_FAR": ("MOVE_CLOSER",), "DOCUMENT_TOO_CLOSE": ("MOVE_BACK",),
    "PERSPECTIVE_DISTORTED": ("ALIGN_DOCUMENT",), "DOCUMENT_SHAPE_UNEXPECTED": ("ALIGN_DOCUMENT",),
    "TOO_DARK": ("MORE_LIGHT",), "TOO_BRIGHT": ("LESS_LIGHT",), "IMAGE_BLURRY": ("HOLD_STILL",),
    "GLARE_DETECTED": ("REDUCE_GLARE",), "SHADOW_DETECTED": ("AVOID_SHADOW",),
    "OVERALL_QUALITY_LOW": ("RETAKE_PHOTO",), "SAME_IMAGE_FOR_MULTIPLE_SIDES": ("CAPTURE_OTHER_SIDE",),
}
TECHNICAL_REASONS = frozenset({"DOCUMENT_PROCESSING_FAILED", "OCR_ENGINE_UNAVAILABLE",
                               "DOCUMENT_PROCESSING_UNAVAILABLE", "DOCUMENT_ADAPTER_UNAVAILABLE"})
SAFE_REASONS = frozenset(RECAPTURE_INSTRUCTIONS) | frozenset(QUALITY_INSTRUCTIONS) | TECHNICAL_REASONS
SAFE_INSTRUCTIONS = frozenset(instruction for values in (*RECAPTURE_INSTRUCTIONS.values(), *QUALITY_INSTRUCTIONS.values())
                              for instruction in values) | {"RETRY_DOCUMENT_PROCESSING", "CONTACT_SUPPORT"}
PROGRESS_ACTIONS = ("DOCUMENT_SUBMITTED", "DOCUMENT_ACCEPTED", "RECAPTURE_REQUIRED", "REFERENCE_RECAPTURE_REQUIRED",
                    "DOCUMENT_PROCESSING_FAILED", "OCR_ENGINE_UNAVAILABLE", "CAPTURE_STORAGE_NOT_CONFIGURED",
                    "PII_ENCRYPTION_NOT_CONFIGURED", "DOCUMENT_ADAPTER_UNAVAILABLE", "DOCUMENT_PROCESSING_RETRY_REQUESTED")


def processing_problem(reason: str) -> tuple[str, tuple[str, ...]]:
    safe = reason if reason in TECHNICAL_REASONS else "DOCUMENT_PROCESSING_UNAVAILABLE"
    return safe, (("CONTACT_SUPPORT",) if safe == "DOCUMENT_ADAPTER_UNAVAILABLE" else ("RETRY_DOCUMENT_PROCESSING",))


def recapture_instructions(reasons) -> list[str]:
    return list(dict.fromkeys(instruction for reason in reasons for instruction in RECAPTURE_INSTRUCTIONS.get(reason, ())))


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def capture_feedback(db: Session, record: KYCSession, max_attempts: int) -> DocumentCaptureFeedback:
    """Bounded, tenant-scoped reads use existing session/type and audit indexes.

    A recapture cause belongs to the current session version and remains visible
    while replacement sides arrive. DOCUMENT_SUBMITTED starts the next version,
    so failures from a previous pair cannot explain a new in-progress pair.
    """
    scope = (DocumentCheck.organization_id == record.organization_id, DocumentCheck.session_id == record.id)
    attempts = db.scalar(sa.select(sa.func.count()).select_from(DocumentCheck).where(*scope,
        DocumentCheck.check_type == "CAPTURE_QUALITY")) or 0
    accepted = set(db.scalars(sa.select(DocumentImage.side).where(
        DocumentImage.organization_id == record.organization_id, DocumentImage.session_id == record.id)))
    sides = {side: "ACCEPTED" if side in accepted else "REQUIRED"
             for side in requirement_for(record.expected_document_type).sides}
    missing = [side for side, value in sides.items() if value == "REQUIRED"]
    remaining = max(0, max_attempts - attempts)
    capture_open = record.status in (SessionStatus.CREATED, SessionStatus.DOCUMENT_REQUIRED)
    state = "READY" if capture_open else "PROCESSING" if record.status == SessionStatus.DOCUMENT_PROCESSING else "COMPLETE"
    reasons, instructions = [], []
    retry_processing = False
    if capture_open or record.status == SessionStatus.DOCUMENT_PROCESSING:
        processing = db.scalar(sa.select(DocumentCheck).where(*scope, DocumentCheck.check_type == "DOCUMENT_PROCESSING")
            .order_by(DocumentCheck.created_at.desc(), DocumentCheck.id.desc()).limit(1))
        event = db.scalar(sa.select(AuditLog).where(AuditLog.organization_id == record.organization_id,
            AuditLog.session_id == record.id, AuditLog.action.in_(PROGRESS_ACTIONS))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))
        metadata = processing.evidence_metadata if processing else {}
        current = metadata.get("session_version") == record.version
        # Existing sessions predate the version marker. Their transition audit
        # distinguishes OCR recapture from later portrait/reviewer recaptures.
        if not current and "session_version" not in metadata and event is not None:
            current = (capture_open and event.action == "RECAPTURE_REQUIRED"
                       and event.from_status == SessionStatus.DOCUMENT_PROCESSING.value)
        if current and metadata.get("outcome") == "RECAPTURE" and capture_open:
            reasons.extend(code for code in metadata.get("reason_codes", []) if code in RECAPTURE_INSTRUCTIONS)
            instructions.extend(recapture_instructions(reasons))
            state = "RECAPTURE"
        if record.status == SessionStatus.DOCUMENT_PROCESSING:
            retry_requested = event is not None and event.action == "DOCUMENT_PROCESSING_RETRY_REQUESTED"
            if current and metadata.get("outcome") == "TECHNICAL_ERROR" and not (retry_requested and
                    _aware(event.created_at) >= _aware(processing.created_at)):
                reasons.extend(code for code in metadata.get("reason_codes", []) if code in TECHNICAL_REASONS)
                instructions.extend(code for code in metadata.get("instructions", []) if code in SAFE_INSTRUCTIONS)
            elif event is not None and not retry_requested and _aware(event.created_at) >= _aware(record.updated_at):
                legacy = {"DOCUMENT_PROCESSING_FAILED": "DOCUMENT_PROCESSING_FAILED",
                          "OCR_ENGINE_UNAVAILABLE": "OCR_ENGINE_UNAVAILABLE",
                          "CAPTURE_STORAGE_NOT_CONFIGURED": "DOCUMENT_PROCESSING_UNAVAILABLE",
                          "PII_ENCRYPTION_NOT_CONFIGURED": "DOCUMENT_PROCESSING_UNAVAILABLE",
                          "DOCUMENT_ADAPTER_UNAVAILABLE": "DOCUMENT_ADAPTER_UNAVAILABLE"}.get(event.action)
                if legacy:
                    safe, hints = processing_problem(legacy)
                    reasons.append(safe)
                    instructions.extend(hints)
            if reasons:
                state = "TECHNICAL_ERROR"
                retry_processing = "DOCUMENT_ADAPTER_UNAVAILABLE" not in reasons
        if capture_open:
            if event is not None and event.action == "REFERENCE_RECAPTURE_REQUIRED":
                reasons.append("DOCUMENT_PORTRAIT_UNUSABLE")
                instructions.extend(RECAPTURE_INSTRUCTIONS["DOCUMENT_PORTRAIT_UNUSABLE"])
                state = "RECAPTURE"
            quality = db.scalar(sa.select(DocumentCheck).where(*scope, DocumentCheck.check_type == "CAPTURE_QUALITY")
                .order_by(DocumentCheck.created_at.desc(), DocumentCheck.id.desc()).limit(1))
            if quality is not None and quality.evidence_metadata.get("outcome") in ("RECAPTURE", "REJECTED_INPUT"):
                for code in quality.evidence_metadata.get("reason_codes", []):
                    if code in QUALITY_INSTRUCTIONS:
                        reasons.append(code)
                        instructions.extend(QUALITY_INSTRUCTIONS[code])
                state = "RECAPTURE"
            if remaining == 0:
                reasons.append("CAPTURE_ATTEMPTS_EXCEEDED")
                instructions.append("CONTACT_SUPPORT")
    return DocumentCaptureFeedback(state=state, sides=sides, required_sides=missing,
        reason_codes=list(dict.fromkeys(reasons)), instructions=list(dict.fromkeys(instructions)),
        attempts_remaining=remaining, retry_allowed=capture_open and remaining > 0,
        retry_processing_allowed=retry_processing)
