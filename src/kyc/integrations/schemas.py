from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kyc.api.schemas import SessionCreate
from kyc.domain.enums import DocumentType, VerificationLevel

Outcome = Literal["VERIFIED", "MANUAL_REVIEW", "REJECTED", "EXPIRED", "TECHNICAL_ERROR"]
IDENTITY_FIELDS = {"full_name", "full_name_local", "date_of_birth", "sex", "nationality"}
CHECK_FIELDS = {"document_quality", "document_classification", "document_data", "expiry", "mrz", "mrz_consistency",
                "barcode", "issuing_country", "cross_check", "fraud", "face_quality", "portrait_quality",
                "face_match", "liveness", "nfc", "nfc_status", "chip_document_consistency", "chip_active_authentication"}
PROFILE_FIELDS = {"display_name", "internal_reference", "department", "membership_status", "kyc_status"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Workflow(Strict):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    expected_document_type: DocumentType
    verification_level: VerificationLevel = VerificationLevel.DOCUMENT_FACE_LIVENESS

    @field_validator("verification_level")
    @classmethod
    def hosted_support(cls, value):
        if value not in {VerificationLevel.DOCUMENT_FACE_LIVENESS, VerificationLevel.DOCUMENT_FACE_LIVENESS_NFC}:
            raise ValueError("Hosted workflows require document, face and liveness.")
        return value


class Branding(Strict):
    display_name: str = Field(min_length=1, max_length=120)
    color: str = Field(default="#164e63", pattern=r"^#[0-9A-Fa-f]{6}$")


class ApplicationCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["sandbox", "production"] = "sandbox"
    workflows: dict[str, Workflow] = Field(min_length=1, max_length=30)
    return_urls: list[str] = Field(min_length=1, max_length=20)
    branding: Branding
    identity_fields: list[str] = Field(default_factory=list, max_length=5)
    check_fields: list[str] = Field(default_factory=list, max_length=30)
    populations: list[Literal["STAFF", "CUSTOMER"]] = Field(default_factory=list, max_length=2)
    purposes: list[Literal["self_profile", "staff_lookup", "customer_service"]] = Field(default_factory=list, max_length=3)
    profile_fields: list[str] = Field(default_factory=list, max_length=5)
    daily_session_quota: int = Field(default=1000, ge=1, le=1_000_000)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)

    @field_validator("identity_fields", "check_fields", "profile_fields")
    @classmethod
    def disclosure(cls, values, info):
        supported = {"identity_fields": IDENTITY_FIELDS, "check_fields": CHECK_FIELDS, "profile_fields": PROFILE_FIELDS}
        if set(values) - supported[info.field_name]:
            raise ValueError("Unsupported disclosure field.")
        return sorted(set(values))

    @field_validator("workflows")
    @classmethod
    def workflows_valid(cls, workflows):
        import re
        for key, workflow in workflows.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
                raise ValueError("Invalid workflow ID.")
            SessionCreate(user_id="configuration", **workflow.model_dump())
        return workflows


class VerificationCreate(Strict):
    external_subject_id: str = Field(min_length=1, max_length=128)
    workflow_id: str = Field(min_length=1, max_length=80)
    reference: str | None = Field(default=None, max_length=128)
    return_url: str = Field(min_length=1, max_length=2048)
    locale: Literal["en", "km"] = "en"


class VerificationCreated(BaseModel):
    session_id: UUID
    hosted_verification_url: str
    capture_token: str | None = None
    expires_at: datetime
    capture_expires_at: datetime
    sandbox: bool


class VerificationStatus(BaseModel):
    session_id: UUID
    external_subject_id: str | None
    reference: str | None
    workflow_id: str
    status: str
    expires_at: datetime
    result_version: int
    sandbox: bool


class VerificationResult(BaseModel):
    session_id: UUID
    outcome: Outcome | None
    result_version: int
    sandbox: bool
    identity: dict[str, str | None] = Field(default_factory=dict)
    checks: dict[str, str] = Field(default_factory=dict)


class CredentialCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=12)
    expires_in_days: int = Field(default=90, ge=1, le=730)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)


class DeviceCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    populations: list[Literal["STAFF", "CUSTOMER"]] = Field(min_length=1, max_length=2)
    purposes: list[Literal["staff_lookup", "customer_service"]] = Field(min_length=1, max_length=2)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class FaceSessionCreate(Strict):
    purpose: Literal["self_profile", "staff_lookup", "customer_service"]
    population: Literal["STAFF", "CUSTOMER"]
    external_subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    verification_session_id: UUID | None = None
    device_id: UUID | None = None


class RecognitionConsent(Strict):
    consent: Literal[True]
    notice_version: str = Field(min_length=1, max_length=80)


class FaceSessionCreated(BaseModel):
    session_id: UUID
    capture_token: str
    expires_at: datetime
    purpose: str
    population: str
    consent_required: bool


class RecognitionResult(BaseModel):
    outcome: Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "RETRY_REQUIRED", "LIVENESS_FAILED", "EXPIRED", "TECHNICAL_ERROR"]
    message: str | None = None
    recognition_reference: str | None = None
    expires_at: str | None = None
    external_subject_id: str | None = None
    profile: dict[str, str] | None = None


IdempotencyKey = Annotated[str | None, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
