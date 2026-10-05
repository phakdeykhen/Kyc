from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kyc.core.crypto import decode_key, parse_keyring


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)
    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    development_api_key: SecretStr
    development_organization_id: UUID
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
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
    # Deployment interfaces reserved for later approved phases.
    redis_url: SecretStr | None = None
    gcp_project_id: str | None = None
    gcs_capture_bucket: str | None = None
    gcs_biometric_bucket: str | None = None
    pubsub_topic: str | None = None

    @model_validator(mode="after")
    def enforce_phase_one_boundary(self):
        if self.environment == "production":
            raise ValueError("Production mode is disabled until tenant credentials and security hardening phases are completed.")
        if len(self.development_api_key.get_secret_value()) < 32:
            raise ValueError("DEVELOPMENT_API_KEY must contain at least 32 characters.")
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
