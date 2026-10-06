from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Request, Response, UploadFile, status

from kyc.api.dependencies import AnyApiKey, Capture, Database, SessionReader, SessionWriter, StatusReader, TenantContext
from kyc.api.schemas import COUNTRY_CODES, CaptureError, CaptureResponse, ClientTokenResponse, DocumentSide, SessionCreate, SessionResponse, SessionResult, SelfieResponse, VerifyResponse
from kyc.documents.adapters import adapter_for
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import DocumentType
from kyc.services.captures import CaptureLimits, submit_capture
from kyc.services.biometrics import SelfieLimits, submit_selfie
from kyc.services.liveness import LivenessLimits, issue_challenge, submit_liveness
from kyc.services.nfc import NFCLimits, issue_nfc_challenge, submit_nfc
from kyc.services.results import build_result
from kyc.services.risk import verify_session
from kyc.services.sessions import aware, create_session, get_session, issue_client_token, respond

router = APIRouter(prefix="/v1")

CAPTURE_RESPONSES = {
    409: {"model": CaptureError, "description": "Session expired, closed, or not accepting document capture."},
    413: {"description": "Upload exceeds the size limit."},
    422: {"description": "Unreadable or unsupported image, or a side this document type does not have."},
    429: {"model": CaptureError, "description": "Capture attempt limit reached for this session."},
    503: {"description": "Capture storage is not configured."},
}


@router.post("/kyc/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, responses={
    200: {"model": SessionResponse, "description": "Replay of an earlier request with the same Idempotency-Key."},
    409: {"description": "Idempotency-Key already used with a different request."},
})
def start_session(body: SessionCreate, request: Request, response: Response, tenant: SessionWriter, db: Database,
                  idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=128,
                                                                pattern=r"^[A-Za-z0-9._:-]+$")] = None):
    """Create a session. Send an Idempotency-Key so that retrying after a timeout cannot create a duplicate."""
    record, replayed = create_session(db, tenant, body, request.app.state.settings.session_ttl_seconds,
                                      request.state.request_id, idempotency_key)
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return respond(record)


@router.get("/kyc/{session_id}", response_model=SessionResponse)
def read_session(session_id: UUID, request: Request, tenant: StatusReader, db: Database):
    return respond(get_session(db, tenant, session_id, request.state.request_id))


@router.post("/kyc/{session_id}/client-token", response_model=ClientTokenResponse, status_code=status.HTTP_201_CREATED,
             responses={409: {"description": "The session is closed."}})
def client_token(session_id: UUID, request: Request, tenant: SessionWriter, db: Database):
    """Issue a token for the user's device: it can capture evidence for, and read the status of, this session only.

    Issuing a new token revokes the previous one. Never ship an API key to a browser or mobile app.
    """
    record = get_session(db, tenant, session_id, request.state.request_id)
    token = issue_client_token(db, tenant, record, request.state.request_id)
    return ClientTokenResponse(session_id=record.id, client_token=token, scope="session:capture",
                               expires_at=aware(record.expires_at))


@router.get("/kyc/{session_id}/result", response_model=SessionResult)
def read_result(session_id: UUID, request: Request, tenant: SessionReader, db: Database):
    """Identity fields are masked unless the credential holds the results:identity scope."""
    record = get_session(db, tenant, session_id, request.state.request_id)
    return build_result(db, record, request.app.state.field_cipher, reveal_identity="results:identity" in tenant.scopes)


def _process_after_commit(state, organization_id: UUID, session_id: UUID, request_id: UUID) -> None:
    outcome = state.document_processor.process(organization_id, session_id, request_id)
    if outcome.status == "ACCEPTED":
        # Document-only sessions reach PROCESSING here; the assessor ignores any other status.
        state.assessor.assess(organization_id, session_id, request_id)


def _assess_if_processing(outcome, background: BackgroundTasks, request: Request, tenant, session_id: UUID):
    """Fraud analysis and the risk decision run after the transaction that reached PROCESSING commits."""
    status_value = outcome.get("status") if isinstance(outcome, dict) else getattr(outcome, "status", None)
    if str(getattr(status_value, "value", status_value)) == "PROCESSING":
        background.add_task(request.app.state.assessor.assess, tenant.organization_id, session_id,
                            request.state.request_id)
    return outcome


