"""Cambodia National ID (Khmer Identity Card) adapter.

Extraction is anchored on the printed Khmer field labels rather than fixed pixel
coordinates, so small layout differences between card versions and OCR noise in the
labels are tolerated (fuzzy matching). The label set and the 9-digit ID-number
format are layout assumptions; they must be confirmed against official specimens
before production. MRZ, barcode and portrait engines are later phases, and those
checks report UNAVAILABLE rather than a fake PASS.
"""

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
import re

from kyc.documents import khmer
from kyc.documents.refine import NumericRefinement
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import DocumentClassification, IdentityDocument, OCRField, OCRLine
from kyc.engines.contracts import CheckEvidence

HEADER_CUES = ("ព្រះរាជាណាចក្រកម្ពុជា", "ជាតិ សាសនា ព្រះមហាក្សត្រ", "អត្តសញ្ញាណប័ណ្ណ", "សញ្ជាតិខ្មែរ")
LATIN_HEADER_CUES = ("KINGDOM OF CAMBODIA", "IDENTITY CARD")
PASSPORT_CUES = ("PASSPORT", "លិខិតឆ្លងដែន", "P<KHM")
LABELS = {
    "full_name_local": ("គោត្តនាម និងនាម", "ឈ្មោះ"),
    "date_of_birth": ("ថ្ងៃខែឆ្នាំកំណើត",),
    "sex": ("ភេទ",),
    "height": ("កម្ពស់",),
    "place_of_birth": ("ទីកន្លែងកំណើត",),
    "address": ("អាសយដ្ឋាន",),
    "validity": ("សុពលភាព",),
    "features": ("ភិនភាគ",),
}
MULTILINE_FIELDS = {"place_of_birth": 1, "address": 2}
MRZ_LINE = re.compile(r"^[A-Z0-9<]{25,44}$")
DOCUMENT_NUMBER = re.compile(r"(?<!\d)(\d{9})(?!\d)")
LATIN_NAME_LINE = re.compile(r"^[A-Z][A-Z' \-]{2,}$")
CRITICAL_FIELDS = ("document_number", "full_name_local", "date_of_birth")
IMPORTANT_FIELDS = ("full_name", "sex", "expiry_date")
FLAG_PENALTY = {"MIXED_DIGIT_SCRIPTS": 0.6, "FUZZY_LABEL": 0.9, "AMBIGUOUS_CANDIDATES": 0.7,
                "NON_NAME_CHARACTERS_REMOVED": 0.85, "EXPECTED_KHMER_SCRIPT": 0.6, "VALUE_ON_NEXT_LINE": 0.95,
                "OCR_PASSES_DISAGREED": 0.9, "NUMERIC_REREAD_REJECTED": 0.8}
# The card prints Khmer numerals; numeric words get a second read restricted to them.
NUMERIC_REFINEMENT = NumericRefinement(languages=("script/Khmer",), alphabet=khmer.KHMER_DIGITS + "./")


@dataclass(frozen=True)
class AdapterPolicy:
    version: str = "KH-NID-ADAPTER-2026.10.1"
    calibrated: bool = False
    label_similarity: float = 0.78
    min_field_confidence: float = 0.80
    accept_classification: float = 0.60
    recapture_below: float = 0.35
    max_age_years: int = 120
    typical_validity_years: tuple[int, int] = (9, 11)


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


