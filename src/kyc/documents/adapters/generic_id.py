"""Generic national ID and residence card adapters (Phase 6).

For cards from countries without a dedicated adapter. The reliable source is an ICAO
TD1 (or TD2) MRZ, normally on the back; the front's visual zone is read with common
English labels and cross-checked. A card without an MRZ can still be processed when
its labels are legible, but its classification stays at REVIEW confidence. A card with
neither is sent back for recapture rather than guessed at. Country adapters (Phase 20)
replace this for specific documents.
"""

from dataclasses import replace

from kyc.documents.adapters.khmer_label import AdapterPolicy, CardLayout, KhmerLabelAdapter, NumberRule, mrz_candidates
from kyc.domain.enums import DocumentType
from kyc.domain.identity import DocumentClassification, OCRLine
from kyc.mrz import parser as mrz_parser

ID_LABELS = {
    "document_number": ("Document No", "Document number", "Card No", "Card number", "ID No", "ID number",
                        "Identity card number", "Permit No", "Permit number"),
    "surname": ("Surname", "Last name", "Family name", "Nom", "Apellidos"),
    "given_names": ("Given names", "Given name", "First name", "Prénoms", "Nombres"),
    "name": ("Full name", "Name"),
    "nationality": ("Nationality", "Nationalité", "Nacionalidad"),
    "date_of_birth": ("Date of birth", "Birth date", "Date de naissance", "Fecha de nacimiento"),
    "sex": ("Sex", "Gender", "Sexe", "Sexo"),
    "issue_date": ("Date of issue", "Issue date", "Date de délivrance"),
    "expiry_date": ("Date of expiry", "Expiry date", "Expiry", "Valid until", "Date d'expiration"),
}

BASE = CardLayout(
    document_type=DocumentType.NATIONAL_ID,
    family="NATIONAL_ID",
    labels=ID_LABELS,
    text_fields=(("name", "full_name", "latin"), ("surname", "surname", "latin"),
                 ("given_names", "given_names", "latin"), ("nationality", "nationality_printed", "text"),
                 ("date_of_birth", "date_of_birth", "date"), ("sex", "sex", "sex"),
                 ("issue_date", "issue_date", "date"), ("expiry_date", "expiry_date", "date")),
    numbers=(NumberRule("document_number", r"(?<![A-Z0-9])(?=[A-Z0-9]*\d)([A-Z0-9]{5,15})(?![A-Z0-9])",
                        r"(?=.*\d)[A-Z0-9]{5,15}", "DOCUMENT_NUMBER_FORMAT", label="document_number"),),
    critical_fields=("document_number", "date_of_birth", "full_name"),
    important_fields=("sex", "expiry_date"),
    mrz_formats=("TD1", "TD2"),
    mrz_regions={"BACK": (0.0, 0.45, 1.0, 1.0)},
    latin_name_from=("given_names", "surname"),
    latin_name_line=False,
    nationality=None,
    numeric_refinement=None,
    ocr_languages=("eng",),
)
# ICAO TD1/TD2 document codes for identity cards and residence documents start with I, A or C.
CARD_CODES = ("I", "A", "C")


class GenericIDAdapter(KhmerLabelAdapter):
    layout = BASE
    policy = AdapterPolicy(version="GENERIC-ID-ADAPTER-2026.10.1")

    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification:
        family = self.layout.family
        candidates = [line.text for line in mrz_candidates(lines)]
        parsed = mrz_parser.read(candidates) if candidates else None
        labels = self._label_hits([line for line in lines if "MRZ_PASS" not in line.notes])
        if parsed is not None and parsed.format in ("TD1", "TD2"):
            if not parsed.document_code.startswith(CARD_CODES):
                other = DocumentType.PASSPORT if parsed.document_code.startswith("P") else DocumentType.UNKNOWN
                return DocumentClassification(country=None, document_family="OTHER", document_type=other,
                                              document_side="UNKNOWN", confidence=0.7)
            confidence = 0.9 if parsed.mrz_valid else 0.5
            side = "BACK" if len(labels) < 3 else "FRONT"
            return DocumentClassification(country=None, document_family=family, document_type=self.document_type,
                                          document_side=side, document_version=parsed.format, confidence=confidence)
        if parsed is not None and parsed.format == "TD3":
            return DocumentClassification(country=None, document_family="OTHER", document_type=DocumentType.PASSPORT,
                                          document_side="UNKNOWN", confidence=0.7)
        if len(labels) >= 3:
            # Legible labels but no MRZ on this side: plausible card front, not verified by any checksum.
            return DocumentClassification(country=None, document_family=family, document_type=self.document_type,
                                          document_side="FRONT", confidence=min(0.55, 0.15 * len(labels)))
        if side_hint == "FRONT":
            # Unreadable front (e.g. labels in a script we do not read): the back's MRZ must carry the card.
            return DocumentClassification(country=None, document_family=family, document_type=self.document_type,
                                          document_side="FRONT", confidence=0.4)
        return DocumentClassification(country=None, document_family=family, document_type=self.document_type,
                                      document_side="UNKNOWN", confidence=0.0)


class GenericResidenceCardAdapter(GenericIDAdapter):
    layout = replace(BASE, document_type=DocumentType.RESIDENCE_CARD, family="RESIDENCE_CARD")
    policy = AdapterPolicy(version="GENERIC-RESIDENCE-ADAPTER-2026.10.1")
