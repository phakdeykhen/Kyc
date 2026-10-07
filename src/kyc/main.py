from contextlib import asynccontextmanager
import ipaddress
import logging
from pathlib import Path
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser
from starlette.exceptions import HTTPException as StarletteHTTPException
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from kyc import __schema_revision__, __version__
from kyc.api.review_routes import router as review_router
from kyc.api.routes import router
from kyc.api.tenant_routes import router as tenant_router
from kyc.api.webhook_routes import router as webhook_router
from kyc.biometrics import FaceMatchPolicy, OpenCVFaceEngine
from kyc.core.admission import AdmissionControl
from kyc.core.config import Settings, get_settings
from kyc.core.errors import error_response
from kyc.core.hardening import allowed_host_list
from kyc.core.observability import configure_logging, route_label, security_event
from kyc.db.session import build_engine
from kyc.core.crypto import FieldCipher, decode_key
from kyc.engines.capture_quality import HeuristicDocumentQualityEngine
from kyc.barcode.signatures import TrustStore
from kyc.liveness.active import ActiveLivenessPolicy
from kyc.nfc.trust import CSCATrustStore
from kyc.risk.policy import RiskPolicy
from kyc.services.fraud import FraudAnalyzer
from kyc.services.risk import SessionAssessor
from kyc.ocr.tesseract import TesseractOCREngine
from kyc.services.documents import DocumentProcessor
from kyc.storage.captures import LocalEncryptedCaptureStore, parse_keyring
from kyc.storage.biometrics import BiometricCipher
from kyc.tenancy.ratelimit import RateLimiter
from kyc.webhooks.delivery import HTTPSender
from kyc.webhooks.dispatcher import WebhookDispatcher
from kyc.webhooks.outbox import DISPATCHER_KEY
from kyc.webhooks.secrets import build_webhook_cipher

UPLOAD_PATH = re.compile(r"^/v1/kyc/[^/]+/documents(/front|/back)?$")
SELFIE_PATH = re.compile(r"^/v1/kyc/[^/]+/selfie$")
LIVENESS_PATH = re.compile(r"^/v1/kyc/[^/]+/liveness$")
LIVENESS_FEEDBACK_PATH = re.compile(r"^/v1/kyc/[^/]+/liveness/(position|guide)$")
NFC_PATH = re.compile(r"^/v1/kyc/[^/]+/nfc$")
JSON_BODY_LIMIT = 64 * 1024
MULTIPART_OVERHEAD = 256 * 1024
# Every request is bounded before form parsing. Keep permitted captures in RAM
# until encrypted storage rather than Starlette's default plaintext disk spool.
# Liveness permits the largest body (64 MiB), including malformed single files
# that have not reached the per-frame validation yet.
MultiPartParser.spool_max_size = 64 * 1024 * 1024 + MULTIPART_OVERHEAD
CAPTURE_PAGE = Path(__file__).resolve().parent / "web" / "capture"
REVIEW_PAGE = Path(__file__).resolve().parent / "web" / "review"
CAPTURE_PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
                    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
REVIEW_PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
                   "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
# JSON responses never need to load or frame anything.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOC_PAGES = ("/docs", "/redoc")
HSTS = "max-age=63072000; includeSubDomains"
# Refusals worth a security event even without a reason code from the authentication layer.
SECURITY_STATUSES = {401, 403, 429}
log = logging.getLogger("kyc.api")


def apply_security_headers(response, request: Request):
    path = request.url.path
    response.headers["X-Request-ID"] = str(request.state.request_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.app.state.settings.environment == "production":
        response.headers["Strict-Transport-Security"] = HSTS
    if not path.startswith(("/review", "/capture") + DOC_PAGES) or response.status_code >= 400:
        response.headers["Content-Security-Policy"] = API_CSP
    if path.startswith("/review") and response.status_code < 400:
        response.headers["Content-Security-Policy"] = REVIEW_PAGE_CSP
    if path.startswith("/capture") and response.status_code < 400:
        response.headers["Content-Security-Policy"] = CAPTURE_PAGE_CSP
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()" if path.startswith("/capture") and response.status_code < 400 else "camera=(), microphone=(), geolocation=()"
    return response


def host_allowed(host_header: str | None, allowed: list[str]) -> bool:
    """Host header check (Phase 17). `*.example.com` matches subdomains; `*` allows any host."""
    host = (host_header or "").lower()
    if host.startswith("["):
        match = re.fullmatch(r"(\[[0-9a-f:.]+\])(?::([0-9]{1,5}))?", host)
        if match is None:
            return False
        try:
            ipaddress.IPv6Address(match[1][1:-1])
        except ValueError:
            return False
    else:
        match = re.fullmatch(r"([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)(?::([0-9]{1,5}))?", host)
        if match is None or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                for label in match[1].split(".")):
            return False
    if match[2] is not None and not 1 <= int(match[2]) <= 65535:
        return False
    host = match[1]
    if "*" in allowed:
        return True
    return any(host == item or (item.startswith("*.") and host.endswith(item[1:])) for item in allowed)