class CambodiaNationalIDAdapter:
    document_type = DocumentType.KH_NATIONAL_ID

    def __init__(self, policy: AdapterPolicy | None = None):
        self.policy = policy or AdapterPolicy()
        self.version = self.policy.version
        self.numeric_refinement = NUMERIC_REFINEMENT

    def supports(self, classification: DocumentClassification) -> bool:
        return classification.document_type == self.document_type

    def required_sides(self) -> tuple[str, ...]:
        return ("FRONT", "BACK")

    # Classification -----------------------------------------------------------------
    def _label_hits(self, lines: list[OCRLine]) -> set[str]:
        hits = set()
        for line in lines:
            for name, variants in LABELS.items():
                if any(find_label(line.text, variant, self.policy.label_similarity) for variant in variants):
                    hits.add(name)
        return hits

    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification:
        text = " ".join(line.text for line in lines)
        upper = text.upper()
        if any(cue in upper or cue in text for cue in PASSPORT_CUES):
            return DocumentClassification(country="KH", document_family="PASSPORT", document_type=DocumentType.PASSPORT,
                                          document_side="UNKNOWN", confidence=0.7)
        headers = sum(1 for cue in HEADER_CUES if find_label(text, cue, self.policy.label_similarity))
        headers += sum(1 for cue in LATIN_HEADER_CUES if cue in upper)
        labels = self._label_hits(lines)
        digits = khmer.digits_to_ascii(text).value
        front = min(0.3, 0.15 * headers) + min(0.5, 0.1 * len(labels)) + (0.1 if DOCUMENT_NUMBER.search(digits) else 0)
        front += 0.1 if any(LATIN_NAME_LINE.match(line.text) for line in lines) else 0
        mrz = [line.text.replace(" ", "") for line in lines if MRZ_LINE.match(line.text.replace(" ", ""))]
        back = (0.7 if any(item.startswith("IDKHM") for item in mrz) else 0.35 if mrz else 0.0)
        back += min(0.2, 0.1 * headers) if not labels else 0
        if front >= back and front > 0:
            side, confidence = "FRONT", min(1.0, front)
        elif back > 0:
            side, confidence = "BACK", min(1.0, back)
        else:
            side, confidence = "UNKNOWN", 0.0
        document_type = self.document_type if confidence > 0 else DocumentType.UNKNOWN
        return DocumentClassification(country="KH", document_family="NATIONAL_ID", document_type=document_type,
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

    def _labelled_values(self, lines: list[OCRLine]) -> dict[str, tuple[str, OCRLine, tuple[str, ...]]]:
        """Map each label to the text that follows it (same line, else next line), up to the next label."""
        found: dict[str, tuple[str, OCRLine, tuple[str, ...]]] = {}
        located = []
        for index, line in enumerate(lines):
            marks = []
            for name, variants in LABELS.items():
                for variant in variants:
                    match = find_label(line.text, variant, self.policy.label_similarity)
                    if match:
                        marks.append((match[0], match[1], name, match[2]))
                        break
            marks.sort()
            located.append(marks)
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
                    source, value, flags = lines[index + 1], lines[index + 1].text.strip(" :;"), flags + ("VALUE_ON_NEXT_LINE",)
                for extra in range(1, MULTILINE_FIELDS.get(name, 0) + 1):
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

    def extract_fields(self, lines_by_side: dict[str, list[OCRLine]]) -> IdentityDocument:
        front, back = lines_by_side.get("FRONT", []), lines_by_side.get("BACK", [])
        values = self._labelled_values(front)
        fields: list[OCRField] = []

        def labelled(name: str, normalizer, output: str | None = None) -> OCRField:
            raw, line, flags = values.get(name, (None, None, ()))
            normalized = normalizer(raw) if raw else khmer.Normalized(None, ("NOT_FOUND",))
            item = self._field(output or name, raw, normalized, line, "FRONT", flags)
            fields.append(item)
            return item

        name_local = labelled("full_name_local", khmer.khmer_text)
        birth = labelled("date_of_birth", khmer.parse_date)
        sex = labelled("sex", khmer.parse_sex)
        place = labelled("place_of_birth", khmer.khmer_text)
        address = labelled("address", khmer.khmer_text)

        validity_raw, validity_line, validity_flags = values.get("validity", (None, None, ()))
        dates = khmer.find_dates(validity_raw or "")
        for position, name in enumerate(("issue_date", "expiry_date")):
            if position < len(dates):
                fields.append(self._field(name, dates[position][0], dates[position][1], validity_line, "FRONT", validity_flags))
            else:
                fields.append(self._field(name, None, khmer.Normalized(None, ("NOT_FOUND",)), None, "FRONT"))

        # Document number: a 9-digit run, preferring the top band of the card.
        candidates = []
        for line in front:
            converted = khmer.digits_to_ascii(line.text)
            for match in DOCUMENT_NUMBER.finditer(converted.value):
                if not khmer.DATE_PATTERN.search(line.text):
                    candidates.append((line.bbox[1], match.group(1), line, converted.flags, line.text))
        candidates.sort(key=lambda item: item[0])
        if candidates:
            _, number, line, flags, raw = candidates[0]
            extra = ("AMBIGUOUS_CANDIDATES",) if len({item[1] for item in candidates}) > 1 else ()
            fields.append(self._field("document_number", raw, khmer.Normalized(number, flags), line, "FRONT", extra))
        else:
            fields.append(self._field("document_number", None, khmer.Normalized(None, ("NOT_FOUND",)), None, "FRONT"))

        # Latin name: the uppercase Latin line nearest below the Khmer name label.
        anchor = values.get("full_name_local", (None, None, ()))[1]
        latin = [line for line in front if LATIN_NAME_LINE.match(line.text)
                 and not any(cue in line.text for cue in LATIN_HEADER_CUES) and len(line.text.split()) >= 2]
        if anchor is not None:
            below = [line for line in latin if line.bbox[1] >= anchor.bbox[1]]
            latin = sorted(below or latin, key=lambda line: abs(line.bbox[1] - anchor.bbox[3]))
        latin_field = self._field("full_name", latin[0].text if latin else None,
                                  khmer.latin_name(latin[0].text) if latin else khmer.Normalized(None, ("NOT_FOUND",)),
                                  latin[0] if latin else None, "FRONT")
        fields.append(latin_field)

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
            document_type=self.document_type, issuing_country="KH", document_number=value("document_number"),
            full_name=value("full_name"), full_name_local=value("full_name_local"),
            date_of_birth=value("date_of_birth"), sex=value("sex"), nationality="KH",
            place_of_birth=value("place_of_birth"), address=value("address"),
            issue_date=value("issue_date"), expiry_date=value("expiry_date"), mrz=mrz_text, fields=fields)

    # Validation ---------------------------------------------------------------------
    def validate_fields(self, document: IdentityDocument, today: date) -> list[CheckEvidence]:
        p = self.policy
        by_name = {item.field: item for item in document.fields}
        checks: list[CheckEvidence] = []

        missing_critical = [name for name in CRITICAL_FIELDS if not getattr(document, name)]
        missing_important = [name for name in IMPORTANT_FIELDS if not getattr(document, name)]
        if missing_critical:
            checks.append(CheckEvidence(CheckResult.FAIL, ("CRITICAL_FIELD_MISSING",), check_type="REQUIRED_FIELDS",
                                        details={"missing": missing_critical + missing_important}))
        elif missing_important:
            checks.append(CheckEvidence(CheckResult.REVIEW, ("FIELD_MISSING",), check_type="REQUIRED_FIELDS",
                                        details={"missing": missing_important}))
        else:
            checks.append(CheckEvidence(CheckResult.PASS, ("REQUIRED_FIELDS_PRESENT",), check_type="REQUIRED_FIELDS"))

        if document.document_number:
            ok = bool(re.fullmatch(r"\d{9}", document.document_number))
            checks.append(CheckEvidence(CheckResult.PASS if ok else CheckResult.REVIEW,
                                        ("DOCUMENT_NUMBER_FORMAT_VALID" if ok else "DOCUMENT_NUMBER_FORMAT_UNEXPECTED",),
                                        check_type="DOCUMENT_NUMBER_FORMAT"))

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
        if issue and expiry and expiry > issue:
            years = (expiry - issue).days / 365.25
            if not (p.typical_validity_years[0] <= years <= p.typical_validity_years[1]):
                problems.append("UNUSUAL_VALIDITY_PERIOD")
        checks.append(CheckEvidence(CheckResult.REVIEW if problems else CheckResult.PASS,
                                    tuple(problems) or ("DATES_CONSISTENT",), check_type="DATE_CONSISTENCY"))

        if expiry is None:
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
        reason = "MRZ_ENGINE_PHASE_5" if text else "MRZ_NOT_FOUND"
        return CheckEvidence(CheckResult.UNAVAILABLE, (reason,), check_type="MRZ")

    def parse_barcode(self, payload: bytes) -> CheckEvidence:
        return CheckEvidence(CheckResult.UNAVAILABLE, ("BARCODE_ENGINE_PHASE_7",), check_type="BARCODE")

    def parse_qr(self, payload: bytes) -> CheckEvidence:
        return CheckEvidence(CheckResult.UNAVAILABLE, ("QR_ENGINE_PHASE_7",), check_type="QR")

    def get_security_checks(self) -> tuple[str, ...]:
        return ("REQUIRED_FIELDS", "DOCUMENT_NUMBER_FORMAT", "DATE_CONSISTENCY", "EXPIRY", "OCR_CONFIDENCE",
                "SCRIPT_CONSISTENCY", "MRZ", "BARCODE", "PORTRAIT")
