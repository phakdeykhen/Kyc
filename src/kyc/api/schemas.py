from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kyc.domain.enums import DocumentType, SessionStatus, VerificationLevel

# ISO 3166-1 alpha-2. Core validation is country-neutral; adapters stay separate.
COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO
JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR
MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV
TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=128)
    country: str = Field(min_length=2, max_length=2)
    expected_document_type: DocumentType
    verification_level: VerificationLevel = VerificationLevel.DOCUMENT_FACE_LIVENESS

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        value = value.upper()
        if value not in COUNTRY_CODES:
            raise ValueError("Use an ISO 3166-1 alpha-2 country code.")
        return value

    @model_validator(mode="after")
    def country_document_consistency(self):
        if self.expected_document_type.value.startswith("KH_") and self.country != "KH":
            raise ValueError("Cambodian document types require country KH.")
        return self


class SessionResponse(BaseModel):
    session_id: UUID
    organization_id: UUID
    user_id: str
    country: str
    expected_document_type: DocumentType
    verification_level: VerificationLevel
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int


class ResultDocument(BaseModel):
    country: str | None
    type: DocumentType
    document_number_masked: str | None
    expiry_status: Literal["VALID", "EXPIRED", "UNKNOWN", "NOT_APPLICABLE"]


class ResultIdentity(BaseModel):
    full_name: str | None = None
    full_name_local: str | None = None
    date_of_birth: str | None = None
    sex: str | None = None
    nationality: str | None = None


class SessionResult(BaseModel):
    session_id: UUID
    status: SessionStatus
    document: ResultDocument | None = None
    identity: ResultIdentity | None = None
    checks: dict[str, str] = Field(default_factory=dict)
    review_flags: list[str] = Field(default_factory=list, description="Reason codes that a reviewer or the risk engine must consider.")
    decision: None = Field(default=None, description="Set by the risk engine (Phase 13); never by extraction alone.")


DocumentSide = Literal["FRONT", "BACK", "DATA_PAGE"]


class CaptureQuality(BaseModel):
    blur_score: float
    glare_score: float
    brightness_score: float
    shadow_score: float
    document_coverage: float
    perspective_score: float
    resolution_score: float
    overall_quality: float
    policy_version: str


class CaptureGeometry(BaseModel):
    document_detected: bool
    orientation: Literal["LANDSCAPE", "PORTRAIT"] | None = None
    rotation_hint_degrees: int | None = None
    skew_degrees: float | None = None
    corners: list[list[float]] | None = Field(default=None, description="Normalized [x, y] corners: top-left, top-right, bottom-right, bottom-left.")


class CaptureResponse(BaseModel):
    session_id: UUID
    status: SessionStatus
    side: DocumentSide
    capture_status: Literal["ACCEPTED", "RECAPTURE"]
    quality: CaptureQuality
    geometry: CaptureGeometry
    reason_codes: list[str]
    instructions: list[str]
    sides: dict[str, Literal["ACCEPTED", "REQUIRED"]]
    next_step: str
    attempts_remaining: int


class CaptureError(BaseModel):
    detail: str
    reason_code: str
    attempts_remaining: int | None = None
