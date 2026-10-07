"""ICAO Doc 9303 machine-readable zone parsing: TD1 (3×30), TD2 (2×36), TD3 (2×44).

Pipeline (spec §9): normalize allowed characters → detect format → parse → validate
check digits → compare with the visual zone. A valid MRZ only proves internal
consistency of what was read; it is never treated as proof of authenticity.

Character handling: OCR confusions (O/0, I/1, ...) are substituted only inside fields
that ICAO defines as numeric (dates, check digits). Every substitution is counted and
reported, and the check digits then confirm or refute the reading. Alphanumeric
fields (document number, names, optional data) are never digit-substituted. Name
padding noise may be removed with an explicit flag; original OCR lines are retained.
"""

from dataclasses import dataclass, field
from datetime import date
import re

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
FORMATS = {"TD1": (3, 30), "TD2": (2, 36), "TD3": (2, 44)}
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"})
_LOOKALIKES = str.maketrans({"«": "<", "‹": "<", "(": "<", "[": "<", "{": "<", " ": ""})


def check_digit(value: str) -> str:
    total = 0
    for index, character in enumerate(value):
        if "0" <= character <= "9":
            number = int(character)
        elif "A" <= character <= "Z":
            number = ord(character) - 55
        elif character == "<":
            number = 0
        else:
            raise ValueError("Check-digit input must use the ICAO MRZ alphabet")
        total += number * (7, 3, 1)[index % 3]
    return str(total % 10)


@dataclass
class MRZResult:
    format: str
    lines: tuple[str, ...]
    document_code: str = ""
    issuing_state: str = ""
    document_number: str | None = None
    nationality: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    expiry_date: date | None = None
    surname: str | None = None
    given_names: str | None = None
    optional_data: str | None = None
    check_digits: dict[str, dict] = field(default_factory=dict)
    substitutions: int = 0
    flags: list[str] = field(default_factory=list)
    data_valid: bool = True

    @property
    def mrz_valid(self) -> bool:
        return self.data_valid and bool(self.check_digits) and all(item["valid"] for item in self.check_digits.values())

    @property
    def full_name(self) -> str | None:
        parts = [part for part in (self.given_names, self.surname) if part]
        return " ".join(parts) if parts else None


def clean_line(text: str) -> str:
    return re.sub(r"[^A-Z0-9<]", "", text.upper().translate(_LOOKALIKES))


def _fit(line: str, width: int, name_line: bool = False) -> tuple[str, bool] | None:
    """Fit a line to the format width. Lost trailing filler is restored; nothing else is shifted.

    Only a name line may have lost a long run of trailing filler; data lines must be within ±3.
    """
    if len(line) == width:
        return line, False
    if width - 3 <= len(line) < width or (name_line and len(line) < width and line.endswith("<")):
        return (line + "<" * width)[:width], True
    if width < len(line) <= width + 3 and set(line[width:]) <= {"<"}:
        return line[:width], True
    return None


def _document_code(code: str, format_name: str) -> bool:
    first_codes = "P" if format_name == "TD3" else "ACI"
    if not re.fullmatch(f"[{first_codes}][A-Z<]", code):
        return False
    # Parts 5/6 note k reserves V. TD1 also reserves AI; AC is the TD1 crew
    # member certificate code, but is not allowed by the TD2 data directory.
    return format_name == "TD3" or (code[1] != "V" and code != ("AI" if format_name == "TD1" else "AC"))


def _header(line: str, format_name: str) -> bool:
    return _document_code(line[:2], format_name) and bool(re.fullmatch(r"[A-Z<]{3}", line[2:5]))


def _date_characters(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9<]{6}", text.translate(_TO_DIGIT)))


FIELD_CHECKS = ("document_number", "date_of_birth", "expiry_date")


def _fields_verified(result: "MRZResult") -> bool:
    """Document number, birth date and expiry each match their own check digit."""
    return all(result.check_digits.get(name, {}).get("valid") for name in FIELD_CHECKS)


def _td1_tail_repair(line: str) -> str:
    """TD1 line 2: keep the 18 characters up to the nationality; the optional-data zone after it is
    filler on most cards, where OCR turns '<' into letters (C, E, K…) or drops the tail. Letters there
    become '<', a lost tail is refilled, and a composite digit is kept only if one was actually read."""
    head, tail = line[:18], line[18:]
    zone = re.sub(r"[A-Z]", "<", tail)
    composite = zone[-1] if len(zone) >= 12 and zone[-1].isdigit() else "<"
    body = zone[:-1] if len(zone) >= 12 else zone
    return head + (body + "<" * 11)[:11] + composite


