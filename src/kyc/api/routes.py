from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile, status

from kyc.api.dependencies import Database, Tenant
from kyc.api.schemas import COUNTRY_CODES, CaptureError, CaptureResponse, DocumentSide, SessionCreate, SessionResponse, SessionResult
from kyc.documents.adapters import adapter_for
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import DocumentType
from kyc.services.captures import CaptureLimits, submit_capture
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
