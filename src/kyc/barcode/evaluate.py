"""Turns decoded barcodes into evidence: format, signature and consistency with the printed document."""

from dataclasses import dataclass

from kyc.barcode.engine import BarcodeRead
from kyc.barcode.payload import ParsedPayload, parse
from kyc.barcode.signatures import TrustStore, verify
from kyc.domain.enums import CheckResult
from kyc.domain.identity import IdentityDocument
from kyc.engines.contracts import CheckEvidence

COMPARED = ("document_number", "date_of_birth", "expiry_date", "sex", "full_name", "surname", "given_names",
            "national_id_number")


@dataclass
class BarcodeEvidence:
    side: str
    read: BarcodeRead
    parsed: ParsedPayload
    signature_valid: bool | None
    signature_reason: str
    consistency: dict[str, str]


def _norm(name: str, value: str | None) -> str:
    text = (value or "").upper()
    if name in ("full_name", "surname", "given_names"):
        return " ".join(sorted(text.replace(",", " ").split()))
    return text.replace(" ", "").replace("-", "")


def compare(parsed: ParsedPayload, document: IdentityDocument) -> dict[str, str]:
    visual = {item.field: item.normalized_value for item in document.fields if item.normalized_value}
    for name in ("document_number", "date_of_birth", "expiry_date", "sex", "full_name", "surname", "given_names"):
        value = getattr(document, name, None)
        if value:
            visual.setdefault(name, value.isoformat() if hasattr(value, "isoformat") else str(value))
    outcome = {}
    for name in COMPARED:
        coded, printed = parsed.fields.get(name), visual.get(name)
        if coded and printed:
            outcome[name] = "MATCH" if _norm(name, coded) == _norm(name, printed) else "MISMATCH"
        elif coded:
            outcome[name] = "BARCODE_ONLY"
    return outcome


def assess(reads_by_side: dict[str, list[BarcodeRead]], document: IdentityDocument, trust: TrustStore,
           expected: bool = False) -> tuple[CheckEvidence, list[BarcodeEvidence]]:
    evidence: list[BarcodeEvidence] = []
    for side, reads in reads_by_side.items():
        for read in reads:
            parsed = parse(read.payload, read.text)
            valid, reason = verify(parsed, trust) if parsed.signature_present or parsed.format == "JWS" else (None, "NO_SIGNATURE")
            evidence.append(BarcodeEvidence(side, read, parsed, valid, reason, compare(parsed, document)))
    if not evidence:
        result = CheckResult.REVIEW if expected else CheckResult.NOT_APPLICABLE
        return CheckEvidence(result, ("BARCODE_NOT_FOUND" if expected else "NO_BARCODE_ON_DOCUMENT",), check_type="BARCODE"), []
    reasons: list[str] = []
    if any(item.signature_valid is False for item in evidence):
        reasons += sorted({f"BARCODE_{item.signature_reason}" for item in evidence if item.signature_valid is False})
    mismatches = sorted({name for item in evidence for name, state in item.consistency.items() if state == "MISMATCH"})
    reasons += [f"BARCODE_VISUAL_{name.upper()}_MISMATCH" for name in mismatches]
    if any(item.parsed.format_valid is False for item in evidence):
        reasons.append("BARCODE_FORMAT_INVALID")
    matched = any(state == "MATCH" for item in evidence for state in item.consistency.values())
    details = {"codes": [{"side": item.side, "symbology": item.read.symbology, "format": item.parsed.format,
                          "format_valid": item.parsed.format_valid, "signature_present": item.parsed.signature_present,
                          "signature_valid": item.signature_valid, "signature_reason": item.signature_reason,
                          "data_consistency": item.consistency} for item in evidence]}
    if any(item.signature_valid is False for item in evidence):
        result = CheckResult.FAIL  # a signature that does not verify is a tamper signal, not a reading error
    elif reasons:
        result = CheckResult.REVIEW
    elif any(item.signature_valid for item in evidence):
        result, reasons = CheckResult.PASS, ["BARCODE_SIGNATURE_VALID"]
    elif matched:
        # Decoding plus agreement with the printed fields: consistent, not proven authentic.
        result, reasons = CheckResult.PASS, ["BARCODE_DATA_CONSISTENT"]
    else:
        result, reasons = CheckResult.NOT_APPLICABLE, ["BARCODE_NOT_COMPARABLE"]
    return CheckEvidence(result, tuple(reasons), check_type="BARCODE", details=details), evidence