def assemble(candidates: list[str], today: date | None = None) -> tuple[str, tuple[str, ...], list[str]] | None:
    """Pick the best TD1/TD2/TD3 block from candidate lines (in reading order, possibly from several OCR passes).

    Lines are grouped by format and role before checksum validation. Names have no check
    digits and are selected independently, so unrelated OCR text cannot cause cubic work.
    Cross-pass readings may appear out of order. Only trailing filler is restored.
    """
    lines = list(dict.fromkeys(clean_line(line) for text in candidates for line in text.splitlines()))
    lines = [line for line in lines if len(line) >= 20]
    best = None
    for name, (count, width) in FORMATS.items():
        name_position = 2 if name == "TD1" else 0
        pools = [[] for _ in range(count)]
        for raw in lines:
            for position in range(count):
                # The data line (with the check digits) may also carry one stray OCR character; each
                # single deletion is a candidate that is kept only if every check digit then validates.
                variants = [(raw, False)]
                if position == 1 and width - 2 <= len(raw) <= width + 4:
                    variants += [(raw[:index] + raw[index + 1:], True) for index in range(min(len(raw), width))]
                if name == "TD1" and position == 1 and 18 <= len(raw) <= width + 6:
                    variants.append((_td1_tail_repair(raw), True))
                for line, repaired in variants:
                    fit = _fit(line, width, position == name_position)
                    if fit is None:
                        continue
                    if repaired:
                        fit = (fit[0], fit[1], True)
                    pools[position].append(fit)
        for position, pool in enumerate(pools):
            kept = []
            for fit in dict.fromkeys(pool):
                fitted = fit[0]
                if position == 0 and not _header(fitted, name):
                    continue
                if position == 1:
                    birth = fitted[:6] if name == "TD1" else fitted[13:19]
                    expiry = fitted[8:14] if name == "TD1" else fitted[21:27]
                    if not _date_characters(birth) or not _date_characters(expiry):
                        continue
                if name == "TD1" and position == 2 and not re.fullmatch(r"[A-Z<]+", fitted):
                    continue
                kept.append(fit)
            pools[position] = kept
        if any(not pool for pool in pools):
            continue

        def name_quality(fit):
            name_field = fit[0] if name == "TD1" else fit[0][5:]
            run = re.search(r"<{3,}", name_field)
            noise = len(re.findall(r"[A-Z0-9]", name_field[run.end():])) if run else 0
            cleaned, _ = _filler_noise(name_field)
            syntax_valid = bool(re.fullmatch(r"[A-Z<]+", cleaned) and cleaned.strip("<"))
            if name != "TD1":
                syntax_valid = syntax_valid and bool(fit[0][2:5].strip("<"))
            # A reading with fewer noisy trailing characters beats one filled with misread noise;
            # check the name separator in the unpadded name rather than against trailing '<' padding.
            return (syntax_valid, "<<" in cleaned.rstrip("<"), -noise, -fit[1])

        chosen_name = max(pools[name_position], key=name_quality)
        # TD2/TD3 checks depend only on line 2; TD1 checks depend on lines 1 and 2.
        first_pool = pools[0] if name == "TD1" else [chosen_name]
        # Repaired readings are only tried when no reading as printed validates, so a clean MRZ costs
        # no extra parses.
        for allow_repair in (False, True):
            if allow_repair and best is not None and best[0][0]:
                break
            best = _best_block(name, count, first_pool, pools[1], chosen_name, name_quality, today, best, allow_repair)
    return (best[1], best[2], best[3]) if best else None


def _best_block(name, count, first_pool, second_pool, chosen_name, name_quality, today, best, allow_repair):
    for first in first_pool:
            for second in second_pool:
                combo = [first, second] + ([chosen_name] if name == "TD1" else [])
                if (len(first) > 2 or len(second) > 2) != allow_repair:
                    continue
                block = tuple(fit[0] for fit in combo)
                if len(set(block)) != count:
                    continue
                result = parse(name, block, today)
                repaired = any(len(fit) > 2 for fit in combo)
                if repaired and not (result.data_valid and _fields_verified(result)):
                    continue  # a repair is only believed when the field check digits then agree
                valid = sum(item["valid"] for item in result.check_digits.values())
                adjusted = sum(fit[1] for fit in combo)
                noise = -name_quality(chosen_name)[2]
                score = (result.mrz_valid, valid, result.data_valid, -noise, -repaired, -adjusted, -result.substitutions)
                if best is None or score > best[0]:
                    flags = (["MRZ_LINE_LENGTH_ADJUSTED"] if adjusted else []) + (["MRZ_CHAR_CORRECTED"] if repaired else [])
                    if not result.mrz_valid and _fields_verified(result):
                        flags.append("MRZ_COMPOSITE_UNVERIFIED")
                    best = (score, name, block, flags)
    return best


