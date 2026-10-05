from contextlib import asynccontextmanager
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from kyc import __schema_revision__, __version__
from kyc.api.routes import router
from kyc.core.config import Settings, get_settings
from kyc.db.session import build_engine
from kyc.core.crypto import FieldCipher, decode_key
from kyc.engines.capture_quality import HeuristicDocumentQualityEngine
from kyc.ocr.tesseract import TesseractOCREngine
from kyc.services.documents import DocumentProcessor
from kyc.storage.captures import LocalEncryptedCaptureStore, parse_keyring

UPLOAD_PATH = re.compile(r"^/v1/kyc/[^/]+/documents(/front|/back)?$")
JSON_BODY_LIMIT = 64 * 1024
MULTIPART_OVERHEAD = 256 * 1024
CAPTURE_PAGE = Path(__file__).resolve().parent / "web" / "capture"
CAPTURE_PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
                    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def build_field_cipher(settings: Settings) -> FieldCipher | None:
    if settings.pii_encryption_keys is None:
        return None
    return FieldCipher(settings.pii_encryption_keys.get_secret_value(), decode_key(settings.pii_hmac_key.get_secret_value()))


def build_document_processor(settings: Settings, factory, store, cipher) -> DocumentProcessor:
    ocr = TesseractOCREngine(settings.tesseract_cmd, settings.ocr_timeout_seconds)
    languages = tuple(item.strip() for item in settings.ocr_languages.split(",") if item.strip())
    return DocumentProcessor(factory, store, cipher, ocr, languages, settings.max_capture_pixels)


def build_capture_store(settings: Settings) -> LocalEncryptedCaptureStore | None:
    if settings.capture_encryption_keys is None:
        return None
    active, keys = parse_keyring(settings.capture_encryption_keys.get_secret_value())
    return LocalEncryptedCaptureStore(settings.capture_storage_dir, active, keys)


def create_app(settings: Settings | None = None, database_engine: sa.Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or get_settings()
        application.state.settings = configuration
        engine = database_engine if database_engine is not None else build_engine(configuration)
        application.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        application.state.capture_store = build_capture_store(configuration)
        application.state.quality_engine = HeuristicDocumentQualityEngine()
        application.state.field_cipher = build_field_cipher(configuration)
        application.state.document_processor = build_document_processor(
            configuration, application.state.session_factory, application.state.capture_store, application.state.field_cipher)
        try:
            yield
        finally:
            if database_engine is None:
                engine.dispose()

    application = FastAPI(title="Universal Identity Platform", version=__version__, lifespan=lifespan,
                          description="Phases 1-3: KYC session lifecycle, document capture quality gate, and Cambodia National ID extraction.")
    application.state.settings = settings

    @application.middleware("http")
    async def limit_request_body(request: Request, call_next):
        # Runs before multipart parsing, so oversized uploads are never spooled.
        if request.method in {"POST", "PUT", "PATCH"}:
            upload = bool(UPLOAD_PATH.match(request.url.path))
            limit = request.app.state.settings.max_capture_bytes + MULTIPART_OVERHEAD if upload else JSON_BODY_LIMIT
            length = request.headers.get("content-length")
            if length is None:
                return JSONResponse(status_code=411, content={"detail": "Content-Length is required."})
            if not length.isdigit() or int(length) > limit:
                return JSONResponse(status_code=413, content={"detail": "Request body is too large.", "reason_code": "FILE_TOO_LARGE"})
        return await call_next(request)

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/capture"):
            response.headers["Content-Security-Policy"] = CAPTURE_PAGE_CSP
            response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Pydantic's default errors include submitted values; omit those PII fields.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]})

    @application.exception_handler(sa.exc.SQLAlchemyError)
    async def database_unavailable(request: Request, error: sa.exc.SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable.", "request_id": str(request.state.request_id)})

    @application.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "phase": 3, "version": __version__}

    @application.get("/health/ready", tags=["health"])
    def ready():
        try:
            with application.state.session_factory() as db:
                revision = db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                if revision != __schema_revision__:
                    return JSONResponse(status_code=503, content={"status": "not_ready"})
            return {"status": "ready"}
        except sa.exc.SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "not_ready"})

    application.include_router(router)
    # Development capture client; the server-side gate stays authoritative.
    application.mount("/capture", StaticFiles(directory=CAPTURE_PAGE, html=True), name="capture")
    return application


app = create_app()
