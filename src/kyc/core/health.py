"""Dependency health for /health/ready and /health/dependencies.

Each dependency reports UP, DOWN, NOT_CONFIGURED or NOT_IMPLEMENTED with a stable reason
code; nothing reports UP without being checked. In production every critical dependency
must be UP before the instance is READY, so a load balancer never routes identity
decisions to an instance that would crash or quietly degrade. Outside production only the
database decides readiness, and the full list still says what is missing.

Results are cached for a few seconds: probes run often and the OCR check starts a process.
"""

from dataclasses import asdict, dataclass
import os
import threading
import time

import sqlalchemy as sa

from kyc import __schema_revision__

CACHE_SECONDS = 10.0


@dataclass(frozen=True)
class Dependency:
    name: str
    status: str            # UP | DOWN | NOT_CONFIGURED | NOT_IMPLEMENTED | NOT_APPLICABLE
    critical: bool
    reason: str | None = None


def _database(state) -> Dependency:
    try:
        with state.session_factory() as db:
            revision = db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    except sa.exc.SQLAlchemyError:
        return Dependency("postgresql", "DOWN", True, "DATABASE_UNAVAILABLE_OR_NOT_MIGRATED")
    if revision != __schema_revision__:
        return Dependency("postgresql", "DOWN", True, "SCHEMA_REVISION_MISMATCH")
    return Dependency("postgresql", "UP", True)


def _object_storage(state) -> Dependency:
    store = state.capture_store
    if store is None:
        return Dependency("object_storage", "NOT_CONFIGURED", True, "CAPTURE_ENCRYPTION_KEYS_MISSING")
    root = getattr(store, "root", None)
    if root is not None:
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            return Dependency("object_storage", "DOWN", True, "CAPTURE_STORAGE_NOT_WRITABLE")
        if not os.access(root, os.W_OK):
            return Dependency("object_storage", "DOWN", True, "CAPTURE_STORAGE_NOT_WRITABLE")
    return Dependency("object_storage", "UP", True)


def _ocr(state) -> Dependency:
    processor = state.document_processor
    ocr = getattr(processor, "ocr", None)
    if ocr is None:
        return Dependency("ocr_worker", "NOT_CONFIGURED", True, "OCR_ENGINE_NOT_CONFIGURED")
    if not ocr.is_available(processor.languages):
        return Dependency("ocr_worker", "DOWN", True, "OCR_ENGINE_OR_LANGUAGE_DATA_MISSING")
    return Dependency("ocr_worker", "UP", True)


def _face(state) -> list[Dependency]:
    engine = state.face_engine
    reason = engine.unavailable_reason() if engine is not None else "FACE_ENGINE_NOT_CONFIGURED"
    status = "UP" if reason is None else "DOWN"
    # Liveness runs on the same detector and landmarks, so it shares the model's availability.
    return [Dependency("face_model", status, True, reason), Dependency("liveness_model", status, True, reason)]


def _keys(state) -> list[Dependency]:
    found = []
    for name, value, reason in (("pii_encryption", state.field_cipher, "PII_ENCRYPTION_KEYS_MISSING"),
                                ("biometric_encryption", state.biometric_cipher, "BIOMETRIC_ENCRYPTION_KEYS_MISSING"),
                                ("webhook_secret_encryption", state.webhook_cipher, "WEBHOOK_SECRET_KEYS_MISSING")):
        found.append(Dependency(name, "UP" if value is not None else "NOT_CONFIGURED", True, None if value else reason))
    # Keys are injected as secrets today; Cloud KMS envelope encryption is a Phase 19 deployment task.
    found.append(Dependency("kms", "NOT_IMPLEMENTED", False, "KEYS_FROM_ENVIRONMENT_SECRETS"))
    return found


def _nfc(state) -> Dependency:
    if not state.settings.nfc_enabled:
        return Dependency("csca_trust_store", "NOT_APPLICABLE", False, "NFC_DISABLED")
    trust = state.csca_trust
    if trust is None or not trust.certificates:
        return Dependency("csca_trust_store", "NOT_CONFIGURED", True, "NO_TRUSTED_CSCA_CERTIFICATES")
    return Dependency("csca_trust_store", "UP", True)


def _workers(state) -> list[Dependency]:
    mode = state.settings.document_processing_mode
    # Backlogs are tenant-scoped rows behind row-level security; they are measured by the
    # workers' own metrics, not by an unauthenticated probe.
    return [Dependency("document_queue", "UP" if mode == "inline" else "NOT_IMPLEMENTED", False,
                       "INLINE_PROCESSING" if mode == "inline" else "BACKLOG_MONITORED_BY_WORKER_METRICS"),
            Dependency("webhook_worker", "NOT_IMPLEMENTED", False, "BACKLOG_MONITORED_BY_WORKER_METRICS"),
            Dependency("redis", "NOT_APPLICABLE", False, "NOT_USED")]


def _others(state) -> list[Dependency]:
    return [_object_storage(state), _ocr(state), *_face(state), *_keys(state), _nfc(state), *_workers(state)]


class HealthCache:
    """The database is checked on every probe; the slower checks are reused for a few seconds."""

    def __init__(self, ttl: float = CACHE_SECONDS):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value: tuple[float, list[Dependency]] | None = None

    def get(self, state) -> list[Dependency]:
        with self._lock:
            cached = self._value if self._value and time.monotonic() - self._value[0] < self.ttl else None
        if cached is None:
            cached = (time.monotonic(), _others(state))
            with self._lock:
                self._value = cached
        return [_database(state), *cached[1]]


def readiness(dependencies: list[Dependency], production: bool) -> tuple[bool, list[str]]:
    """Production needs every critical dependency UP; elsewhere only the database blocks readiness."""
    blocking = [item.name for item in dependencies
                if item.critical and item.status != "UP" and (production or item.name == "postgresql")]
    return not blocking, blocking


def as_json(dependencies: list[Dependency]) -> list[dict]:
    return [{key: value for key, value in asdict(item).items() if value is not None} for item in dependencies]
