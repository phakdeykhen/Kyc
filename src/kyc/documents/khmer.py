"""Khmer/Latin text normalization for OCR output.

Normalization never invents data: anything ambiguous is returned with flags so the
caller can lower confidence and route to review, rather than silently "fixing" it.
"""

from dataclasses import dataclass, field
from datetime import date
import re
import unicodedata

KHMER_DIGITS = "០១២៣៤៥៦៧៨៩"
_DIGIT_MAP = {ord(k): str(i) for i, k in enumerate(KHMER_DIGITS)}
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_KHMER_BLOCK = re.compile(r"[ក-៿᧠-᧿]")
_LATIN_DIGIT = re.compile(r"[0-9]")
_KHMER_DIGIT = re.compile(f"[{KHMER_DIGITS}]")
DATE_PATTERN = re.compile(r"(?<![0-9០-៩])([0-9០-៩]{1,2})\s*[./\-]\s*([0-9០-៩]{1,2})\s*[./\-]\s*([0-9០-៩]{4})(?![0-9០-៩])")


@dataclass(frozen=True)
class Normalized:
    value: str | None
    flags: tuple[str, ...] = field(default=())


def clean(text: str) -> str:
    """NFC, drop zero-width characters, collapse whitespace."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return re.sub(r"\s+", " ", text).strip()


def has_khmer(text: str) -> bool:
    return bool(_KHMER_BLOCK.search(text))


def digits_to_ascii(text: str) -> Normalized:
    flags = ("MIXED_DIGIT_SCRIPTS",) if _LATIN_DIGIT.search(text) and _KHMER_DIGIT.search(text) else ()
    return Normalized(text.translate(_DIGIT_MAP), flags)


def parse_date(text: str) -> Normalized:
    """DD.MM.YYYY / DD/MM/YYYY / DD-MM-YYYY in either digit script → ISO date."""
    match = DATE_PATTERN.search(text)
    if not match:
        return Normalized(None, ("DATE_NOT_FOUND",))
    raw = "".join(match.groups())
    flags = digits_to_ascii(raw).flags
    day, month, year = (int(part.translate(_DIGIT_MAP)) for part in match.groups())
    try:
        return Normalized(date(year, month, day).isoformat(), flags)
    except ValueError:
        return Normalized(None, flags + ("INVALID_DATE",))


def find_dates(text: str) -> list[tuple[str, Normalized]]:
    return [(match.group(0), parse_date(match.group(0))) for match in DATE_PATTERN.finditer(text)]


def parse_sex(text: str) -> Normalized:
    value = clean(text)
    if "ប្រុស" in value or re.fullmatch(r"(?i)m(ale)?", value):
        return Normalized("M")
    if "ស្រី" in value or re.fullmatch(r"(?i)f(emale)?", value):
        return Normalized("F")
    return Normalized(None, ("SEX_UNRECOGNIZED",))


def latin_name(text: str) -> Normalized:
    value = clean(text).upper()
    stripped = re.sub(r"[^A-Z \-']", "", value)
    flags = ("NON_NAME_CHARACTERS_REMOVED",) if stripped != value else ()
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return Normalized(stripped or None, flags + (() if stripped else ("EMPTY_VALUE",)))


def khmer_text(text: str) -> Normalized:
    value = clean(text).strip(" :;.-")
    if not value:
        return Normalized(None, ("EMPTY_VALUE",))
    if not has_khmer(value):
        # A Khmer-script field with no Khmer text is a misread, not a value.
        return Normalized(None, ("EXPECTED_KHMER_SCRIPT",))
    return Normalized(value)