def build_field_cipher(settings: Settings) -> FieldCipher | None:
    if settings.pii_encryption_keys is None:
        return None
    return FieldCipher(settings.pii_encryption_keys.get_secret_value(), decode_key(settings.pii_hmac_key.get_secret_value()))


def build_document_processor(settings: Settings, factory, store, cipher) -> DocumentProcessor:
    ocr = TesseractOCREngine(settings.tesseract_cmd, settings.ocr_timeout_seconds, tessdata_dir=settings.ocr_tessdata_dir)
    languages = tuple(item.strip() for item in settings.ocr_languages.split(",") if item.strip())
    processor = DocumentProcessor(factory, store, cipher, ocr, languages, settings.max_capture_pixels)
    processor.trust_store = TrustStore.load(settings.barcode_trust_store)
    return processor


def build_capture_store(settings: Settings) -> LocalEncryptedCaptureStore | None:
    if settings.capture_encryption_keys is None:
        return None
    active, keys = parse_keyring(settings.capture_encryption_keys.get_secret_value())
    return LocalEncryptedCaptureStore(settings.capture_storage_dir, active, keys)


def build_biometric_cipher(settings: Settings) -> BiometricCipher | None:
    return None if settings.biometric_encryption_keys is None else BiometricCipher(
        settings.biometric_encryption_keys.get_secret_value())


def build_fraud_analyzer(settings: Settings, factory, field_cipher, biometric_cipher, capture_store, face_policy) -> FraudAnalyzer:
    return FraudAnalyzer(factory, field_cipher, biometric_cipher, capture_store, face_policy,
                         settings.fraud_duplicate_window_hours, settings.fraud_velocity_limit)


