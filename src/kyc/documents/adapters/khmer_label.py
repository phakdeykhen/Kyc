"""Shared engine for Cambodian cards whose fields follow printed Khmer labels.

A card type is described by a CardLayout (labels, number rules, date rules, cues);
KhmerLabelAdapter does the work: fuzzy label matching, value extraction with
provenance, classification against rival card types, and validation. New Khmer
card types are new layouts, not new extraction code.

Rules that hold for every layout: low-confidence or mixed-script values are flagged,
never guessed; values a card does not carry stay null; engines that are not built
yet report UNAVAILABLE, never PASS.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from difflib import SequenceMatcher
import re

from kyc.documents import khmer
from kyc.documents.refine import NumericRefinement
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import DocumentClassification, IdentityDocument, OCRField, OCRLine
from kyc.engines.contracts import CheckEvidence
from kyc.mrz import parser as mrz_parser
from kyc.mrz.parser import clean_line as clean_mrz

MRZ_LINE = re.compile(r"^[A-Z0-9<]{25,44}$")
LATIN_NAME_LINE = re.compile(r"^[A-Z][A-Z' \-]{2,}$")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
KINGDOM_CUES = ("ព្រះរាជាណាចក្រកម្ពុជា", "ជាតិ សាសនា ព្រះមហាក្សត្រ")
LATIN_KINGDOM_CUES = ("KINGDOM OF CAMBODIA",)
# Distinctive titles per document type. Used to notice a different card than the one claimed.
TITLE_CUES: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.KH_NATIONAL_ID: ("អត្តសញ្ញាណប័ណ្ណ", "IDENTITY CARD"),
    DocumentType.KH_NSSF: ("បេឡាជាតិរបបសន្តិសុខសង្គម", "ប័ណ្ណសមាជិក", "ប.ស.ស", "NATIONAL SOCIAL SECURITY FUND", "NSSF"),
    DocumentType.KH_PASSPORT: ("លិខិតឆ្លងដែន", "PASSPORT", "P<KHM"),
    DocumentType.PASSPORT: ("PASSPORT", "លិខិតឆ្លងដែន"),
}
FLAG_PENALTY = {"MIXED_DIGIT_SCRIPTS": 0.6, "FUZZY_LABEL": 0.9, "AMBIGUOUS_CANDIDATES": 0.7,
                "NON_NAME_CHARACTERS_REMOVED": 0.85, "EXPECTED_KHMER_SCRIPT": 0.6, "VALUE_ON_NEXT_LINE": 0.95,
                "OCR_PASSES_DISAGREED": 0.9, "NUMERIC_REREAD_REJECTED": 0.8}
# Cambodian cards print Khmer numerals; numeric words get a second read restricted to them.
KHMER_NUMERIC_REFINEMENT = NumericRefinement(languages=("script/Khmer",), alphabet=khmer.KHMER_DIGITS + "./")
NORMALIZERS = {"khmer": khmer.khmer_text, "date": khmer.parse_date_any, "sex": khmer.parse_sex,
               "latin": khmer.latin_name, "text": khmer.plain_text}
CANONICAL = set(IdentityDocument.model_fields) - {"fields"}


def mrz_candidates(lines: list[OCRLine]) -> list[OCRLine]:
    """Keep both OCR passes: a constrained pass can be less legible than the visual pass."""
    return sorted((line for line in lines if "MRZ_PASS" in line.notes
                   or MRZ_LINE.fullmatch(clean_mrz(line.text))), key=lambda line: line.bbox[1])


def _bounds(fields) -> tuple[float, float, float, float] | None:
    boxes = [item.bbox for item in fields if item.bbox is not None]
    if not boxes:
        return None
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


@dataclass(frozen=True)
class AdapterPolicy:
    version: str
    calibrated: bool = False
    label_similarity: float = 0.78
    min_field_confidence: float = 0.80
    accept_classification: float = 0.60
    recapture_below: float = 0.35
    max_age_years: int = 120
    typical_validity_years: tuple[int, int] | None = None


@dataclass(frozen=True)
class NumberRule:
    """A number field: found after `label`, or (label=None) as an unlabelled run, preferring the top of the card."""

    output: str
    pattern: str
    format: str
    check_type: str
    label: str | None = None


@dataclass(frozen=True)
class CardLayout:
    document_type: DocumentType
    family: str
    labels: dict[str, tuple[str, ...]]
    text_fields: tuple[tuple[str, str, str], ...]          # (label key, output field, normalizer)
    numbers: tuple[NumberRule, ...]
    critical_fields: tuple[str, ...]
    important_fields: tuple[str, ...]
    multiline: dict[str, int] = field(default_factory=dict)
    validity_label: str | None = None                       # one label holding "issue … expiry"
    back_marker: str | None = None                          # MRZ prefix that identifies the card's MRZ
    mrz_on_front: bool = False                              # the MRZ shares the portrait side (real KH ID)
    # Critical fields that a fully check-digit-valid MRZ makes non-blocking: their absence means
    # REVIEW instead of recapture (e.g. a Khmer name the OCR cannot read when the MRZ identifies the card).
    mrz_relieves: tuple[str, ...] = ()
    expiry_printed: bool = True                             # False: a missing expiry is NOT_APPLICABLE
    sides: tuple[str, ...] = ("FRONT", "BACK")
    mrz_formats: tuple[str, ...] = ()                       # expected ICAO formats, e.g. ("TD1",)
    mrz_regions: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)  # side → band
    latin_name_from: tuple[str, str] | None = None          # (given names field, surname field)
    nationality: str | None = "KH"                           # derived from the document type; None → from MRZ
    latin_name_line: bool = True                             # look for an unlabelled Latin name line
    numeric_refinement: NumericRefinement | None = KHMER_NUMERIC_REFINEMENT  # None for Latin-numeral documents
    viz_regions: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)  # side → OCR region
    mrz_document_code: str | None = None                     # e.g. "ID" or "P" (first MRZ characters)
    mrz_issuing_state: str | None = None                     # e.g. "KHM"
    ocr_languages: tuple[str, ...] | None = None              # visual-zone OCR models; None → service default
    barcode_expected: bool = False                            # True: a missing barcode is a REVIEW signal
    portrait_regions: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def find_label(text: str, label: str, threshold: float) -> tuple[int, int, bool] | None:
    """(start, end, fuzzy) of the best match of label inside text."""
    # Case varies across printed English labels. ASCII translation preserves the
    # original character offsets for slicing bilingual OCR lines.
    text, label = text.translate(_ASCII_LOWER), label.translate(_ASCII_LOWER)
    index = text.find(label)
    if index >= 0:
        return index, index + len(label), False
    size, best = len(label), None
    for width in {size - 1, size, size + 1}:
        for start in range(0, max(1, len(text) - width + 1)):
            score = _similar(text[start:start + width], label)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, start, start + width)
    return (best[1], best[2], True) if best else None


class KhmerLabelAdapter:
    layout: CardLayout
    policy: AdapterPolicy

    def __init__(self, policy: AdapterPolicy | None = None):
        self.policy = policy or type(self).policy
        self.version = self.policy.version
        self.document_type = self.layout.document_type
        self.numeric_refinement = self.layout.numeric_refinement

    def supports(self, classification: DocumentClassification) -> bool:
        return classification.document_type == self.document_type

    def required_sides(self) -> tuple[str, ...]:
        return self.layout.sides

    # Classification -----------------------------------------------------------------
    def _cue_hits(self, text: str, cues: tuple[str, ...]) -> int:
        upper = text.upper()
        return sum(1 for cue in cues if cue in upper or find_label(text, cue, self.policy.label_similarity))

    def _label_hits(self, lines: list[OCRLine]) -> set[str]:
        return {name for line in lines for name, variants in self.layout.labels.items()
                if any(find_label(line.text, variant, self.policy.label_similarity) for variant in variants)}

    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification:
        layout = self.layout
        text = " ".join(line.text for line in lines)
        own = self._cue_hits(text, TITLE_CUES.get(layout.document_type, ()))
        rivals = {kind: self._cue_hits(text, cues) for kind, cues in TITLE_CUES.items() if kind != layout.document_type}
        rival, rival_hits = max(rivals.items(), key=lambda item: item[1]) if rivals else (None, 0)
        if rival is not None and rival_hits > own:
            family = "PASSPORT" if rival == DocumentType.PASSPORT else "OTHER"
            return DocumentClassification(country="KH", document_family=family, document_type=rival,
                                          document_side="UNKNOWN", confidence=0.7)
        headers = own + self._cue_hits(text, KINGDOM_CUES) + sum(1 for cue in LATIN_KINGDOM_CUES if cue in text.upper())
        labels = self._label_hits(lines)
        digits = khmer.digits_to_ascii(text).value
        has_number = any(re.search(rule.pattern, digits) for rule in layout.numbers)
        front = min(0.3, 0.15 * headers) + min(0.5, 0.1 * len(labels)) + (0.1 if has_number else 0)
        front += 0.1 if any(LATIN_NAME_LINE.match(line.text) for line in lines) else 0
        if not labels and not has_number:
            front = 0.0  # headings alone (often printed on the back too) do not make a front
        mrz = [clean_mrz(line.text) for line in mrz_candidates(lines)]
        marked = bool(layout.back_marker) and any(item.startswith(layout.back_marker) for item in mrz)
        back = 0.0
        if mrz and layout.mrz_on_front:
            # The card's own MRZ is printed under the portrait: evidence of the front, never of the back.
            front += 0.4 if marked else 0.1
        elif mrz:
            back = 0.7 if marked else 0.35
        back += min(0.2, 0.1 * headers) if not labels else 0
        if front >= back and front > 0:
            side, confidence = "FRONT", min(1.0, front)
        elif back > 0:
            side, confidence = "BACK", min(1.0, back)
        else:
            side, confidence = "UNKNOWN", 0.0
        if layout.sides == ("DATA_PAGE",) and side in ("FRONT", "BACK") and confidence > 0:
            side = "DATA_PAGE"  # a passport data page is a single page holding both zones
        return DocumentClassification(country="KH", document_family=layout.family,
                                      document_type=layout.document_type if confidence > 0 else DocumentType.UNKNOWN,
                                      document_side=side, document_version=None, confidence=round(confidence, 3))

    # Extraction ---------------------------------------------------------------------
    def _field(self, name: str, raw: str | None, normalized: khmer.Normalized, line: OCRLine | None,
               side: str, extra_flags: tuple[str, ...] = ()) -> OCRField:
        # Only notes from the words that make up this value apply to it.
        notes = tuple(note for word in (line.words if line else ()) if raw and word.text in raw
                      for note in word.notes if note in FLAG_PENALTY)
        flags = tuple(dict.fromkeys(normalized.flags + extra_flags + notes))
        confidence = line.confidence if line else 0.0
        for flag in flags:
            confidence *= FLAG_PENALTY.get(flag, 1.0)
        if normalized.value is None:
            confidence = 0.0
        return OCRField(field=name, raw_value=raw, normalized_value=normalized.value, confidence=round(confidence, 3),
                        bbox=line.bbox if line else None, side=side, flags=flags)

    def _missing(self, name: str, side: str = "FRONT") -> OCRField:
        return self._field(name, None, khmer.Normalized(None, ("NOT_FOUND",)), None, side)

    def _marks(self, line: OCRLine) -> list[tuple[int, int, str, bool]]:
        marks = []
        for name, variants in self.layout.labels.items():
            found = [match for variant in variants
                     if (match := find_label(line.text, variant, self.policy.label_similarity))]
            if not found:
                continue
            # At one position the exact, longest variant wins ("Given names" over "Given name").
            found.sort(key=lambda match: (match[0], match[2], -match[1]))
            start, end, fuzzy = found[0]
            for other_start, other_end, other_fuzzy in found[1:]:
                # Bilingual labels ("Surname / នាមត្រកូល") are one label when only separators lie between.
                if other_start >= end and re.fullmatch(r"[\s/|:,.\-]*", line.text[end:other_start]):
                    end, fuzzy = other_end, fuzzy and other_fuzzy
            marks.append((start, end, name, fuzzy))
        # Overlapping labels: an exact match beats a fuzzy one ("Date of birth" over a fuzzy "Place of birth"),
        # then the longer label wins ("ឈ្មោះសហគ្រាស" over the "ឈ្មោះ" inside it).
        kept: list[tuple[int, int, str, bool]] = []
        for mark in sorted(marks, key=lambda item: (item[3], -(item[1] - item[0]))):
            if all(mark[1] <= other[0] or mark[0] >= other[1] for other in kept):
                kept.append(mark)
        return sorted(kept)

    def _labelled_values(self, lines: list[OCRLine]) -> dict[str, tuple[str, OCRLine, tuple[str, ...]]]:
        """Map each label to the text that follows it (same line, else next line), up to the next label."""
        return {name: occurrences[0] for name, occurrences in self._labelled_occurrences(lines).items()}

    def _labelled_occurrences(self, lines: list[OCRLine]) -> dict[str, list[tuple[str, OCRLine, tuple[str, ...]]]]:
        found: dict[str, list[tuple[str, OCRLine, tuple[str, ...]]]] = {}
        # Latin text below a Khmer-script label is the next field (often the Latin name), not its value.
        khmer_labels = {label for label, _, normalizer in self.layout.text_fields if normalizer == "khmer"}
        located = [self._marks(line) for line in lines]
        label_lines = {index for index, marks in enumerate(located) if marks}
        for index, marks in enumerate(located):
            line = lines[index]
            for position, (start, end, name, fuzzy) in enumerate(marks):
                latin_guard = name in khmer_labels
                stop = marks[position + 1][0] if position + 1 < len(marks) else len(line.text)
                value = line.text[end:stop].strip(" :;")
                flags = ("FUZZY_LABEL",) if fuzzy else ()
                if value.startswith("/"):
                    # "Date of birth / <garbled Khmer>": the rest is the label's other language, not a value.
                    value, flags = "", flags + ("LABEL_SECOND_LANGUAGE_UNREAD",)
                source = line
                following_text = lines[index + 1].text if index + 1 < len(lines) else ""
                if not value and following_text and index + 1 not in label_lines \
                        and not (latin_guard and LATIN_NAME_LINE.match(following_text)) \
                        and not MRZ_LINE.match(following_text.replace(" ", "")):
                    source, value, flags = lines[index + 1], following_text.strip(" :;"), flags + ("VALUE_ON_NEXT_LINE",)
                for extra in range(1, self.layout.multiline.get(name, 0) + 1):
                    follow = index + extra + (1 if "VALUE_ON_NEXT_LINE" in flags else 0)
                    if follow >= len(lines) or follow in label_lines \
                            or (latin_guard and LATIN_NAME_LINE.match(lines[follow].text)) \
                            or MRZ_LINE.match(lines[follow].text.replace(" ", "")):
                        break
                    value = f"{value} {lines[follow].text}".strip()
                    following = lines[follow]
                    # A wrapped value is only as trustworthy as its weakest line.
                    source = OCRLine(text=value, confidence=min(source.confidence, following.confidence),
                                     bbox=(min(source.bbox[0], following.bbox[0]), min(source.bbox[1], following.bbox[1]),
                                           max(source.bbox[2], following.bbox[2]), max(source.bbox[3], following.bbox[3])))
                found.setdefault(name, []).append((value, source, flags))
        return found

    def _number(self, rule: NumberRule, front: list[OCRLine], values: dict, occurrences: dict | None = None) -> OCRField:
        candidates = []
        side = self.layout.sides[0]
        if rule.label is not None:
            # A label can also match title text; use the first occurrence that actually holds a number.
            entries = (occurrences or {}).get(rule.label) or ([values[rule.label]] if rule.label in values else [])
            for raw, line, flags in entries:
                converted = khmer.digits_to_ascii(raw)
                match = re.search(rule.pattern, re.sub(r"[\s\-]", "", converted.value))
                if match:
                    return self._field(rule.output, raw, khmer.Normalized(match.group(1), converted.flags), line, side, flags)
            if entries:
                raw, line, flags = entries[0]
                converted = khmer.digits_to_ascii(raw)
                return self._field(rule.output, raw, khmer.Normalized(None, converted.flags + ("NUMBER_NOT_FOUND",)), line, side, flags)
            return self._missing(rule.output, side)
        labelled_lines = {id(entry[1]) for entry in values.values()}
        for line in front:
            if id(line) in labelled_lines or khmer.DATE_PATTERN.search(line.text):
                continue
            converted = khmer.digits_to_ascii(line.text)
            for match in re.finditer(rule.pattern, converted.value):
                candidates.append((line.bbox[1], match.group(1), line, converted.flags, line.text))
        candidates.sort(key=lambda item: item[0])
        if not candidates:
            return self._missing(rule.output)
        _, number, line, flags, raw = candidates[0]
        extra = ("AMBIGUOUS_CANDIDATES",) if len({item[1] for item in candidates}) > 1 else ()
        return self._field(rule.output, raw, khmer.Normalized(number, flags), line, "FRONT", extra)

    def extract_fields(self, lines_by_side: dict[str, list[OCRLine]]) -> IdentityDocument:
        layout = self.layout
        primary_side = layout.sides[0]
        front = lines_by_side.get(primary_side, [])
        back = [line for side, lines in lines_by_side.items() if side != primary_side for line in lines]
        occurrences = self._labelled_occurrences([line for line in front if "MRZ_PASS" not in line.notes])
        values = {name: entries[0] for name, entries in occurrences.items()}
        fields: list[OCRField] = []
        for label, output, normalizer in layout.text_fields:
            raw, line, flags = values.get(label, (None, None, ()))
            normalized = NORMALIZERS[normalizer](raw) if raw else khmer.Normalized(None, ("NOT_FOUND",))
            fields.append(self._field(output, raw, normalized, line, primary_side, flags))

        if layout.validity_label:
            raw, line, flags = values.get(layout.validity_label, (None, None, ()))
            dates = khmer.find_dates(raw or "")
            for position, name in enumerate(("issue_date", "expiry_date")):
                fields.append(self._field(name, dates[position][0], dates[position][1], line, primary_side, flags)
                              if position < len(dates) else self._missing(name, primary_side))
        for rule in layout.numbers:
            fields.append(self._number(rule, front, values, occurrences))

        # Latin name: the uppercase Latin line nearest below the Khmer name label.
        anchor = values.get("full_name_local", (None, None, ()))[1]
        title_words = tuple(cue for cues in TITLE_CUES.values() for cue in cues) + LATIN_KINGDOM_CUES
        latin = [line for line in front if layout.latin_name_line and LATIN_NAME_LINE.match(line.text)
                 and len(line.text.split()) >= 2 and not any(cue in line.text for cue in title_words)]
        if anchor is not None:
            below = [line for line in latin if line.bbox[1] >= anchor.bbox[1]]
            latin = sorted(below or latin, key=lambda line: abs(line.bbox[1] - anchor.bbox[3]))
        if not any(item.field == "full_name" for item in fields):
            fields.append(self._field("full_name", latin[0].text, khmer.latin_name(latin[0].text), latin[0], primary_side)
                          if latin else self._missing("full_name", primary_side))

        mrz_side = next(iter(layout.mrz_regions), "BACK")
        pool = front + back if layout.mrz_formats else back
        # Lines from the dedicated MRZ pass and MRZ-like lines from the general pass, in reading order;
        # the assembler picks the combination whose check digits validate.
        mrz_lines = mrz_candidates(pool)
        parsed = mrz_parser.read([line.text for line in mrz_lines]) if mrz_lines else None
        mrz_text = "\n".join(parsed.lines) if parsed else ("\n".join(line.text.replace(" ", "") for line in mrz_lines) or None)
        if mrz_lines:
            # When every check digit validates they verify the reading better than the OCR engine's own score
            # (constrained reads often report 0); TD3/TD2 name lines carry no check digit, hence not 1.0.
            line_confidence = 0.95 if parsed and parsed.mrz_valid else round(min(line.confidence for line in mrz_lines), 3)
            flags = tuple(parsed.flags) if parsed else ("MRZ_FORMAT_UNRECOGNIZED",)
            if parsed:
                flags += ("CHECK_DIGITS_VALID",) if all(item["valid"] for item in parsed.check_digits.values()) else ("CHECK_DIGIT_FAILED",)
            used = []
            for assembled in parsed.lines if parsed else ():
                matching = [line for line in mrz_lines if clean_mrz(line.text) == assembled
                            or clean_mrz(line.text).rstrip("<") == assembled.rstrip("<")]
                if matching:
                    used.append(matching[0])
            sources = used or mrz_lines
            fields.append(OCRField(field="mrz", raw_value="\n".join(line.text for line in sources), normalized_value=mrz_text,
                                   confidence=line_confidence, bbox=_bounds(sources), side=mrz_side, flags=flags))
        if parsed:
            self._fill_from_mrz(fields, parsed, mrz_side, used or mrz_lines)
        if layout.latin_name_from:
            self._compose_name(fields, primary_side)
        if layout.nationality:
            fields.append(OCRField(field="nationality", raw_value=None, normalized_value=layout.nationality, confidence=1.0,
                                   side=None, source="DERIVED", flags=("DERIVED_FROM_DOCUMENT_TYPE",)))

        by_name = {item.field: item for item in fields}
        value = lambda name: by_name[name].normalized_value if name in by_name else None  # noqa: E731
        return IdentityDocument(
            document_type=layout.document_type, issuing_country="KH" if layout.nationality == "KH" else None,
            document_number=value("document_number"),
            full_name=value("full_name"), full_name_local=value("full_name_local"),
            date_of_birth=value("date_of_birth"), sex=value("sex"), nationality=value("nationality"),
            place_of_birth=value("place_of_birth"), address=value("address"),
            given_names=value("given_names"), surname=value("surname"),
            issue_date=value("issue_date"), expiry_date=value("expiry_date"), mrz=mrz_text, fields=fields)

    def _compose_name(self, fields: list[OCRField], side: str) -> None:
        sources = [item for name in self.layout.latin_name_from
                   for item in fields if item.field == name and item.normalized_value]
        if not sources:
            return
        source_kinds = {item.source for item in sources}
        source = next(iter(source_kinds)) if len(source_kinds) == 1 else "DERIVED"
        combined = OCRField(field="full_name", raw_value=" ".join(item.raw_value for item in sources if item.raw_value) or None,
                            normalized_value=" ".join(item.normalized_value for item in sources),
                            confidence=min(item.confidence for item in sources), bbox=_bounds(sources), side=side,
                            source=source, flags=tuple(dict.fromkeys(flag for item in sources for flag in item.flags)))
        existing = next((item for item in fields if item.field == "full_name"), None)
        if existing is not None:
            fields[fields.index(existing)] = combined
        else:
            fields.append(combined)

    def _mrz_layout_reasons(self, parsed: "mrz_parser.MRZResult") -> list[str]:
        layout, reasons = self.layout, []
        if not layout.mrz_formats and not layout.back_marker:
            reasons.append("MRZ_UNEXPECTED_ON_DOCUMENT")
        if layout.mrz_formats and parsed.format not in layout.mrz_formats:
            reasons.append("MRZ_FORMAT_UNEXPECTED")
        if layout.mrz_document_code and not parsed.document_code.startswith(layout.mrz_document_code):
            reasons.append("MRZ_DOCUMENT_CODE_UNEXPECTED")
        if layout.mrz_issuing_state and parsed.issuing_state != layout.mrz_issuing_state:
            reasons.append("MRZ_ISSUING_STATE_UNEXPECTED")
        return reasons

    def _fill_from_mrz(self, fields: list[OCRField], parsed: "mrz_parser.MRZResult", side: str,
                       sources: list[OCRLine]) -> None:
        """The visual value stays canonical; the MRZ fills only fields the visual zone lacks.

        A field filled this way needs its own check digit to be valid (sex and names have none,
        so they need the whole MRZ to be valid). Those unprotected fields are still OCR
        readings; their flags do not claim checksum verification. Disagreement is
        reported by MRZ_CONSISTENCY. A different document layout never supplies fields.
        """
        if self._mrz_layout_reasons(parsed) or not getattr(parsed, "data_valid", True):
            return
        candidates = {"document_number": (parsed.document_number, "document_number"),
                      "date_of_birth": (parsed.date_of_birth and parsed.date_of_birth.isoformat(), "date_of_birth"),
                      "expiry_date": (parsed.expiry_date and parsed.expiry_date.isoformat(), "expiry_date"),
                      "sex": (parsed.sex, None), "surname": (parsed.surname, None),
                      "given_names": (parsed.given_names, None), "full_name": (parsed.full_name, None)}
        if self.layout.latin_name_from:
            candidates.pop("full_name")  # compose after fallback, retaining visual name components
        if not self.layout.nationality:
            candidates["nationality"] = (parsed.nationality, None)
            candidates["issuing_state"] = (parsed.issuing_state.replace("<", "") or None, None)
        for name, (value, digit) in candidates.items():
            existing = next((item for item in fields if item.field == name), None)
            if value is None or (existing is not None and existing.normalized_value):
                continue
            trusted = parsed.check_digits.get(digit, {}).get("valid") if digit else parsed.mrz_valid
            if not trusted:
                continue
            name_line = 2 if parsed.format == "TD1" else 0
            data_line = 1
            position = name_line if name in ("surname", "given_names", "full_name") else data_line
            if name == "issuing_state" or (name == "document_number" and parsed.format == "TD1"):
                position = 0
            bbox = sources[position].bbox if position < len(sources) else _bounds(sources)
            flags = ("FROM_MRZ", "CHECK_DIGIT_VALID") if digit else ("FROM_MRZ", "MRZ_VALID", "NOT_CHECK_DIGIT_PROTECTED")
            filled = OCRField(field=name, raw_value=None, normalized_value=value, confidence=0.99 if digit else 0.95,
                              bbox=bbox, side=side, source="MRZ", flags=flags)
            if existing is not None:
                if existing.raw_value:
                    fields.append(existing.model_copy(update={"field": name + "_visual"}))
                fields[fields.index(existing)] = filled
            else:
                fields.append(filled)

    # Validation ---------------------------------------------------------------------
    def validate_fields(self, document: IdentityDocument, today: date) -> list[CheckEvidence]:
        p, layout = self.policy, self.layout
        by_name = {item.field: item for item in document.fields}
        present = lambda name: bool(getattr(document, name)) if name in CANONICAL else bool(  # noqa: E731
            by_name[name].normalized_value if name in by_name else None)
        checks: list[CheckEvidence] = []

        mrz_field = by_name.get("mrz")
        mrz_verified = bool(mrz_field and "CHECK_DIGITS_VALID" in mrz_field.flags)
        relieved = set(layout.mrz_relieves) if mrz_verified else set()
        missing_critical = [name for name in layout.critical_fields if not present(name) and name not in relieved]
        missing_important = [name for name in layout.important_fields if not present(name)] + \
            [name for name in layout.critical_fields if not present(name) and name in relieved]
        if missing_critical:
            checks.append(CheckEvidence(CheckResult.FAIL, ("CRITICAL_FIELD_MISSING",), check_type="REQUIRED_FIELDS",
                                        details={"missing": missing_critical + missing_important}))
        elif missing_important:
            checks.append(CheckEvidence(CheckResult.REVIEW, ("FIELD_MISSING",), check_type="REQUIRED_FIELDS",
                                        details={"missing": missing_important}))
        else:
            checks.append(CheckEvidence(CheckResult.PASS, ("REQUIRED_FIELDS_PRESENT",), check_type="REQUIRED_FIELDS"))

        for rule in layout.numbers:
            number = by_name.get(rule.output)
            if number and number.normalized_value:
                ok = bool(re.fullmatch(rule.format, number.normalized_value))
                prefix = rule.check_type.removesuffix("_FORMAT")
                checks.append(CheckEvidence(CheckResult.PASS if ok else CheckResult.REVIEW,
                                            (f"{prefix}_FORMAT_VALID" if ok else f"{prefix}_FORMAT_UNEXPECTED",),
                                            check_type=rule.check_type))

        problems = []
        dob, issue, expiry = document.date_of_birth, document.issue_date, document.expiry_date
        if dob and (dob > today or (today.year - dob.year) > p.max_age_years):
            problems.append("DATE_OF_BIRTH_IMPOSSIBLE")
        if issue and issue > today:
            problems.append("ISSUE_DATE_IN_FUTURE")
        if issue and dob and issue < dob:
            problems.append("ISSUED_BEFORE_BIRTH")
        if issue and expiry and expiry <= issue:
            problems.append("EXPIRY_NOT_AFTER_ISSUE")
        if issue and expiry and expiry > issue and p.typical_validity_years:
            years = (expiry - issue).days / 365.25
            if not (p.typical_validity_years[0] <= years <= p.typical_validity_years[1]):
                problems.append("UNUSUAL_VALIDITY_PERIOD")
        checks.append(CheckEvidence(CheckResult.REVIEW if problems else CheckResult.PASS,
                                    tuple(problems) or ("DATES_CONSISTENT",), check_type="DATE_CONSISTENCY"))

        if expiry is None and not layout.expiry_printed:
            checks.append(CheckEvidence(CheckResult.NOT_APPLICABLE, ("EXPIRY_NOT_PRINTED",), check_type="EXPIRY"))
        elif expiry is None:
            checks.append(CheckEvidence(CheckResult.REVIEW, ("EXPIRY_UNKNOWN",), check_type="EXPIRY"))
        elif expiry < today:
            checks.append(CheckEvidence(CheckResult.FAIL, ("EXPIRED_DOCUMENT",), check_type="EXPIRY"))
        else:
            checks.append(CheckEvidence(CheckResult.PASS, ("DOCUMENT_NOT_EXPIRED",), check_type="EXPIRY"))

        low = sorted(item.field for item in document.fields
                     if item.source == "OCR" and item.normalized_value and item.confidence < p.min_field_confidence)
        checks.append(CheckEvidence(CheckResult.REVIEW if low else CheckResult.PASS,
                                    ("LOW_OCR_CONFIDENCE",) if low else ("OCR_CONFIDENCE_OK",),
                                    check_type="OCR_CONFIDENCE", details={"fields": low}))
        mixed = sorted(name for name, item in by_name.items() if "MIXED_DIGIT_SCRIPTS" in item.flags)
        checks.append(CheckEvidence(CheckResult.REVIEW if mixed else CheckResult.PASS,
                                    ("MIXED_DIGIT_SCRIPTS",) if mixed else ("SCRIPT_CONSISTENT",),
                                    check_type="SCRIPT_CONSISTENCY", details={"fields": mixed}))

        checks.extend(self._mrz_checks(document, today))
        checks.append(self.parse_barcode(b""))  # replaced by barcode.evaluate when the service decodes codes
        checks.append(CheckEvidence(CheckResult.UNAVAILABLE, ("PORTRAIT_ENGINE_PHASE_8",), check_type="PORTRAIT"))
        return checks

    def extract_portrait(self, side_image):
        return None  # Face detection and portrait templates arrive in Phases 8–9.

    def _mrz_checks(self, document: IdentityDocument, today: date | None = None) -> list[CheckEvidence]:
        mrz = self.parse_mrz(document.mrz or "", today)
        checks = [mrz]
        # Registry adapters are reused between sessions. Compare this document's MRZ,
        # never a previous extraction cached on the adapter instance.
        parsed = mrz_parser.read((document.mrz or "").splitlines(), today) if document.mrz else None
        if mrz.result in (CheckResult.PASS, CheckResult.REVIEW) and parsed is not None:
            visual = {item.field: item.normalized_value for item in document.fields if item.source != "MRZ"}
            by_name = {item.field: item for item in document.fields}
            mixed_name = self.layout.latin_name_from and any(
                by_name.get(name) and by_name[name].source == "MRZ" for name in self.layout.latin_name_from)
            if mixed_name:
                # A composed name can contain MRZ fallback components. Compare the
                # independently read components instead of treating the composition
                # as an entirely visual name.
                visual.pop("full_name", None)
            not_compared: list[str] = []
            printed_nationality = visual.get("nationality_printed")
            if printed_nationality:
                aliases = {"KH", "KHM", "CAMBODIAN", "CAMBODIA", "ខ្មែរ", "កម្ពុជា"}
                normalized = khmer.clean(printed_nationality).upper()
                parts = [part.strip() for part in normalized.split("/")]
                if all(part in aliases for part in parts):
                    visual["nationality"] = "KH"
                elif re.fullmatch(r"[A-Z]{3}", normalized) or self.layout.nationality:
                    # A printed code is comparable; on a single-country document any other wording is a disagreement.
                    visual["nationality"] = normalized
                else:
                    # A demonym ("UTOPIAN") cannot be checked against a code without a translation table.
                    visual.pop("nationality", None)
                    not_compared.append("nationality")
            elif self.layout.nationality:
                visual["nationality"] = document.nationality
            consistency = mrz_parser.compare(parsed, visual)
            for name in not_compared:
                consistency[name] = "NOT_COMPARED"
            if mixed_name:
                for name, other in (("surname", "given_names"), ("given_names", "surname")):
                    if visual.get(name):
                        component = replace(parsed, **{other: None})
                        state = mrz_parser.compare(component, {"full_name": visual[name]}).get("full_name")
                        if state:
                            consistency[name] = state
            mismatches = sorted(name for name, state in consistency.items() if state == "MISMATCH")
            compared = any(state in ("MATCH", "MISMATCH") for state in consistency.values())
            reasons = tuple(f"MRZ_VISUAL_{name.upper()}_MISMATCH" for name in mismatches)
            reasons = reasons or (("MRZ_VISUAL_CONSISTENT",) if compared else ("MRZ_VISUAL_COMPARISON_UNAVAILABLE",))
            checks.append(CheckEvidence(
                CheckResult.REVIEW if mismatches else CheckResult.PASS if compared else CheckResult.NOT_APPLICABLE,
                reasons,
                check_type="MRZ_CONSISTENCY", details={"field_consistency": consistency}))
        return checks

    def parse_mrz(self, text: str, today: date | None = None) -> CheckEvidence:
        layout = self.layout
        if not text:
            if not layout.mrz_formats and not layout.back_marker:
                return CheckEvidence(CheckResult.NOT_APPLICABLE, ("NO_MRZ_ON_DOCUMENT",), check_type="MRZ")
            return CheckEvidence(CheckResult.REVIEW, ("MRZ_NOT_FOUND",), check_type="MRZ")
        parsed = mrz_parser.read(text.splitlines(), today)
        if parsed is None:
            return CheckEvidence(CheckResult.REVIEW, ("MRZ_FORMAT_UNRECOGNIZED",), check_type="MRZ")
        details = {"format": parsed.format, "mrz_valid": parsed.mrz_valid, "data_valid": getattr(parsed, "data_valid", True),
                   "check_digit_results": parsed.check_digits,
                   "substitutions": parsed.substitutions, "flags": parsed.flags}
        reasons = self._mrz_layout_reasons(parsed)
        if not all(item["valid"] for item in parsed.check_digits.values()):
            reasons.append("MRZ_CHECK_DIGIT_FAILED")
        if not getattr(parsed, "data_valid", True):
            reasons.append("MRZ_DATA_INVALID")
        # Valid check digits mean the MRZ is internally consistent, never that the document is genuine.
        return CheckEvidence(CheckResult.REVIEW if reasons else CheckResult.PASS,
                             tuple(reasons) or ("MRZ_CHECK_DIGITS_VALID",), check_type="MRZ", details=details)

    def parse_barcode(self, payload: bytes) -> CheckEvidence:
        """Format and signature evidence for one payload; consistency needs the document (barcode.evaluate)."""
        from kyc.barcode.payload import parse
        from kyc.barcode.signatures import TrustStore, verify

        if not payload:
            return CheckEvidence(CheckResult.NOT_APPLICABLE, ("NO_BARCODE_PAYLOAD",), check_type="BARCODE")
        parsed = parse(payload, payload.decode("utf-8", errors="replace"))
        valid, reason = verify(parsed, TrustStore())
        result = CheckResult.FAIL if valid is False else CheckResult.REVIEW if parsed.format_valid is False else CheckResult.PASS
        return CheckEvidence(result, (reason if valid is not None else f"BARCODE_FORMAT_{parsed.format}",), check_type="BARCODE",
                             details={"format": parsed.format, "format_valid": parsed.format_valid,
                                      "signature_present": parsed.signature_present, "signature_valid": valid})

    def parse_qr(self, payload: bytes) -> CheckEvidence:
        return self.parse_barcode(payload)

    def get_security_checks(self) -> tuple[str, ...]:
        return ("REQUIRED_FIELDS", *(rule.check_type for rule in self.layout.numbers), "DATE_CONSISTENCY", "EXPIRY",
                "OCR_CONFIDENCE", "SCRIPT_CONSISTENCY", "MRZ", "MRZ_CONSISTENCY", "BARCODE", "PORTRAIT")
