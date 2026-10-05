from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile, status

from kyc.api.dependencies import Database, Tenant
from kyc.api.schemas import COUNTRY_CODES, CaptureError, CaptureResponse, DocumentSide, SessionCreate, SessionResponse, SessionResult, SelfieResponse
from kyc.documents.adapters import adapter_for
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import DocumentType
from kyc.services.captures import CaptureLimits, submit_capture
from kyc.services.biometrics import SelfieLimits, submit_selfie
from kyc.services.liveness import LivenessLimits, issue_challenge, submit_liveness
from kyc.services.results import build_result
from kyc.services.sessions import create_session, get_session, respond

router = APIRouter(prefix="/v1")

CAPTURE_RESPONSES = {
    409: {"model": CaptureError, "description": "Session expired, closed, or not accepting document capture."},
    413: {"description": "Upload exceeds the size limit."},
    422: {"description": "Unreadable or unsupported image, or a side this document type does not have."},
    429: {"model": CaptureError, "description": "Capture attempt limit reached for this session."},
    503: {"description": "Capture storage is not configured."},
}


@router.post("/kyc/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def start_session(body: SessionCreate, request: Request, tenant: Tenant, db: Database):
    record = create_session(db, tenant, body, request.app.state.settings.session_ttl_seconds, request.state.request_id)
    return respond(record)


@router.get("/kyc/{session_id}", response_model=SessionResponse)
def read_session(session_id: UUID, request: Request, tenant: Tenant, db: Database):
    return respond(get_session(db, tenant, session_id, request.state.request_id))


@router.get("/kyc/{session_id}/result", response_model=SessionResult)
def read_result(session_id: UUID, request: Request, tenant: Tenant, db: Database):
    record = get_session(db, tenant, session_id, request.state.request_id)
    # Extracted evidence only; the PASS/REVIEW/FAIL decision belongs to the risk engine (Phase 13).
    return build_result(db, record, request.app.state.field_cipher)


def _process_after_commit(state, organization_id: UUID, session_id: UUID, request_id: UUID) -> None:
    state.document_processor.process(organization_id, session_id, request_id)


def _capture(session_id: UUID, side: str, file: UploadFile, request: Request, tenant: Tenant, db: Database,
             background: BackgroundTasks):
    state = request.app.state
    if state.capture_store is None:
        raise HTTPException(503, detail="Capture storage is not configured.")
    settings = state.settings
    data = file.file.read(settings.max_capture_bytes + 1)
    limits = CaptureLimits(settings.max_capture_bytes, settings.max_capture_pixels, settings.max_capture_attempts)
    outcome = submit_capture(db, tenant, session_id, side, data, state.quality_engine, state.capture_store,
                             limits, request.state.request_id)
    if isinstance(outcome, CaptureResponse) and outcome.status == "DOCUMENT_PROCESSING" \
            and settings.document_processing_mode == "inline":
        # Background tasks run after the response, i.e. after this transaction has committed.
        background.add_task(_process_after_commit, state, tenant.organization_id, session_id, request.state.request_id)
    return outcome


@router.post("/kyc/{session_id}/documents", response_model=CaptureResponse, responses=CAPTURE_RESPONSES)
def upload_document(session_id: UUID, request: Request, tenant: Tenant, db: Database, background: BackgroundTasks,
                    side: Annotated[DocumentSide, Form()], file: Annotated[UploadFile, File()]):
    """Upload one side. Passports use DATA_PAGE; cards use FRONT and BACK."""
    return _capture(session_id, side, file, request, tenant, db, background)


@router.post("/kyc/{session_id}/documents/front", response_model=CaptureResponse, responses=CAPTURE_RESPONSES)
def upload_front(session_id: UUID, request: Request, tenant: Tenant, db: Database, background: BackgroundTasks,
                 file: Annotated[UploadFile, File()]):
    return _capture(session_id, "FRONT", file, request, tenant, db, background)


@router.post("/kyc/{session_id}/documents/back", response_model=CaptureResponse, responses=CAPTURE_RESPONSES)
def upload_back(session_id: UUID, request: Request, tenant: Tenant, db: Database, background: BackgroundTasks,
                file: Annotated[UploadFile, File()]):
    return _capture(session_id, "BACK", file, request, tenant, db, background)


@router.post("/kyc/{session_id}/selfie", response_model=SelfieResponse, responses={
    409: {"model": CaptureError, "description": "Session is not accepting selfies."},
    413: {"description": "Upload exceeds the size limit."},
    422: {"description": "Consent is required, or image is unreadable."},
    429: {"model": CaptureError, "description": "Selfie attempt limit reached."},
    503: {"description": "Models, encrypted storage, or document portrait unavailable."},
})
def upload_selfie(session_id: UUID, request: Request, tenant: Tenant, db: Database,
                  file: Annotated[UploadFile, File()], biometric_consent: Annotated[bool, Form()]):
    """Assess one selfie and compare it only to this session's document portrait."""
    state, settings = request.app.state, request.app.state.settings
    data = file.file.read(settings.max_selfie_bytes + 1)
    limits = SelfieLimits(settings.max_selfie_bytes, settings.max_selfie_pixels,
                          settings.max_selfie_attempts, settings.max_capture_pixels)
    return submit_selfie(db, tenant, session_id, data, state.face_engine, state.capture_store,
                         state.biometric_cipher, state.face_match_policy, limits, request.state.request_id,
                         biometric_consent=biometric_consent,
                         consent_policy_version=settings.biometric_consent_policy_version)


def _liveness_limits(settings) -> LivenessLimits:
    per_frame = min(settings.max_selfie_bytes, settings.max_liveness_bytes)
    return LivenessLimits(settings.liveness_challenge_ttl_seconds, settings.max_liveness_attempts, per_frame,
                          settings.max_selfie_pixels)


@router.post("/kyc/{session_id}/liveness/challenge", responses={
    409: {"model": CaptureError, "description": "Session is not waiting for liveness."},
    429: {"model": CaptureError, "description": "Liveness attempt limit reached."},
})
def liveness_challenge(session_id: UUID, request: Request, tenant: Tenant, db: Database):
    """Issue a single-use, randomly ordered head-movement challenge (expires quickly)."""
    state = request.app.state
    return issue_challenge(db, tenant, session_id, _liveness_limits(state.settings), state.liveness_policy,
                           request.state.request_id)


@router.post("/kyc/{session_id}/liveness", responses={
    409: {"model": CaptureError, "description": "Challenge unknown, used or expired, or session not waiting for liveness."},
    413: {"description": "Upload exceeds the size limit."},
    422: {"description": "Frames unreadable or not matched to steps."},
    503: {"description": "Face models unavailable."},
})
def upload_liveness(session_id: UUID, request: Request, tenant: Tenant, db: Database,
                    challenge_id: Annotated[UUID, Form()], nonce: Annotated[str, Form(max_length=128)],
                    frame_steps: Annotated[str, Form(max_length=200, description="Comma-separated step index per frame")],
                    frames: Annotated[list[UploadFile], File(description="Raw (unmirrored) camera frames")]):
    """Verify the challenge from 4–12 frames. Frames are assessed in memory and never stored."""
    state, settings = request.app.state, request.app.state.settings
    limits = _liveness_limits(settings)
    try:
        steps = [int(value) for value in frame_steps.split(",") if value.strip()]
    except ValueError:
        raise HTTPException(422, detail="frame_steps must be comma-separated integers.") from None
    if len(frames) > state.liveness_policy.max_frames:
        raise HTTPException(422, detail="Too many frames.")
    data = [upload.file.read(limits.max_frame_bytes + 1) for upload in frames]
    return submit_liveness(db, tenant, session_id, challenge_id, nonce, data, steps, state.face_engine,
                           state.biometric_cipher, state.face_match_policy, state.liveness_policy, limits,
                           request.state.request_id)


@router.get("/document-types")
def document_types(tenant: Tenant):
    return {"document_types": [{"type": item.value,
            "country": "KH" if item.value.startswith("KH_") else None,
            "required_sides": list(requirement_for(item).sides),
            "capture_quality_gate": "AVAILABLE",
            "adapter_status": "AVAILABLE" if adapter_for(item) else "PLANNED"} for item in DocumentType]}


@router.get("/countries")
def countries(tenant: Tenant):
    # Country-specific adapters, plus document types read from their ICAO MRZ whatever the issuing country.
    return {"countries": sorted(COUNTRY_CODES), "verification_adapters_available": ["KH"],
            "any_country_document_types": ["PASSPORT"]}