class _Reader:
    def __init__(self, result: MRZResult):
        self.result = result

    def numeric(self, text: str) -> str:
        converted = text.translate(_TO_DIGIT)
        changed = sum(1 for a, b in zip(text, converted) if a != b)
        if changed:
            self.result.substitutions += changed
        return converted

    def check(self, name: str, value: str, digit: str, allow_empty_filler: bool = False) -> None:
        computed = check_digit(value)
        # Part 4 allows '<' only for the unused TD3 personal-number check digit.
        valid = computed == digit or (allow_empty_filler and digit == "<" and set(value) <= {"<"})
        self.result.check_digits[name] = {"expected": digit, "computed": computed, "valid": valid}


def _date(text: str, kind: str, today: date) -> date | None:
    """Infer century from the two-digit MRZ year; the century is not encoded by ICAO.

    Birth uses the most recent matching date no later than today. Expiry uses the
    100-year window beginning 50 years before today. Visual-zone comparison remains
    necessary to resolve people over 100 years old or unusually distant expiry dates.
    """
    if not re.fullmatch(r"[0-9]{6}", text):
        return None
    year, month, day = int(text[:2]), int(text[2:4]), int(text[4:6])
    century = today.year // 100 * 100
    year += century
    if kind == "birth" and (year, month, day) > (today.year, today.month, today.day):
        year -= 100
    elif kind != "birth":
        if year < today.year - 50:
            year += 100
        elif year >= today.year + 50:
            year -= 100
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _filler_noise(field_text: str) -> tuple[str, bool]:
    """In a name field, '<<<' can only be trailing padding (ICAO 9303), so anything after it is misread filler."""
    run = re.search(r"<{3,}", field_text)
    if not run:
        return field_text, False
    head, tail = field_text[:run.start()], field_text[run.start():]
    if not re.search(r"[A-Z0-9]", tail):
        return field_text, False
    return head + "<" * len(tail), True


def _names(field_text: str) -> tuple[str | None, str | None]:
    surname, _, given = field_text.partition("<<")
    surname = surname.replace("<", " ").strip() or None
    given = re.sub(r"<+", " ", given).strip() or None
    return surname, given


def _sex(character: str) -> str | None:
    return {"M": "M", "F": "F"}.get(character)  # '<' means unspecified


def _clean_name(result: "MRZResult", field_text: str) -> str:
    cleaned, noisy = _filler_noise(field_text)
    if noisy:
        result.flags.append("MRZ_NAME_FILLER_NOISE_REMOVED")
    return cleaned


def _numeric_lines(format_name: str, lines: tuple[str, ...], reader: _Reader) -> tuple[str, ...]:
    """Normalize each numeric position once, including digits used in composite checks."""
    positions = {
        "TD3": {1: (*range(13, 20), *range(21, 28), 9, 42, 43)},
        "TD2": {1: (*range(13, 20), *range(21, 28), 9, 35)},
        "TD1": {0: (14,), 1: (*range(0, 7), *range(8, 15), 29)},
    }
    normalized = list(lines)
    for line_index, indexes in positions[format_name].items():
        characters = list(lines[line_index])
        for index in indexes:
            characters[index] = reader.numeric(characters[index])
        normalized[line_index] = "".join(characters)
    return tuple(normalized)


def _number(result: MRZResult, reader: _Reader, number: str, digit: str, optional: str = "") -> tuple[str, str, str]:
    """Read the Parts 5/6 overflow form: 9 characters, '<', remainder + digit + '<'."""
    if digit != "<" or result.format == "TD3":
        reader.check("document_number", number, digit)
        return number, optional, optional
    prefix, separator, remainder = optional.partition("<")
    if not separator or len(prefix) < 2 or "<" in number:
        reader.check("document_number", number, digit)
        result.data_valid = False
        result.flags.append("MRZ_DOCUMENT_NUMBER_EXTENSION_INVALID")
        return number, optional, optional
    extended = number + prefix[:-1]
    extended_digit = reader.numeric(prefix[-1])
    reader.check("document_number", extended, extended_digit)
    result.flags.append("MRZ_DOCUMENT_NUMBER_EXTENDED")
    return extended, remainder, prefix[:-1] + extended_digit + "<" + remainder


