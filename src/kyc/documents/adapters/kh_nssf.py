"""Cambodia NSSF (National Social Security Fund, បេឡាជាតិរបបសន្តិសុខសង្គម) member card adapter.

A layout on the shared Khmer label engine. Layout assumptions, all to be confirmed
against official NSSF card specimens and consented samples before production:
  * labels: name, sex, date of birth, NSSF member number, linked national ID number,
    enterprise/employer, issue date, and an expiry label that some cards may not print;
  * the member number follows its label and is 6–12 digits (exact format unconfirmed);
  * the linked national ID number is 9 digits;
  * Khmer numerals; no MRZ; the back may carry a QR code (QR engine: Phase 7).
The card is evidence of social-security registration, not a primary identity document;
the risk policy (Phase 13) decides what weight it carries.
"""

from kyc.documents.adapters.khmer_label import AdapterPolicy, CardLayout, KhmerLabelAdapter, NumberRule
from kyc.domain.enums import DocumentType

LAYOUT = CardLayout(
    document_type=DocumentType.KH_NSSF,
    family="SOCIAL_SECURITY",
    labels={
        "full_name_local": ("គោត្តនាម និងនាម", "ឈ្មោះ"),
        "sex": ("ភេទ",),
        "date_of_birth": ("ថ្ងៃខែឆ្នាំកំណើត",),
        "member_number": ("លេខសមាជិក", "លេខ ប.ស.ស"),
        "national_id_number": ("លេខអត្តសញ្ញាណប័ណ្ណ",),
        "employer": ("ឈ្មោះសហគ្រាស", "សហគ្រាស"),
        "issue_date": ("ថ្ងៃចេញប័ណ្ណ", "ថ្ងៃចេញ"),
        "expiry_date": ("ផុតកំណត់",),
    },
    text_fields=(("full_name_local", "full_name_local", "khmer"), ("sex", "sex", "sex"),
                 ("date_of_birth", "date_of_birth", "date"), ("employer", "employer", "khmer"),
                 ("issue_date", "issue_date", "date"), ("expiry_date", "expiry_date", "date")),
    numbers=(
        NumberRule("document_number", r"(\d{6,12})", r"\d{6,12}", "DOCUMENT_NUMBER_FORMAT", label="member_number"),
        NumberRule("national_id_number", r"(\d{9})", r"\d{9}", "NATIONAL_ID_NUMBER_FORMAT", label="national_id_number"),
    ),
    critical_fields=("document_number", "full_name_local"),
    important_fields=("full_name", "date_of_birth", "sex", "issue_date"),
    multiline={"employer": 1},
    expiry_printed=False,
    portrait_regions={"FRONT": (0.0, 0.15, 0.27, 0.83)},
)


class CambodiaNSSFAdapter(KhmerLabelAdapter):
    layout = LAYOUT
    policy = AdapterPolicy(version="KH-NSSF-ADAPTER-2026.10.1")
