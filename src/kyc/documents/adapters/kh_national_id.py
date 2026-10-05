"""Cambodia National ID (Khmer Identity Card) adapter — a layout on the shared Khmer label engine.

Layout assumptions to confirm against official specimens before production: the label
set below, the unlabelled 9-digit number at the top, Khmer numerals, a single validity
label holding "issue … expiry", and an "IDKHM" TD1 MRZ on the lower back.
"""

from kyc.documents.adapters.khmer_label import (  # noqa: F401  (find_label re-exported)
    AdapterPolicy, CardLayout, KhmerLabelAdapter, NumberRule, find_label)
from kyc.domain.enums import DocumentType

LAYOUT = CardLayout(
    document_type=DocumentType.KH_NATIONAL_ID,
    family="NATIONAL_ID",
    labels={
        "full_name_local": ("គោត្តនាម និងនាម", "ឈ្មោះ"),
        "date_of_birth": ("ថ្ងៃខែឆ្នាំកំណើត",),
        "sex": ("ភេទ",),
        "height": ("កម្ពស់",),
        "place_of_birth": ("ទីកន្លែងកំណើត",),
        "address": ("អាសយដ្ឋាន",),
        "validity": ("សុពលភាព",),
        "features": ("ភិនភាគ",),
    },
    text_fields=(("full_name_local", "full_name_local", "khmer"), ("date_of_birth", "date_of_birth", "date"),
                 ("sex", "sex", "sex"), ("place_of_birth", "place_of_birth", "khmer"), ("address", "address", "khmer")),
    numbers=(NumberRule("document_number", r"(?<!\d)(\d{9})(?!\d)", r"\d{9}", "DOCUMENT_NUMBER_FORMAT"),),
    critical_fields=("document_number", "full_name_local", "date_of_birth"),
    important_fields=("full_name", "sex", "expiry_date"),
    multiline={"place_of_birth": 1, "address": 2},
    validity_label="validity",
    back_marker="IDKHM",
    mrz_formats=("TD1",),
    mrz_regions={"BACK": (0.0, 0.45, 1.0, 1.0)},
    mrz_document_code="ID",
    mrz_issuing_state="KHM",
    portrait_regions={"FRONT": (0.0, 0.15, 0.27, 0.83)},
)


class CambodiaNationalIDAdapter(KhmerLabelAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="KH-NID-ADAPTER-2026.10.1", typical_validity_years=(9, 11))
