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
    tokens = {token.upper() for token in re.split(r"[\s/|,]+", value) if token}
    if tokens and tokens <= {"M", "MALE", "H", "HOMBRE", "MASCULIN"}:
        return Normalized("M")  # bilingual passports print e.g. "M/H" or "F/F"
    if tokens and tokens <= {"F", "FEMALE", "MUJER", "FÉMININ", "FEMININ"}:
        return Normalized("F")
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
    value = clean(text).strip(" :;.-ៈ៖")
    if not value:
        return Normalized(None, ("EMPTY_VALUE",))
    if not has_khmer(value):
        # A Khmer-script field with no Khmer text is a misread, not a value.
        return Normalized(None, ("EXPECTED_KHMER_SCRIPT",))
    flags = []
    # Preserve the reading, while preventing mixed MRZ/Latin debris from looking like trustworthy Khmer.
    if re.search(r"[A-Za-z<>{}|]", value) or re.search(r"[.:;]\s*[:.;]", value):
        flags.append("OCR_NOISE_DETECTED")
    # COENG must introduce a consonant. An isolated subscript or leading mark is damaged OCR.
    if re.search(r"\u17d2(?![\u1780-\u17a2])", value) or re.match(r"[\u17b4-\u17d3]", value):
        flags.append("KHMER_ORDERING_UNCERTAIN")
    return Normalized(value, tuple(flags))


LATIN_MONTHS = {name: index for index, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
KHMER_MONTHS = {name: index for index, name in enumerate(
    ("មករា", "កុម្ភៈ", "មីនា", "មេសា", "ឧសភា", "មិថុនា", "កក្កដា", "សីហា", "កញ្ញា", "តុលា", "វិច្ឆិកា", "ធ្នូ"), start=1)}
_TEXT_DATE = re.compile(r"(?<![0-9០-៩])([0-9០-៩]{1,2})\s+(.{2,24}?)\s*([0-9០-៩]{4})(?![0-9០-៩])")


def parse_date_any(text: str) -> Normalized:
    """Numeric dates, or passport-style "15 MAR 1990" / "15 មីនា/MAR 1990"."""
    numeric = parse_date(text)
    if numeric.value or "INVALID_DATE" in numeric.flags:
        return numeric
    match = _TEXT_DATE.search(clean(text).upper())
    if not match:
        return Normalized(None, ("DATE_NOT_FOUND",))
    middle = match.group(2)
    months = {LATIN_MONTHS[token[:3]] for token in re.findall(r"[A-Z]{3,9}", middle) if token[:3] in LATIN_MONTHS}
    months |= {number for name, number in KHMER_MONTHS.items() if name in middle}
    if len(months) != 1:
        return Normalized(None, ("MONTH_UNRECOGNIZED" if not months else "MONTH_SOURCES_DISAGREE",))
    flags = digits_to_ascii(match.group(1) + match.group(3)).flags
    try:
        return Normalized(date(int(match.group(3).translate(_DIGIT_MAP)), months.pop(),
                               int(match.group(1).translate(_DIGIT_MAP))).isoformat(), flags)
    except ValueError:
        return Normalized(None, flags + ("INVALID_DATE",))


def plain_text(text: str) -> Normalized:
    value = clean(text).strip(" :;.-")
    return Normalized(value or None, () if value else ("EMPTY_VALUE",))