def _capture(session_id: UUID, side: str, file: UploadFile, request: Request, tenant: TenantContext, db: Database,
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
def upload_document(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
                    side: Annotated[DocumentSide, Form()], file: Annotated[UploadFile, File()]):
    """Upload one side. Passports use DATA_PAGE; cards use FRONT and BACK."""
    return _capture(session_id, side, file, request, tenant, db, background)


@router.post("/kyc/{session_id}/documents/front", response_model=CaptureResponse, responses=CAPTURE_RESPONSES)
def upload_front(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
                 file: Annotated[UploadFile, File()]):
    return _capture(session_id, "FRONT", file, request, tenant, db, background)


@router.post("/kyc/{session_id}/documents/back", response_model=CaptureResponse, responses=CAPTURE_RESPONSES)
def upload_back(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
                file: Annotated[UploadFile, File()]):
    return _capture(session_id, "BACK", file, request, tenant, db, background)


@router.post("/kyc/{session_id}/selfie", response_model=SelfieResponse, responses={
    409: {"model": CaptureError, "description": "Session is not accepting selfies."},
    413: {"description": "Upload exceeds the size limit."},
    422: {"description": "Consent is required, or image is unreadable."},
    429: {"model": CaptureError, "description": "Selfie attempt limit reached."},
    503: {"description": "Models, encrypted storage, or document portrait unavailable."},
})
def upload_selfie(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
                  file: Annotated[UploadFile, File()], biometric_consent: Annotated[bool, Form()]):
    """Assess one selfie and compare it only to this session's document portrait."""
    state, settings = request.app.state, request.app.state.settings
    data = file.file.read(settings.max_selfie_bytes + 1)
    limits = SelfieLimits(settings.max_selfie_bytes, settings.max_selfie_pixels,
                          settings.max_selfie_attempts, settings.max_capture_pixels)
    outcome = submit_selfie(db, tenant, session_id, data, state.face_engine, state.capture_store,
                            state.biometric_cipher, state.face_match_policy, limits, request.state.request_id,
                            biometric_consent=biometric_consent,
                            consent_policy_version=settings.biometric_consent_policy_version)
    return _assess_if_processing(outcome, background, request, tenant, session_id)


def _liveness_limits(settings) -> LivenessLimits:
    per_frame = min(settings.max_selfie_bytes, settings.max_liveness_bytes)
    return LivenessLimits(settings.liveness_challenge_ttl_seconds, settings.max_liveness_attempts, per_frame,
                          settings.max_selfie_pixels)


@router.post("/kyc/{session_id}/liveness/challenge", responses={
    409: {"model": CaptureError, "description": "Session is not waiting for liveness."},
    429: {"model": CaptureError, "description": "Liveness attempt limit reached."},
})
def liveness_challenge(session_id: UUID, request: Request, tenant: Capture, db: Database):
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
def upload_liveness(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
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
    outcome = submit_liveness(db, tenant, session_id, challenge_id, nonce, data, steps, state.face_engine,
                              state.biometric_cipher, state.face_match_policy, state.liveness_policy, limits,
                              request.state.request_id)
    return _assess_if_processing(outcome, background, request, tenant, session_id)


def _nfc_limits(settings) -> NFCLimits:
    return NFCLimits(settings.nfc_challenge_ttl_seconds, settings.max_nfc_attempts)


@router.post("/kyc/{session_id}/nfc/challenge", responses={
    409: {"model": CaptureError, "description": "Session is not waiting for the chip step."},
    429: {"model": CaptureError, "description": "Chip attempt limit reached."},
})
def nfc_challenge(session_id: UUID, request: Request, tenant: Capture, db: Database):
    """Issue a fresh 8-byte Active Authentication challenge for the chip to sign."""
    return issue_nfc_challenge(db, tenant, session_id, _nfc_limits(request.app.state.settings), request.state.request_id)


def _optional_file(upload: UploadFile | None, limit: int) -> bytes | None:
    if upload is None:
        return None
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, detail="Chip file is too large.")
    return data or None


@router.post("/kyc/{session_id}/nfc", responses={
    409: {"model": CaptureError, "description": "Challenge invalid, or session not waiting for the chip step."},
    413: {"description": "Upload exceeds the size limit."},
    429: {"model": CaptureError, "description": "Chip attempt limit reached."},
})
def upload_nfc(session_id: UUID, request: Request, tenant: Capture, db: Database, background: BackgroundTasks,
               read_status: Annotated[Literal["READ", "NOT_SUPPORTED", "NOT_AVAILABLE", "FAILED"], Form()] = "READ",
               access_protocol: Annotated[Literal["PACE", "BAC"] | None, Form()] = None,
               challenge_id: Annotated[UUID | None, Form()] = None,
               aa_signature: Annotated[str | None, Form(max_length=2048, pattern=r"^[0-9a-fA-F]*$")] = None,
               sod: Annotated[UploadFile | None, File(description="EF.SOD as read from the chip")] = None,
               dg1: Annotated[UploadFile | None, File(description="EF.DG1 (MRZ)")] = None,
               dg2: Annotated[UploadFile | None, File(description="EF.DG2 (portrait)")] = None,
               dg15: Annotated[UploadFile | None, File(description="EF.DG15 (Active Authentication key)")] = None):
    """Verify chip data server-side (Passive + Active Authentication). Raw chip data is not stored."""
    state, settings = request.app.state, request.app.state.settings
    limit = settings.max_nfc_bytes
    groups = {number: data for number, data in ((1, _optional_file(dg1, limit)), (2, _optional_file(dg2, limit)),
                                                (15, _optional_file(dg15, limit))) if data}
    signature = bytes.fromhex(aa_signature) if aa_signature else None
    outcome = submit_nfc(db, tenant, session_id, read_status, access_protocol, groups, _optional_file(sod, limit),
                         challenge_id, signature, state.csca_trust, state.field_cipher, state.face_engine,
                         state.biometric_cipher, state.face_match_policy, _nfc_limits(settings), request.state.request_id)
    return _assess_if_processing(outcome, background, request, tenant, session_id)


@router.post("/kyc/{session_id}/verify", response_model=VerifyResponse, responses={
    409: {"model": CaptureError, "description": "Session not ready, expired, or document not processed."},
})
def verify(session_id: UUID, request: Request, tenant: SessionWriter, db: Database):
    """Run the deterministic risk engine for a PROCESSING session, or return the decision already made."""
    record = get_session(db, tenant, session_id, request.state.request_id)
    return verify_session(db, tenant, record, request.app.state.assessor, request.state.request_id)


@router.get("/document-types")
def document_types(tenant: AnyApiKey):
    return {"document_types": [{"type": item.value,
            "country": "KH" if item.value.startswith("KH_") else None,
            "required_sides": list(requirement_for(item).sides),
            "capture_quality_gate": "AVAILABLE",
            "adapter_status": "AVAILABLE" if adapter_for(item) else "PLANNED"} for item in DocumentType]}


@router.get("/countries")
def countries(tenant: AnyApiKey):
    # Country-specific adapters, plus document types read from their ICAO MRZ whatever the issuing country.
    return {"countries": sorted(COUNTRY_CODES), "verification_adapters_available": ["KH"],
            "any_country_document_types": ["PASSPORT"]}
