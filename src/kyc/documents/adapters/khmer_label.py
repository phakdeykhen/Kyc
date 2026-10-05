"""Shared engine for Cambodian cards whose fields follow printed Khmer labels.

A card type is described by a CardLayout (labels, number rules, date rules, cues);
KhmerLabelAdapter does the work: fuzzy label matching, value extraction with
provenance, classification against rival card types, and validation. New Khmer
card types are new layouts, not new extraction code.

Rules that hold for every layout: low-confidence or mixed-script values are flagged,
never guessed; values a card does not carry stay null; engines that are not built
yet report UNAVAILABLE, never PASS.
"""

from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
import re

from kyc.documents import khmer
from kyc.documents.refine import NumericRefinement
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import DocumentClassification, IdentityDocument, OCRField, OCRLine
from kyc.engines.contracts import CheckEvidence

MRZ_LINE = re.compile(r"^[A-Z0-9<]{25,44}$")
LATIN_NAME_LINE = re.compile(r"^[A-Z][A-Z' \-]{2,}$")
KINGDOM_CUES = ("ព្រះរាជាណាចក្រកម្ពុជា", "ជាតិ សាសនា ព្រះមហាក្សត្រ")
LATIN_KINGDOM_CUES = ("KINGDOM OF CAMBODIA",)
# Distinctive titles per document type. Used to notice a different card than the one claimed.
TITLE_CUES: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.KH_NATIONAL_ID: ("អត្តសញ្ញាណប័ណ្ណ", "IDENTITY CARD"),
    DocumentType.KH_NSSF: ("បេឡាជាតិរបបសន្តិសុខសង្គម", "ប័ណ្ណសមាជិក", "ប.ស.ស", "NATIONAL SOCIAL SECURITY FUND", "NSSF"),
    DocumentType.PASSPORT: ("PASSPORT", "លិខិតឆ្លងដែន", "P<KHM"),
}
FLAG_PENALTY = {"MIXED_DIGIT_SCRIPTS": 0.6, "FUZZY_LABEL": 0.9, "AMBIGUOUS_CANDIDATES": 0.7,
                "NON_NAME_CHARACTERS_REMOVED": 0.85, "EXPECTED_KHMER_SCRIPT": 0.6, "VALUE_ON_NEXT_LINE": 0.95,
                "OCR_PASSES_DISAGREED": 0.9, "NUMERIC_REREAD_REJECTED": 0.8}