def _birth_possible(text: str, today: date) -> bool:
    """Unknown positions are allowed, but known month/day digits must be feasible."""
    if not re.fullmatch(r"[0-9<]{6}", text):
        return False
    if "<" not in text:
        return _date(text, "birth", today) is not None
    patterns = [re.compile(part.replace("<", "[0-9]")) for part in (text[:2], text[2:4], text[4:])]
    values = [[value for value in bounds if pattern.fullmatch(f"{value:02d}")]
              for pattern, bounds in zip(patterns, (range(100), range(1, 13), range(1, 32)))]
    for year in values[0]:
        for month in values[1]:
            for day in values[2]:
                if _date(f"{year:02d}{month:02d}{day:02d}", "birth", today) is not None:
                    return True
    return False


def parse(format_name: str, lines: tuple[str, ...], today: date | None = None) -> MRZResult:
    today = today or date.today()
    if format_name not in FORMATS:
        raise ValueError(f"Unknown MRZ format {format_name}")
    count, width = FORMATS[format_name]
    if (len(lines) != count or any(not isinstance(line, str) or len(line) != width
                                  or not re.fullmatch(r"[A-Z0-9<]+", line) for line in lines)):
        raise ValueError(f"{format_name} requires {count} lines of {width} ICAO MRZ characters")
    lines = tuple(lines)
    result = MRZResult(format=format_name, lines=lines)
    reader = _Reader(result)
    normalized = _numeric_lines(format_name, lines, reader)
    if format_name == "TD3":
        first, second = normalized
        result.document_code, result.issuing_state = first[0:2].rstrip("<"), first[2:5]
        result.surname, result.given_names = _names(_clean_name(result, first[5:44]))
        number, number_digit = second[0:9], second[9]
        result.nationality = second[10:13]
        birth, birth_digit = second[13:19], second[19]
        sex, expiry, expiry_digit = second[20], second[21:27], second[27]
        personal, personal_digit, composite = second[28:42], second[42], second[43]
        number, _, _ = _number(result, reader, number, number_digit)
        reader.check("date_of_birth", birth, birth_digit)
        reader.check("expiry_date", expiry, expiry_digit)
        reader.check("personal_number", personal, personal_digit, allow_empty_filler=True)
        reader.check("composite", second[0:10] + second[13:20] + second[21:43], composite)
        result.optional_data = personal.strip("<") or None
    elif format_name == "TD2":
        first, second = normalized
        result.document_code, result.issuing_state = first[0:2].rstrip("<"), first[2:5]
        result.surname, result.given_names = _names(_clean_name(result, first[5:36]))
        number, number_digit = second[0:9], second[9]
        result.nationality = second[10:13]
        birth, birth_digit = second[13:19], second[19]
        sex, expiry, expiry_digit = second[20], second[21:27], second[27]
        optional, composite = second[28:35], second[35]
        number, remaining_optional, optional = _number(result, reader, number, number_digit, optional)
        reader.check("date_of_birth", birth, birth_digit)
        reader.check("expiry_date", expiry, expiry_digit)
        reader.check("composite", second[0:10] + second[13:20] + second[21:28] + optional, composite)
        result.optional_data = remaining_optional.strip("<") or None
    elif format_name == "TD1":
        first, second, third = normalized
        result.document_code, result.issuing_state = first[0:2].rstrip("<"), first[2:5]
        number, number_digit, optional_1 = first[5:14], first[14], first[15:30]
        birth, birth_digit = second[0:6], second[6]
        sex, expiry, expiry_digit = second[7], second[8:14], second[14]
        result.nationality, optional_2, composite = second[15:18], second[18:29], second[29]
        result.surname, result.given_names = _names(_clean_name(result, third))
        number, remaining_optional, optional_1 = _number(result, reader, number, number_digit, optional_1)
        reader.check("date_of_birth", birth, birth_digit)
        reader.check("expiry_date", expiry, expiry_digit)
        reader.check("composite", first[5:15] + optional_1 + second[0:7] + second[8:15] + optional_2, composite)
        result.optional_data = (remaining_optional + optional_2).strip("<") or None
    result.document_number = number.replace("<", "") or None
    result.date_of_birth = _date(birth, "birth", today)
    result.expiry_date = _date(expiry, "expiry", today)
    result.sex = _sex(sex)
    result.nationality = (result.nationality or "").replace("<", "") or None
    if result.substitutions:
        result.flags.append("MRZ_NUMERIC_CHARACTER_SUBSTITUTION")
    invalid = []
    if not _document_code(lines[0][:2], format_name):
        invalid.append("MRZ_DOCUMENT_CODE_INVALID")
    if not re.fullmatch(r"[A-Z<]{3}", result.issuing_state) or not result.issuing_state.strip("<"):
        invalid.append("MRZ_ISSUING_STATE_INVALID")
    raw_nationality = second[15:18] if format_name == "TD1" else second[10:13]
    if not re.fullmatch(r"[A-Z<]{3}", raw_nationality) or not raw_nationality.strip("<"):
        invalid.append("MRZ_NATIONALITY_INVALID")
    if not result.document_number:
        invalid.append("MRZ_DOCUMENT_NUMBER_INVALID")
    if sex not in "MF<":
        invalid.append("MRZ_SEX_INVALID")
    if not result.surname or not re.fullmatch(r"[A-Z ]+", result.surname + (result.given_names or "")):
        invalid.append("MRZ_NAME_INVALID")
    if not _birth_possible(birth, today):
        invalid.append("MRZ_DATE_OF_BIRTH_INVALID")
    elif result.date_of_birth is None:
        result.flags.append("MRZ_DATE_OF_BIRTH_INCOMPLETE")
    if result.expiry_date is None:
        invalid.append("MRZ_EXPIRY_INVALID")
    result.flags.extend(invalid)
    result.data_valid = result.data_valid and not invalid
    return result


