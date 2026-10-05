"""Fraud signal vocabulary (spec §17). Signals are evidence with a severity, never a verdict.

Severity says how strongly a signal points at fraud; it is not a decision. A HIGH
cross-check mismatch still means REVIEW (spec §18). Only the risk engine (Phase 13)
turns signals into PASS/REVIEW/FAIL for the session.
"""

from dataclasses import dataclass, field
from enum import StrEnum

ENGINE_VERSION = "FRAUD-SIGNALS-2026.10.1"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Category(StrEnum):
    CONSISTENCY = "CONSISTENCY"    # independent sources disagree
    TAMPER = "TAMPER"              # cryptographic proof of altered or cloned data
    VALIDITY = "VALIDITY"          # expired, impossible or malformed dates and check digits
    DUPLICATE = "DUPLICATE"        # the same document, photo file or selfie seen elsewhere
    METADATA = "METADATA"          # file metadata naming editors or screenshots
    PORTRAIT = "PORTRAIT"          # printed portrait disagrees with the chip portrait
    CONTEXT = "CONTEXT"            # weak contextual signals (issuer, trust, incomplete steps)


@dataclass(frozen=True)
class Signal:
    signal: str
    severity: Severity
    category: Category
    detector: str
    fields: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)


# What the spec asks the fraud engine to look for, and how much of it this engine covers.
# NOT_SUPPORTED items produce no signal, so their absence must never be read as a pass.
COVERAGE = {
    "DOCUMENT_LAYOUT_MISMATCH": ("NOT_SUPPORTED", "Classification confidence only; no layout forensics model."),
    "FONT_INCONSISTENCY": ("NOT_SUPPORTED", "No font forensics model."),
    "PORTRAIT_REPLACEMENT": ("PARTIAL", "Printed portrait compared with a verified chip portrait (NFC passports only)."),
    "TEXT_REGION_INCONSISTENCY": ("NOT_SUPPORTED", "No text-region forensics model."),
    "UNEXPECTED_DOCUMENT_DIMENSIONS": ("NOT_SUPPORTED", "Aspect ratio is enforced by the capture gate, not scored as fraud."),
    "MRZ_MISMATCH": ("SUPPORTED", "MRZ compared with the visual zone, barcode and chip."),
    "BARCODE_OCR_MISMATCH": ("SUPPORTED", "Barcode compared with the visual zone and the MRZ; signatures verified."),
    "EXPIRY": ("SUPPORTED", "Expiry from the adapter's validated dates."),
    "IMPOSSIBLE_DATES": ("SUPPORTED", "Birth/issue/expiry ordering and MRZ date validity."),
    "DUPLICATE_DOCUMENT_USAGE": ("SUPPORTED", "Same document number, photo file or selfie file within the organization and retention window."),
    "METADATA_ANOMALY": ("PARTIAL", "Editing-software and screenshot markers in uploaded files; absent metadata proves nothing."),
    "IMAGE_MANIPULATION": ("NOT_SUPPORTED", "No pixel-level manipulation model; editing-software metadata only."),
    "SCREENSHOT_REPRODUCTION": ("PARTIAL", "Replayed files and screenshot metadata; no print/screen (moire) detection."),
    "NFC_MISMATCH": ("SUPPORTED", "Signed chip data compared with the visual zone and MRZ; tamper and clone checks."),
}
