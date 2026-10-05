from contextlib import asynccontextmanager
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from kyc import __schema_revision__, __version__
from kyc.api.routes import router
from kyc.biometrics import FaceMatchPolicy, OpenCVFaceEngine
from kyc.core.config import Settings, get_settings
from kyc.db.session import build_engine
from kyc.core.crypto import FieldCipher, decode_key
from kyc.engines.capture_quality import HeuristicDocumentQualityEngine
from kyc.ocr.tesseract import TesseractOCREngine
from kyc.services.documents import DocumentProcessor
from kyc.storage.captures import LocalEncryptedCaptureStore, parse_keyring
from kyc.storage.biometrics import BiometricCipher

UPLOAD_PATH = re.compile(r"^/v1/kyc/[^/]+/documents(/front|/back)?$")
SELFIE_PATH = re.compile(r"^/v1/kyc/[^/]+/selfie$")
JSON_BODY_LIMIT = 64 * 1024
MULTIPART_OVERHEAD = 256 * 1024
# Every request is bounded before form parsing. Keep permitted captures in RAM
# until encrypted storage rather than Starlette's default plaintext disk spool.
MultiPartParser.spool_max_size = 25 * 1024 * 1024 + MULTIPART_OVERHEAD
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


def build_biometric_cipher(settings: Settings) -> BiometricCipher | None:
    return None if settings.biometric_encryption_keys is None else BiometricCipher(
        settings.biometric_encryption_keys.get_secret_value())


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
        application.state.biometric_cipher = build_biometric_cipher(configuration)
        application.state.face_engine = OpenCVFaceEngine(
            configuration.face_models_dir / "face_detection_yunet_2023mar.onnx",
            configuration.face_models_dir / "face_recognition_sface_2021dec.onnx")
        application.state.face_match_policy = FaceMatchPolicy(
            version=configuration.face_match_policy_version,
            pass_threshold=configuration.face_match_pass_threshold,
            fail_threshold=configuration.face_match_fail_threshold,
            calibrated=configuration.face_match_calibrated,
            calibration_reference=configuration.face_match_calibration_reference)
        application.state.document_processor = build_document_processor(
            configuration, application.state.session_factory, application.state.capture_store, application.state.field_cipher)
        try:
            yield
        finally:
            if database_engine is None:
                engine.dispose()

    application = FastAPI(title="Universal Identity Platform", version=__version__, lifespan=lifespan,
                          description="KYC sessions, document extraction and MRZ validation, face quality, and private 1:1 comparison (phases 1–5, 8–9).")
    application.state.settings = settings

    @application.middleware("http")
    async def limit_request_body(request: Request, call_next):
        # Runs before multipart parsing, so oversized uploads are never spooled.
        if request.method in {"POST", "PUT", "PATCH"}:
            upload = bool(UPLOAD_PATH.match(request.url.path))
            if SELFIE_PATH.match(request.url.path):
                limit = request.app.state.settings.max_selfie_bytes + MULTIPART_OVERHEAD
            else:
                limit = request.app.state.settings.max_capture_bytes + MULTIPART_OVERHEAD if upload else JSON_BODY_LIMIT
            length = request.headers.get("content-length")
            if length is None:
                return JSONResponse(status_code=411, content={"detail": "Content-Length is required."})
            if not length.isdigit() or int(length) > limit:
                return JSONResponse(status_code=413, content={"detail": "Request body is too large.", "reason_code": "FILE_TOO_LARGE"})
            buffered, total = bytearray(), 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    return JSONResponse(status_code=413, content={"detail": "Request body is too large.", "reason_code": "FILE_TOO_LARGE"})
                buffered.extend(chunk)
            # Starlette's cached request replays this bounded body to the parser.
            request._body = bytes(buffered)
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
        return {"status": "ok", "phase": 9, "implemented_phases": [1, 2, 3, 4, 5, 8, 9], "version": __version__}

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
