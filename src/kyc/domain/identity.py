from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import DocumentType


class OCRWord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    notes: tuple[str, ...] = ()


class OCRLine(BaseModel):
    """One recognized text line. bbox is normalized (x0, y0, x1, y1) on the corrected side image."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]
    words: tuple[OCRWord, ...] = ()
    notes: tuple[str, ...] = ()


class OCRField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    raw_value: str | None
    normalized_value: str | None
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float] | None = None
    side: str | None = None
    source: Literal["OCR", "DERIVED"] = "OCR"
    flags: tuple[str, ...] = ()


class DocumentClassification(BaseModel):
    country: str | None
    document_family: str
    document_type: DocumentType
    document_side: Literal["FRONT", "BACK", "DATA_PAGE", "UNKNOWN"]
    document_version: str | None = None
    confidence: float = Field(ge=0, le=1)


class IdentityDocument(BaseModel):
    """Canonical extraction contract. Sensitive fields are not a public API model."""

    model_config = ConfigDict(extra="forbid")
    document_type: DocumentType
    issuing_country: str | None = None
    document_number: str | None = None
    full_name: str | None = None
    given_names: str | None = None
    surname: str | None = None
    full_name_local: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    nationality: str | None = None
    place_of_birth: str | None = None
    address: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    issuing_authority: str | None = None
    mrz: str | None = None
    portrait_ref: str | None = None
    barcode_data: str | None = None
    qr_data: str | None = None
    fields: list[OCRField] = Field(default_factory=list)