def create_app(settings: Settings | None = None, database_engine: sa.Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or get_settings()
        application.state.settings = configuration
        configure_logging(configuration.log_format)
        engine = database_engine if database_engine is not None else build_engine(configuration)
        application.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        application.state.rate_limiter = RateLimiter()
        application.state.capture_store = build_capture_store(configuration)
        application.state.quality_engine = HeuristicDocumentQualityEngine()
        application.state.field_cipher = build_field_cipher(configuration)
        application.state.biometric_cipher = build_biometric_cipher(configuration)
        application.state.webhook_cipher = build_webhook_cipher(configuration, application.state.field_cipher)
        application.state.webhook_dispatcher = WebhookDispatcher(
            application.state.session_factory, application.state.webhook_cipher,
            HTTPSender(configuration.webhook_timeout_seconds, configuration.webhook_allow_private_targets),
            configuration.webhook_max_attempts, background=configuration.webhook_delivery_mode == "background")
        # Every session from this factory hands newly committed deliveries to the dispatcher.
        application.state.session_factory.configure(info={DISPATCHER_KEY: application.state.webhook_dispatcher})
        application.state.face_engine = OpenCVFaceEngine(
            configuration.face_models_dir / "face_detection_yunet_2023mar.onnx",
            configuration.face_models_dir / "face_recognition_sface_2021dec.onnx")
        application.state.csca_trust = CSCATrustStore.load(configuration.nfc_csca_trust_store)
        application.state.liveness_policy = ActiveLivenessPolicy(
            version=configuration.liveness_policy_version, calibrated=configuration.liveness_calibrated)
        application.state.face_match_policy = FaceMatchPolicy(
            version=configuration.face_match_policy_version,
            pass_threshold=configuration.face_match_pass_threshold,
            fail_threshold=configuration.face_match_fail_threshold,
            calibrated=configuration.face_match_calibrated,
            calibration_reference=configuration.face_match_calibration_reference)
        application.state.document_processor = build_document_processor(
            configuration, application.state.session_factory, application.state.capture_store, application.state.field_cipher)
        application.state.fraud_analyzer = build_fraud_analyzer(
            configuration, application.state.session_factory, application.state.field_cipher,
            application.state.biometric_cipher, application.state.capture_store, application.state.face_match_policy)
        application.state.assessor = SessionAssessor(application.state.session_factory, application.state.fraud_analyzer,
                                                     RiskPolicy.load(configuration.risk_policy_file))
        try:
            yield
        finally:
            application.state.webhook_dispatcher.close()
            if database_engine is None:
                engine.dispose()

    # Documentation routes are added below so that settings can switch them off at runtime.
    application = FastAPI(title="Universal Identity Platform", version=__version__, lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None,
                          description="KYC sessions, document extraction (Cambodian and international documents) MRZ and barcode validation, face quality, private 1:1 comparison, active liveness, ePassport chip verification, cross-checks, fraud signals and a deterministic risk engine, an authorized manual review dashboard, multi-tenant API keys with scopes, signed webhooks, security/privacy controls, and load-tested admission control (phases 1–18).")
    application.state.settings = settings
    # Outermost: bounds requests in flight so they cannot exhaust the connection pool (Phase 18).
    application.add_middleware(AdmissionControl)

    @application.middleware("http")
    async def limit_request_body(request: Request, call_next):
        # Runs before multipart parsing, so oversized uploads are never spooled.
        if request.method in {"POST", "PUT", "PATCH"}:
            upload = bool(UPLOAD_PATH.match(request.url.path))
            if SELFIE_PATH.match(request.url.path):
                limit = request.app.state.settings.max_selfie_bytes + MULTIPART_OVERHEAD
            elif LIVENESS_PATH.match(request.url.path):
                limit = request.app.state.settings.max_liveness_bytes + MULTIPART_OVERHEAD
            elif feedback := LIVENESS_FEEDBACK_PATH.match(request.url.path):
                settings_now = request.app.state.settings
                frame_limit = min(settings_now.max_selfie_bytes, settings_now.max_liveness_bytes)
                limit = frame_limit * (2 if feedback[1] == "guide" else 1) + MULTIPART_OVERHEAD
            elif NFC_PATH.match(request.url.path):
                limit = request.app.state.settings.max_nfc_bytes + MULTIPART_OVERHEAD
            else:
                limit = request.app.state.settings.max_capture_bytes + MULTIPART_OVERHEAD if upload else JSON_BODY_LIMIT
            length = request.headers.get("content-length")
            if length is None:
                return JSONResponse(status_code=411, content={"detail": "Content-Length is required."})
            if not re.fullmatch(r"[0-9]{1,20}", length) or int(length) > limit:
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
        request.state.security_reason = None
        settings_now = request.app.state.settings
        path = request.url.path
        if not path.startswith("/health/") and not host_allowed(request.headers.get("host"),
                                                                 allowed_host_list(settings_now.allowed_hosts)):
            request.state.security_reason = "HOST_NOT_ALLOWED"
            response = JSONResponse(status_code=400, content={"detail": "Invalid host header."})
        elif path.startswith("/capture") and not settings_now.enable_capture_client:
            response = JSONResponse(status_code=404, content={"detail": "Not Found"})
        else:
            response = await call_next(request)
        if request.state.security_reason or response.status_code in SECURITY_STATUSES:
            security_event(request, response.status_code, request.state.security_reason)
        return apply_security_headers(response, request)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Pydantic's default errors include submitted values; omit those PII fields.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]})

    @application.exception_handler(sa.exc.SQLAlchemyError)
    async def database_unavailable(request: Request, error: sa.exc.SQLAlchemyError):
        log.error("database error %s on %s", type(error).__name__, route_label(request),
                  extra={"request_id": str(getattr(request.state, "request_id", ""))})
        return error_response(503, "DATABASE_UNAVAILABLE", "Database unavailable.",
                              request_id=str(request.state.request_id))

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        if error.status_code >= 500:
            return error_response(error.status_code, "SERVICE_UNAVAILABLE", str(error.detail), headers=error.headers,
                                  request_id=str(request.state.request_id))
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail}, headers=error.headers)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        # Neither the response nor logs include exception messages or raw paths, which may contain PII.
        request_id = str(getattr(request.state, "request_id", "") or uuid4())
        request.state.request_id = request_id
        log.exception("unhandled %s on %s request_id=%s", type(error).__name__, route_label(request), request_id)
        return apply_security_headers(error_response(500, "INTERNAL_ERROR", "Internal server error.",
                                                     request_id=request_id), request)

    def require_docs(request: Request) -> None:
        if not request.app.state.settings.expose_api_docs:
            raise HTTPException(404, detail="Not Found")

    @application.get("/openapi.json", include_in_schema=False)
    def openapi_document(request: Request):
        require_docs(request)
        return JSONResponse(application.openapi())

    @application.get("/docs", include_in_schema=False)
    def swagger(request: Request):
        require_docs(request)
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{application.title} — API")

    @application.get("/redoc", include_in_schema=False)
    def redoc(request: Request):
        require_docs(request)
        return get_redoc_html(openapi_url="/openapi.json", title=f"{application.title} — API")

    @application.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok", "phase": 18, "implemented_phases": list(range(1, 19)), "version": __version__}

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
    application.include_router(review_router)
    application.include_router(tenant_router)
    application.include_router(webhook_router)
    # Development capture client; the server-side gate stays authoritative.
    application.mount("/capture", StaticFiles(directory=CAPTURE_PAGE, html=True), name="capture")
    # Reviewer dashboard: static shell; every case, image and decision goes through the reviewer API.
    application.mount("/review", StaticFiles(directory=REVIEW_PAGE, html=True), name="review")
    return application


app = create_app()
