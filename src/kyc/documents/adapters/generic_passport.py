"""International passport adapter (Phase 6): visual zone + TD3 MRZ for any issuing state.

Builds on GenericMRZAdapter. ICAO 9303 standardizes the data page's zones but not its
label wording, so the visual zone is read with the common English/French/Spanish labels
("Surname / Nom / Apellidos", ...). Whatever the labels yield is cross-checked against
the MRZ; fields the visual zone does not give are filled from check-digit-valid MRZ
fields. Unfamiliar label languages therefore degrade to MRZ-only reading, never to a
guess. Nationality stays the MRZ's alpha-3 code; the printed nationality is kept as text.
"""

from kyc.documents.adapters.generic_mrz import LAYOUT as MRZ_LAYOUT, GenericMRZAdapter
from kyc.documents.adapters.khmer_label import AdapterPolicy, NumberRule
from dataclasses import replace

INTERNATIONAL_LABELS = {
    "passport_number": ("Passport No", "Passport Number", "No. du passeport", "N° du passeport",
                        "Pasaporte No", "Número de pasaporte"),
    "surname": ("Surname", "Last name", "Nom", "Apellidos"),
    "given_names": ("Given names", "Given name", "First name", "Prénoms", "Nombres"),
    "nationality": ("Nationality", "Nationalité", "Nacionalidad"),
    "date_of_birth": ("Date of birth", "Date de naissance", "Fecha de nacimiento"),
    "sex": ("Sex", "Sexe", "Sexo"),
    "place_of_birth": ("Place of birth", "Lieu de naissance", "Lugar de nacimiento"),
    "issue_date": ("Date of issue", "Date de délivrance", "Fecha de expedición"),
    "expiry_date": ("Date of expiry", "Expiry date", "Date d'expiration", "Fecha de caducidad"),
    "authority": ("Authority", "Autorité", "Autoridad"),
}

LAYOUT = replace(
    MRZ_LAYOUT,
    labels=INTERNATIONAL_LABELS,
    text_fields=(("surname", "surname", "latin"), ("given_names", "given_names", "latin"),
                 ("nationality", "nationality_printed", "text"), ("date_of_birth", "date_of_birth", "date"),
                 ("sex", "sex", "sex"), ("place_of_birth", "place_of_birth", "text"),
                 ("issue_date", "issue_date", "date"), ("expiry_date", "expiry_date", "date"),
                 ("authority", "issuing_authority", "text")),
    numbers=(NumberRule("document_number", r"(?<![A-Z0-9])(?=[A-Z0-9]*\d)([A-Z0-9]{6,9})(?![A-Z0-9])",
                        r"(?=.*\d)[A-Z0-9]{6,9}", "DOCUMENT_NUMBER_FORMAT", label="passport_number"),),
    latin_name_from=("given_names", "surname"),
    # ICAO 9303 TD3: portrait on the left of the data page, MRZ at the bottom.
    viz_regions={"DATA_PAGE": (0.26, 0.0, 1.0, 0.72)},
    ocr_languages=("eng",),
)


class GenericPassportAdapter(GenericMRZAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="GENERIC-PASSPORT-ADAPTER-2026.10.1")
