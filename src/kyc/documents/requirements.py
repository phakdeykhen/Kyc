"""Capture requirements per claimed document type.

Phase 2 defaults only. Country adapters (Phase 3+) own required_sides() and will
replace these entries; the core never encodes country-specific extraction rules.
"""

from dataclasses import dataclass

from kyc.domain.enums import DocumentType

ID1_ASPECT = 85.6 / 53.98   # ISO/IEC 7810 ID-1 card
TD3_ASPECT = 125.0 / 88.0   # ICAO 9303 TD3 passport data page


@dataclass(frozen=True)
class CaptureRequirement:
    sides: tuple[str, ...]
    aspect_ratio: float


_CARD = CaptureRequirement(("FRONT", "BACK"), ID1_ASPECT)
_PASSPORT = CaptureRequirement(("DATA_PAGE",), TD3_ASPECT)

CAPTURE_REQUIREMENTS: dict[DocumentType, CaptureRequirement] = {
    DocumentType.KH_NATIONAL_ID: _CARD,
    DocumentType.KH_NSSF: _CARD,
    DocumentType.KH_PASSPORT: _PASSPORT,
    DocumentType.PASSPORT: _PASSPORT,
    DocumentType.NATIONAL_ID: _CARD,
    DocumentType.RESIDENCE_CARD: _CARD,
    DocumentType.DRIVING_LICENSE: _CARD,
    DocumentType.UNKNOWN: _CARD,
}


def requirement_for(document_type: DocumentType) -> CaptureRequirement:
    return CAPTURE_REQUIREMENTS[document_type]
