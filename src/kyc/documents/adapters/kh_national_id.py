"""Cambodia National ID (Khmer Identity Card) adapter — a layout on the shared Khmer label engine.

Confirmed on a real card (6 October 2026 phone test): the portrait side carries the Khmer
labels, the Latin name and the "IDKHM" TD1 MRZ along its bottom; the back is mostly a
fingerprint/seal area with no identity text. Still to confirm against official specimens:
the full label set, the unlabelled 9-digit number at the top, and the validity label.
"""

from kyc.documents.adapters.khmer_label import (  # noqa: F401  (find_label re-exported)
    AdapterPolicy, CardLayout, KhmerLabelAdapter, NumberRule, find_label)
from kyc.domain.enums import DocumentType

LAYOUT = CardLayout(
    document_type=DocumentType.KH_NATIONAL_ID,
    family="NATIONAL_ID",
    labels={
        "full_name_local": ("គោត្តនាម និងនាម", "គោត្តនាមនិងនាម", "គៅគ្គនាមនិងនាម", "គៅគ្គនាម និងនាម", "ឈ្មោះ", "គោត្តនាម"),
        "date_of_birth": ("ថ្ងៃខែឆ្នាំកំណើត",),
        "sex": ("ភេទ", "ភេទ/Sex", "ភេទ / Sex"),
        "height": ("កម្ពស់",),
        "place_of_birth": ("ទីកន្លែងកំណើត",),
        "address": ("អាសយដ្ឋាន",),
        "validity": ("សុពលភាព",),
        "features": ("ភិនភាគ",),
    },
    text_fields=(("full_name_local", "full_name_local", "khmer"), ("date_of_birth", "date_of_birth", "date"),
                 ("sex", "sex", "sex"), ("place_of_birth", "place_of_birth", "khmer"), ("address", "address", "khmer")),
    numbers=(NumberRule("document_number", r"(?<!\d)(\d{9,10})(?!\d)", r"\d{9,10}", "DOCUMENT_NUMBER_FORMAT"),),
    critical_fields=("document_number", "full_name_local", "date_of_birth"),
    important_fields=("full_name", "sex", "expiry_date"),
    multiline={"place_of_birth": 1, "address": 2},
    validity_label="validity",
    back_marker="IDKHM",
    mrz_on_front=True,
    # A check-digit-valid MRZ already proves number, birth date and sex; an unreadable Khmer name then
    # goes to a reviewer instead of looping the person through recaptures.
    mrz_relieves=("full_name_local",),
    mrz_name_surname_first=True,
    mrz_formats=("TD1",),
    mrz_regions={"FRONT": (0.0, 0.62, 1.0, 1.0)},
    mrz_document_code="ID",
    mrz_issuing_state="KHM",
    # Locate labels in the printed visual zone, away from portrait and MRZ interference.
    viz_regions={"FRONT": (0.24, 0.015, 0.99, 0.65)},
    portrait_regions={"FRONT": (0.0, 0.15, 0.27, 0.83)},
    # Fallback bands on a rectified portrait side. Detected labels/word boxes take priority.
    khmer_field_regions={"full_name_local": (0.23, 0.055, 0.98, 0.155),
                         "place_of_birth": (0.26, 0.26, 0.98, 0.37),
                         "address": (0.26, 0.36, 0.98, 0.56)},
)


class CambodiaNationalIDAdapter(KhmerLabelAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="KH-NID-ADAPTER-2026.10.5", typical_validity_years=(9, 11))
