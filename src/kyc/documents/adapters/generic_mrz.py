"""Generic MRZ adapter for passports from any issuing state (spec §6 GenericMRZAdapter).

It reads only the machine-readable zone. No country's visual layout is assumed, so
number and date fields require their check digits; names, nationality and sex require
a valid MRZ and remain flagged as unprotected OCR readings. A missing or invalid MRZ
means recapture. Phase 6
adds visual-zone extraction for international passports on top of this.
"""

from kyc.documents.adapters.khmer_label import AdapterPolicy, CardLayout, KhmerLabelAdapter, mrz_candidates
from kyc.domain.enums import DocumentType
from kyc.domain.identity import DocumentClassification, OCRLine
from kyc.mrz import parser as mrz_parser

LAYOUT = CardLayout(
    document_type=DocumentType.PASSPORT,
    family="PASSPORT",
    labels={},
    text_fields=(),
    numbers=(),
    critical_fields=("document_number", "date_of_birth", "full_name", "expiry_date"),
    important_fields=("sex", "nationality"),
    sides=("DATA_PAGE",),
    mrz_formats=("TD3",),
    mrz_regions={"DATA_PAGE": (0.0, 0.70, 1.0, 1.0)},
    mrz_document_code="P",
    nationality=None,
    latin_name_line=False,
    numeric_refinement=None,  # Latin numerals; the Khmer-digit re-read would damage them
)
# A/C/I denote other travel documents in ICAO; A and C alone do not prove a residence card.
OTHER_DOCUMENT_CODES = {"I": DocumentType.NATIONAL_ID}


class GenericMRZAdapter(KhmerLabelAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="GENERIC-MRZ-ADAPTER-2026.10.1")

    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification:
        candidates = [line.text for line in mrz_candidates(lines)]
        parsed = mrz_parser.read(candidates) if candidates else None
        if parsed is None:
            return DocumentClassification(country=None, document_family="PASSPORT", document_type=DocumentType.UNKNOWN,
                                          document_side="UNKNOWN", confidence=0.0)
        if not parsed.document_code.startswith("P"):
            other = OTHER_DOCUMENT_CODES.get(parsed.document_code[:1], DocumentType.UNKNOWN)
            return DocumentClassification(country=None, document_family="OTHER", document_type=other,
                                          document_side="UNKNOWN", confidence=0.7)
        confidence = 0.9 if parsed.mrz_valid and parsed.format == "TD3" else 0.5
        return DocumentClassification(country=None, document_family="PASSPORT", document_type=DocumentType.PASSPORT,
                                      document_side="DATA_PAGE", document_version=parsed.format, confidence=confidence)