def read(candidates: list[str], today: date | None = None) -> MRZResult | None:
    block = assemble(candidates, today)
    if block is None:
        return None
    format_name, lines, flags = block
    result = parse(format_name, lines, today)
    result.flags = sorted(set(result.flags) | set(flags))
    return result


def _name_tokens(value: object) -> list[str]:
    return re.sub(r"[^A-Z ]", " ", str(value or "").upper()).split()


def _names_match(mrz: MRZResult, visual: list[str], machine: list[str]) -> bool:
    if sorted(visual) == sorted(machine):
        return True
    name_field = mrz.lines[2] if mrz.format == "TD1" else mrz.lines[0][5:]
    # An alphabetic final position is ICAO's indication that truncation may have
    # occurred. Padding gives no justification for accepting longer visual names.
    if not name_field[-1:].isalpha() or len(visual) != len(machine):
        return False
    components = _name_tokens(mrz.given_names if "<<" in name_field else mrz.surname)
    if not components:
        return False
    truncated = components[-1]
    remaining = list(visual)
    other = list(machine)
    other.remove(truncated)
    for token in other:
        if token not in remaining:
            return False
        remaining.remove(token)
    return len(remaining) == 1 and remaining[0].startswith(truncated)


def compare(mrz: MRZResult, visual: dict[str, object]) -> dict[str, str]:
    """Field-by-field MRZ ↔ visual zone comparison. Disagreement is reported, never resolved."""
    outcome: dict[str, str] = {}
    pairs = {"document_number": mrz.document_number, "date_of_birth": mrz.date_of_birth, "sex": mrz.sex,
             "expiry_date": mrz.expiry_date, "nationality": mrz.nationality}
    for name, mrz_value in pairs.items():
        viz_value = visual.get(name)
        if name == "nationality" and viz_value == "KH":
            viz_value = "KHM"  # the visual field is stored as ISO alpha-2; the MRZ uses alpha-3
        if mrz_value in (None, "") and viz_value in (None, ""):
            continue
        if mrz_value in (None, ""):
            outcome[name] = "VIZ_ONLY"
        elif viz_value in (None, ""):
            outcome[name] = "MRZ_ONLY"
        else:
            mrz_clean = str(mrz_value).replace("<", "")
            viz_clean = str(viz_value).replace("<", "")
            match = (mrz_clean == viz_clean)
            if not match and name == "document_number":
                # National IDs (e.g. Cambodia) may print 10 digits while TD1 allocates 9 characters
                # to the document number (positions 6-14) followed by the check digit in position 15.
                match = viz_clean.startswith(mrz_clean) or mrz_clean.startswith(viz_clean)
            outcome[name] = "MATCH" if match else "MISMATCH"
    viz_name, mrz_name = _name_tokens(visual.get("full_name")), _name_tokens(mrz.full_name)
    if viz_name and mrz_name:
        same = _names_match(mrz, viz_name, mrz_name)
        outcome["full_name"] = "MATCH" if same else "MISMATCH"
    elif mrz_name:
        outcome["full_name"] = "MRZ_ONLY"
    elif viz_name:
        outcome["full_name"] = "VIZ_ONLY"
    return outcome
