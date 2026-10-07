from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kyc.core.crypto import decode_key, parse_keyring
from kyc.core.hardening import production_problems, shared_keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)
    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    # Optional local credential with every scope, bound to one organization. Provisioned
    # API keys (scripts/manage_tenants.py) are the tenant credentials from Phase 15 on.
    development_api_key: SecretStr | None = None
    development_organization_id: UUID | None = None
    # Phase 15 per-credential limits (requests per minute, per API instance).
    api_rate_limit_per_minute: int = Field(default=600, ge=1, le=100_000)
    client_token_rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    # Phase 16 webhooks. "background": the API sends right after commit and the worker sends
    # retries; "worker": only scripts/deliver_webhooks.py sends. Private targets are for local receivers only.
    webhook_delivery_mode: Literal["background", "worker"] = "background"
    webhook_allow_private_targets: bool = False
    webhook_timeout_seconds: float = Field(default=10.0, ge=1, le=30)
    webhook_max_attempts: int = Field(default=8, ge=1, le=20)
    webhook_secret_overlap_hours: int = Field(default=24, ge=0, le=168)
    webhook_delivery_retention_days: int = Field(default=30, ge=1, le=365)
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    # Phase 18 admission control: API requests in flight per process (0: pool capacity minus a
    # reserve), and how long an excess request may wait for a slot before 503 + Retry-After.
    max_concurrent_requests: int = Field(default=0, ge=0, le=200)
    request_queue_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    # Phase 2 capture pipeline. Keys: "version:base64(32 bytes)[,older...]"; first entry encrypts.
    capture_encryption_keys: SecretStr | None = None
    capture_storage_dir: Path = Path("var/captures")
    max_capture_bytes: int = Field(default=10 * 1024 * 1024, ge=100_000, le=25 * 1024 * 1024)
    max_capture_pixels: int = Field(default=40_000_000, ge=1_000_000, le=100_000_000)
    max_capture_attempts: int = Field(default=20, ge=2, le=100)
    # Phase 3 document extraction. PII fields use their own keyring, separate from captures.
    pii_encryption_keys: SecretStr | None = None
    pii_hmac_key: SecretStr | None = None
    tesseract_cmd: str = "tesseract"
    ocr_languages: str = "khm,eng"
    ocr_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    document_processing_mode: Literal["inline", "deferred"] = "inline"
    # Phase 7: JSON file of key id → PEM public key for signed barcode payloads (JWS). None → nothing verifiable.
    barcode_trust_store: Path | None = None
    # Phases 8-9. Model files are installed explicitly; the API never downloads weights.
    biometric_encryption_keys: SecretStr | None = None
    face_models_dir: Path = Path("var/models")
    max_selfie_bytes: int = Field(default=5 * 1024 * 1024, ge=100_000, le=25 * 1024 * 1024)
    max_selfie_pixels: int = Field(default=12_000_000, ge=1_000_000, le=40_000_000)
    max_selfie_attempts: int = Field(default=10, ge=1, le=50)
    # Phase 10 active liveness. The policy stays uncalibrated (REVIEW at best) until validated on attack data.
    liveness_challenge_ttl_seconds: int = Field(default=120, ge=30, le=600)
    max_liveness_attempts: int = Field(default=5, ge=1, le=20)
    max_liveness_bytes: int = Field(default=16 * 1024 * 1024, ge=500_000, le=64 * 1024 * 1024)
    liveness_policy_version: str = Field(default="ACTIVE-GEOMETRY-2026.10.3", min_length=1, max_length=80)
    liveness_calibrated: bool = False
    # Phase 11 ePassport chip. Directory of trusted CSCA certificates (PEM/DER), e.g. from the ICAO PKD.
    nfc_csca_trust_store: Path | None = None
    nfc_challenge_ttl_seconds: int = Field(default=120, ge=30, le=600)
    max_nfc_attempts: int = Field(default=3, ge=1, le=10)
    max_nfc_bytes: int = Field(default=512 * 1024, ge=16 * 1024, le=4 * 1024 * 1024)
    # Phase 12 fraud signals: duplicate-document velocity window and the session count that raises a signal.
    fraud_duplicate_window_hours: int = Field(default=24, ge=1, le=24 * 30)
    fraud_velocity_limit: int = Field(default=3, ge=2, le=100)
    # Phase 13: optional JSON policy overrides (tighten-only). Unset: the built-in policy.
    risk_policy_file: Path | None = None
    biometric_consent_policy_version: str = Field(default="BIOMETRIC-CONSENT-2026.10.1", min_length=1, max_length=80)
    face_match_policy_version: str = Field(default="SFACE-COSINE-UNCALIBRATED-2026.10.1", min_length=1, max_length=120)
    face_match_calibrated: bool = False
    face_match_calibration_reference: str | None = Field(default=None, min_length=1, max_length=200)
    face_match_pass_threshold: float = Field(default=0.363, ge=-1, le=1, allow_inf_nan=False)
    face_match_fail_threshold: float = Field(default=0.20, ge=-1, le=1, allow_inf_nan=False)
    # Phase 17 hardening. Webhook signing secrets get their own keyring (the PII keyring still
    # opens secrets sealed before it existed). Production refuses to start without these controls.
    webhook_secret_keys: SecretStr | None = None
    allowed_hosts: str = Field(default="*", max_length=2000, description="Comma-separated Host header values")
    expose_api_docs: bool | None = None       # default: on outside production
    enable_capture_client: bool | None = None  # default: on outside production; refused in production
    require_document_consent: bool = False
    document_consent_policy_version: str = Field(default="DOCUMENT-CONSENT-2026.10.1", min_length=1, max_length=80)
    reviewer_token_max_days: int = Field(default=90, ge=1, le=365)
    log_format: Literal["text", "json"] | None = None  # default: json in production
    # Deployment interfaces reserved for later approved phases.
    redis_url: SecretStr | None = None
    gcp_project_id: str | None = None
    gcs_capture_bucket: str | None = None
    gcs_biometric_bucket: str | None = None
    pubsub_topic: str | None = None

    @field_validator("face_match_calibration_reference", mode="before")
    @classmethod
    def normalize_calibration_reference(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @model_validator(mode="after")
    def enforce_boundaries(self):
        production = self.environment == "production"
        if self.expose_api_docs is None:
            self.expose_api_docs = not production
        if self.enable_capture_client is None:
            self.enable_capture_client = not production
        if self.log_format is None:
            self.log_format = "json" if production else "text"
        if production:
            problems = production_problems(self)
            if problems:
                raise ValueError("Production mode refused: " + " ".join(problems))
        if self.development_api_key is not None:
            key = self.development_api_key.get_secret_value()
            if len(key) < 32:
                raise ValueError("DEVELOPMENT_API_KEY must contain at least 32 characters.")
            if key.startswith(("kyc_", "kst_")):
                raise ValueError("DEVELOPMENT_API_KEY must not use a provisioned credential prefix.")
            if self.development_organization_id is None:
                raise ValueError("DEVELOPMENT_ORGANIZATION_ID is required with DEVELOPMENT_API_KEY.")
        url = self.database_url.get_secret_value()
        if not url.startswith("postgresql+psycopg2://") and not (self.environment == "test" and url.startswith("sqlite")):
            raise ValueError("PostgreSQL with psycopg2 is required; SQLite is permitted only in tests.")
        if self.capture_encryption_keys is not None:
            parse_keyring(self.capture_encryption_keys.get_secret_value())
        if (self.pii_encryption_keys is None) != (self.pii_hmac_key is None):
            raise ValueError("PII_ENCRYPTION_KEYS and PII_HMAC_KEY must be configured together.")
        if self.pii_encryption_keys is not None:
            parse_keyring(self.pii_encryption_keys.get_secret_value())
            decode_key(self.pii_hmac_key.get_secret_value())
        if self.biometric_encryption_keys is not None:
            parse_keyring(self.biometric_encryption_keys.get_secret_value())
        if self.webhook_secret_keys is not None:
            parse_keyring(self.webhook_secret_keys.get_secret_value())
        shared = shared_keys(self)
        if any("biometric" in pair for pair in shared):
            raise ValueError("Biometric keys must be separate from capture and PII keys.")
        if shared:
            raise ValueError("Each keyring needs its own keys; shared key material: " + "; ".join(shared) + ".")
        if self.face_match_fail_threshold >= self.face_match_pass_threshold:
            raise ValueError("Face match fail threshold must be lower than the pass threshold.")
        if self.face_match_calibrated and (not self.face_match_calibration_reference
                                         or "UNCALIBRATED" in self.face_match_policy_version.upper()):
            raise ValueError("Calibrated face matching requires a calibration reference and a calibrated policy version.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