# Cambodian cards print Khmer numerals; numeric words get a second read restricted to them.
KHMER_NUMERIC_REFINEMENT = NumericRefinement(languages=("script/Khmer",), alphabet=khmer.KHMER_DIGITS + "./")
NORMALIZERS = {"khmer": khmer.khmer_text, "date": khmer.parse_date, "sex": khmer.parse_sex}


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
    back_marker: str | None = None                          # MRZ prefix printed on the back
    expiry_printed: bool = True                             # False: a missing expiry is NOT_APPLICABLE
    sides: tuple[str, ...] = ("FRONT", "BACK")
    extra_canonical: dict[str, str] = field(default_factory=dict)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def find_label(text: str, label: str, threshold: float) -> tuple[int, int, bool] | None:
    """(start, end, fuzzy) of the best match of label inside text."""
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
        self.numeric_refinement = KHMER_NUMERIC_REFINEMENT

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
        mrz = [line.text.replace(" ", "") for line in lines if MRZ_LINE.match(line.text.replace(" ", ""))]
        back = 0.0
        if mrz:
            back = 0.7 if layout.back_marker and any(item.startswith(layout.back_marker) for item in mrz) else 0.35
        back += min(0.2, 0.1 * headers) if not labels else 0
        if front >= back and front > 0:
            side, confidence = "FRONT", min(1.0, front)
        elif back > 0:
            side, confidence = "BACK", min(1.0, back)
        else:
            side, confidence = "UNKNOWN", 0.0
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
            for variant in variants:
                match = find_label(line.text, variant, self.policy.label_similarity)
                if match:
                    marks.append((match[0], match[1], name, match[2]))
                    break
        # A short label inside a longer one ("ឈ្មោះ" within "ឈ្មោះសហគ្រាស") belongs to the longer label.
        marks = [mark for mark in marks if not any(other is not mark and other[0] <= mark[0] and mark[1] <= other[1]
                                                    and (other[1] - other[0]) > (mark[1] - mark[0]) for other in marks)]
        return sorted(marks)

    def _labelled_values(self, lines: list[OCRLine]) -> dict[str, tuple[str, OCRLine, tuple[str, ...]]]:
        """Map each label to the text that follows it (same line, else next line), up to the next label."""
        found: dict[str, tuple[str, OCRLine, tuple[str, ...]]] = {}
        located = [self._marks(line) for line in lines]
        label_lines = {index for index, marks in enumerate(located) if marks}
        for index, marks in enumerate(located):
            line = lines[index]
            for position, (start, end, name, fuzzy) in enumerate(marks):
                if name in found:
                    continue
                stop = marks[position + 1][0] if position + 1 < len(marks) else len(line.text)
                value = line.text[end:stop].strip(" :;")
                flags = ("FUZZY_LABEL",) if fuzzy else ()
                source = line
                following_text = lines[index + 1].text if index + 1 < len(lines) else ""
                if not value and following_text and index + 1 not in label_lines \
                        and not LATIN_NAME_LINE.match(following_text) and not MRZ_LINE.match(following_text.replace(" ", "")):
                    source, value, flags = lines[index + 1], following_text.strip(" :;"), flags + ("VALUE_ON_NEXT_LINE",)
                for extra in range(1, self.layout.multiline.get(name, 0) + 1):
                    follow = index + extra + (1 if "VALUE_ON_NEXT_LINE" in flags else 0)
                    if follow >= len(lines) or follow in label_lines or LATIN_NAME_LINE.match(lines[follow].text) \
                            or MRZ_LINE.match(lines[follow].text.replace(" ", "")):
                        break
                    value = f"{value} {lines[follow].text}".strip()
                    following = lines[follow]
                    # A wrapped value is only as trustworthy as its weakest line.
                    source = OCRLine(text=value, confidence=min(source.confidence, following.confidence),
                                     bbox=(min(source.bbox[0], following.bbox[0]), min(source.bbox[1], following.bbox[1]),
                                           max(source.bbox[2], following.bbox[2]), max(source.bbox[3], following.bbox[3])))
                found[name] = (value, source, flags)
        return found

    def _number(self, rule: NumberRule, front: list[OCRLine], values: dict) -> OCRField:
        candidates = []
        if rule.label is not None:
            raw, line, flags = values.get(rule.label, (None, None, ()))
            if raw:
                converted = khmer.digits_to_ascii(raw)
                compact = re.sub(r"[\s\-]", "", converted.value)
                match = re.search(rule.pattern, compact)
                if match:
                    return self._field(rule.output, raw, khmer.Normalized(match.group(1), converted.flags), line, "FRONT", flags)
                return self._field(rule.output, raw, khmer.Normalized(None, converted.flags + ("NUMBER_NOT_FOUND",)), line, "FRONT", flags)
            return self._missing(rule.output)
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
        front, back = lines_by_side.get("FRONT", []), lines_by_side.get("BACK", [])
        values = self._labelled_values(front)
        fields: list[OCRField] = []
        for label, output, normalizer in layout.text_fields:
            raw, line, flags = values.get(label, (None, None, ()))
            normalized = NORMALIZERS[normalizer](raw) if raw else khmer.Normalized(None, ("NOT_FOUND",))
            fields.append(self._field(output, raw, normalized, line, "FRONT", flags))

        if layout.validity_label:
            raw, line, flags = values.get(layout.validity_label, (None, None, ()))
            dates = khmer.find_dates(raw or "")
            for position, name in enumerate(("issue_date", "expiry_date")):
                fields.append(self._field(name, dates[position][0], dates[position][1], line, "FRONT", flags)
                              if position < len(dates) else self._missing(name))
        for rule in layout.numbers:
            fields.append(self._number(rule, front, values))

        # Latin name: the uppercase Latin line nearest below the Khmer name label.
        anchor = values.get("full_name_local", (None, None, ()))[1]
        title_words = tuple(cue for cues in TITLE_CUES.values() for cue in cues) + LATIN_KINGDOM_CUES
        latin = [line for line in front if LATIN_NAME_LINE.match(line.text) and len(line.text.split()) >= 2
                 and not any(cue in line.text for cue in title_words)]
        if anchor is not None:
            below = [line for line in latin if line.bbox[1] >= anchor.bbox[1]]
            latin = sorted(below or latin, key=lambda line: abs(line.bbox[1] - anchor.bbox[3]))
        fields.append(self._field("full_name", latin[0].text, khmer.latin_name(latin[0].text), latin[0], "FRONT")
                      if latin else self._missing("full_name"))

        mrz_lines = [line for line in back if MRZ_LINE.match(line.text.replace(" ", ""))]
        mrz_text = "\n".join(line.text.replace(" ", "") for line in mrz_lines) or None
        if mrz_lines:
            fields.append(OCRField(field="mrz", raw_value=mrz_text, normalized_value=mrz_text,
                                   confidence=round(min(line.confidence for line in mrz_lines), 3), bbox=mrz_lines[0].bbox,
                                   side="BACK", flags=("UNPARSED_UNTIL_PHASE_5",)))
        fields.append(OCRField(field="nationality", raw_value=None, normalized_value="KH", confidence=1.0,
                               side=None, source="DERIVED", flags=("DERIVED_FROM_DOCUMENT_TYPE",)))

        by_name = {item.field: item for item in fields}
        value = lambda name: by_name[name].normalized_value if name in by_name else None  # noqa: E731
        return IdentityDocument(
            document_type=layout.document_type, issuing_country="KH", document_number=value("document_number"),
            full_name=value("full_name"), full_name_local=value("full_name_local"),
            date_of_birth=value("date_of_birth"), sex=value("sex"), nationality="KH",
            place_of_birth=value("place_of_birth"), address=value("address"),
            issue_date=value("issue_date"), expiry_date=value("expiry_date"), mrz=mrz_text, fields=fields)

    # Validation ---------------------------------------------------------------------
    def validate_fields(self, document: IdentityDocument, today: date) -> list[CheckEvidence]:
        p, layout = self.policy, self.layout
        by_name = {item.field: item for item in document.fields}
        present = lambda name: bool(by_name[name].normalized_value) if name in by_name else bool(getattr(document, name, None))  # noqa: E731
        checks: list[CheckEvidence] = []

        missing_critical = [name for name in layout.critical_fields if not present(name)]
        missing_important = [name for name in layout.important_fields if not present(name)]
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

        checks.append(self.parse_mrz(document.mrz or ""))
        checks.append(self.parse_barcode(b""))
        checks.append(CheckEvidence(CheckResult.UNAVAILABLE, ("PORTRAIT_ENGINE_PHASE_8",), check_type="PORTRAIT"))
        return checks

    def extract_portrait(self, side_image):
        return None  # Face detection and portrait templates arrive in Phases 8–9.

    def parse_mrz(self, text: str) -> CheckEvidence:
        if not self.layout.back_marker and not text:
            return CheckEvidence(CheckResult.NOT_APPLICABLE, ("NO_MRZ_ON_DOCUMENT",), check_type="MRZ")
        return CheckEvidence(CheckResult.UNAVAILABLE, ("MRZ_ENGINE_PHASE_5" if text else "MRZ_NOT_FOUND",), check_type="MRZ")

    def parse_barcode(self, payload: bytes) -> CheckEvidence:
        return CheckEvidence(CheckResult.UNAVAILABLE, ("BARCODE_ENGINE_PHASE_7",), check_type="BARCODE")

    def parse_qr(self, payload: bytes) -> CheckEvidence:
        return CheckEvidence(CheckResult.UNAVAILABLE, ("QR_ENGINE_PHASE_7",), check_type="QR")

    def get_security_checks(self) -> tuple[str, ...]:
        return ("REQUIRED_FIELDS", *(rule.check_type for rule in self.layout.numbers), "DATE_CONSISTENCY", "EXPIRY",
                "OCR_CONFIDENCE", "SCRIPT_CONSISTENCY", "MRZ", "BARCODE", "PORTRAIT")
