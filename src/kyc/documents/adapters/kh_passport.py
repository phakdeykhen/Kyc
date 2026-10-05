"""Cambodia passport adapter: bilingual visual zone + ICAO TD3 MRZ on the data page.

Layout assumptions to confirm against official specimens: English/Khmer label pairs
(Surname/នាមត្រកូល, …), values printed on the line after each label, dates as
"DD MON YYYY", and an ordinary-passport MRZ starting "P<KHM". The visual zone stays the
canonical source; the MRZ fills only what the visual zone lacks and every disagreement
is reported by MRZ_CONSISTENCY. NFC chip reading is Phase 11.
"""

from kyc.documents.adapters.khmer_label import AdapterPolicy, CardLayout, KhmerLabelAdapter, NumberRule, mrz_candidates
from kyc.domain.enums import DocumentType
from kyc.domain.identity import DocumentClassification, OCRLine
from kyc.mrz import parser as mrz_parser

LAYOUT = CardLayout(
    document_type=DocumentType.KH_PASSPORT,
    family="PASSPORT",
    labels={
        "passport_number": ("Passport No", "លេខលិខិតឆ្លងដែន"),
        "surname": ("Surname", "នាមត្រកូល"),
        "given_names": ("Given names", "នាមខ្លួន"),
        "nationality": ("Nationality", "សញ្ជាតិ"),
        "date_of_birth": ("Date of birth", "ថ្ងៃខែឆ្នាំកំណើត"),
        "sex": ("Sex", "ភេទ"),
        "place_of_birth": ("Place of birth", "ទីកន្លែងកំណើត"),
        "issue_date": ("Date of issue", "ថ្ងៃចេញ"),
        "expiry_date": ("Date of expiry", "ថ្ងៃផុតកំណត់"),
    },
    text_fields=(("surname", "surname", "latin"), ("given_names", "given_names", "latin"),
                 ("nationality", "nationality_printed", "text"), ("date_of_birth", "date_of_birth", "date"),
                 ("sex", "sex", "sex"), ("place_of_birth", "place_of_birth", "text"),
                 ("issue_date", "issue_date", "date"), ("expiry_date", "expiry_date", "date")),
    # Alphanumeric with at least one digit, so label/title words ("PASSPORT") never qualify.
    numbers=(NumberRule("document_number", r"(?<![A-Z0-9])(?=[A-Z0-9]*\d)([A-Z0-9]{7,9})(?![A-Z0-9])",
                        r"(?=.*\d)[A-Z0-9]{7,9}", "DOCUMENT_NUMBER_FORMAT", label="passport_number"),),
    critical_fields=("document_number", "date_of_birth", "full_name"),
    important_fields=("sex", "expiry_date"),
    sides=("DATA_PAGE",),
    mrz_formats=("TD3",),
    mrz_regions={"DATA_PAGE": (0.0, 0.70, 1.0, 1.0)},
    # ICAO 9303 TD3: the portrait occupies the left of the data page. Reading the visual zone
    # without it stops the OCR engine from "reading" the photograph into neighbouring labels.
    viz_regions={"DATA_PAGE": (0.26, 0.0, 1.0, 0.72)},
    mrz_document_code="P",
    mrz_issuing_state="KHM",
    latin_name_from=("given_names", "surname"),
    latin_name_line=False,
    numeric_refinement=None,  # Latin numerals; the Khmer-digit re-read would damage them
)


class CambodiaPassportAdapter(KhmerLabelAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="KH-PASSPORT-ADAPTER-2026.10.1")

    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification:
        visual = super().classify(lines, side_hint)
        parsed = mrz_parser.read([line.text for line in mrz_candidates(lines)])
        if parsed is None or not parsed.mrz_valid:
            return visual
        if parsed.document_code.startswith("P") and parsed.format == "TD3":
            kind = DocumentType.KH_PASSPORT if parsed.issuing_state == "KHM" else DocumentType.PASSPORT
            return DocumentClassification(country="KH" if kind == DocumentType.KH_PASSPORT else None,
                                          document_family="PASSPORT", document_type=kind, document_side="DATA_PAGE",
                                          document_version=parsed.format, confidence=max(0.9, visual.confidence))
        kind = DocumentType.NATIONAL_ID if parsed.document_code.startswith("I") else DocumentType.UNKNOWN
        return DocumentClassification(country=None, document_family="OTHER", document_type=kind,
                                      document_side="UNKNOWN", document_version=parsed.format, confidence=0.7)
