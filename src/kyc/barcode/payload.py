"""Payload formats found in identity-document barcodes.

Recognized: signed JWS (compact), JSON objects, key=value lists, AAMVA PDF417 (North
American driving licences) and ICAO Visible Digital Seals (detected; verification needs
a CSCA trust list). Anything else is unstructured text that can only be searched, never
treated as a field-by-field statement.
"""

import base64
import binascii
from dataclasses import dataclass, field
from datetime import date
import json
import re

from kyc.documents import khmer

FIELD_ALIASES = {
    "document_number": ("document_number", "documentnumber", "doc_no", "docno", "number", "no", "id", "id_number",
                        "card_number", "passport_number", "member", "member_number", "nssf", "nssf_number"),
    "full_name": ("full_name", "fullname", "name"),
    "surname": ("surname", "last_name", "family_name"),
    "given_names": ("given_names", "given_name", "first_name", "firstname"),
    "date_of_birth": ("date_of_birth", "dob", "birth_date", "birthdate", "birth"),
    "expiry_date": ("expiry_date", "expiry", "expires", "valid_until", "date_of_expiry"),
    "sex": ("sex", "gender"),
    "national_id_number": ("national_id_number", "nid", "national_id"),
}
_ALIAS = {alias: canonical for canonical, aliases in FIELD_ALIASES.items() for alias in aliases}
AAMVA_FIELDS = {"DAQ": "document_number", "DCS": "surname", "DAC": "given_names", "DBB": "date_of_birth",
                "DBA": "expiry_date", "DBC": "sex"}


@dataclass
class ParsedPayload:
    format: str                                   # JWS | JSON | KEY_VALUE | AAMVA | ICAO_VDS | TEXT
    format_valid: bool | None
    fields: dict[str, str] = field(default_factory=dict)
    signature_present: bool = False
    jws: tuple[str, str, str] | None = None       # (header, payload, signature) segments
    header: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def normalize_date(value: str) -> str | None:
    value = value.strip()
    for pattern, order in ((r"(\d{4})-(\d{2})-(\d{2})", "ymd"), (r"(\d{4})(\d{2})(\d{2})", "ymd")):
        match = re.fullmatch(pattern, value)
        if match:
            year, month, day = map(int, match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
    parsed = khmer.parse_date_any(value)
    return parsed.value


def _canonical(raw: dict) -> dict[str, str]:
    out = {}
    for key, value in raw.items():
        name = _ALIAS.get(re.sub(r"[\s\-.]", "_", str(key).strip().lower()))
        if name is None or value in (None, "") or isinstance(value, (dict, list)):
            continue
        text = khmer.clean(str(value))
        if name in ("date_of_birth", "expiry_date"):
            text = normalize_date(text) or text
        elif name == "sex":
            text = khmer.parse_sex(text).value or text
        else:
            text = khmer.digits_to_ascii(text).value
        out[name] = text.upper() if name in ("full_name", "surname", "given_names") else text
    return out


def _aamva(text: str) -> ParsedPayload:
    fields = {}
    for line in re.split(r"[\n\r\x1e]+", text):
        # The first element of a subfile follows its type ("…DLDAQ…" / "…IDDAQ…") on the header line.
        subfile = re.search(r"(?:DL|ID)(D[A-Z]{2})", line) if not line[:3] in AAMVA_FIELDS else None
        if subfile:
            line = line[subfile.start(1):]
        code, value = line[:3], line[3:].strip()
        if code in AAMVA_FIELDS and value:
            name = AAMVA_FIELDS[code]
            if name in ("date_of_birth", "expiry_date"):
                # AAMVA US dates are MMDDCCYY; Canadian ones CCYYMMDD.
                us = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", value)
                value = normalize_date(f"{us.group(3)}{us.group(1)}{us.group(2)}") if us else normalize_date(value)
            elif name == "sex":
                value = {"1": "M", "2": "F"}.get(value, None)
            if value:
                fields[name] = value.upper()
    return ParsedPayload("AAMVA", bool(fields.get("document_number")), fields)


def parse(payload: bytes, text: str) -> ParsedPayload:
    if payload[:1] == b"\xdc":
        # ICAO Doc 9303 Part 13 Visible Digital Seal: binary header, signed by a CSCA-chained signer.
        return ParsedPayload("ICAO_VDS", True, signature_present=True, flags=["VDS_TRUST_LIST_UNAVAILABLE"])
    stripped = text.strip()
    if stripped.startswith("@") and "ANSI " in stripped[:20]:
        return _aamva(stripped)
    segments = stripped.split(".")
    if len(segments) == 3 and all(re.fullmatch(r"[A-Za-z0-9_\-]+", item or "") for item in segments[:2]):
        try:
            header = json.loads(_b64url(segments[0]))
            claims = json.loads(_b64url(segments[1]))
            if isinstance(header, dict) and "alg" in header and isinstance(claims, dict):
                return ParsedPayload("JWS", True, _canonical(claims), signature_present=bool(segments[2]),
                                     jws=(segments[0], segments[1], segments[2]), header=header)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            pass
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return ParsedPayload("JSON", True, _canonical(data))
        except ValueError:
            return ParsedPayload("JSON", False, flags=["JSON_MALFORMED"])
    pairs = re.findall(r"([A-Za-z_][\w .-]{0,30})\s*[:=]\s*([^;|&\n]+)", stripped)
    if len(pairs) >= 2:
        return ParsedPayload("KEY_VALUE", True, _canonical(dict(pairs)))
    return ParsedPayload("TEXT", None)
