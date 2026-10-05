"""Cross-check engine (spec §18): compare independent sources field by field.

Sources are the printed visual zone (OCR), the MRZ, barcodes and the passport chip.
The engine receives comparison *states* only (MATCH / MISMATCH), never values, and
it never resolves a disagreement by picking a value. When one source disagrees with
two or more sources that agree with each other, it is reported as the outlier; that
is evidence for a reviewer, not a correction.
"""

from dataclasses import dataclass, field

from kyc.fraud.signals import Category, Severity, Signal

# Signal names list the more trusted source first: MRZ_VISUAL_DOB_MISMATCH, NFC_MRZ_DATA_MISMATCH.
SOURCES = ("NFC", "MRZ", "BARCODE", "VISUAL")
FIELDS = ("document_number", "date_of_birth", "expiry_date", "sex", "nationality", "full_name", "mrz_data_lines")
SHORT = {"document_number": "DOCUMENT_NUMBER", "date_of_birth": "DOB", "expiry_date": "EXPIRY", "sex": "SEX",
         "nationality": "NATIONALITY", "full_name": "NAME", "mrz_data_lines": "DATA"}
IDENTIFYING = {"document_number", "date_of_birth", "expiry_date", "mrz_data_lines"}
DETECTOR = "cross-check"


@dataclass(frozen=True)
class Comparison:
    """One pairwise comparison. `protected` names sources whose value for this field is
    cryptographically signed (verified chip, valid barcode signature) or check-digit valid."""

    a: str
    b: str
    field: str
    state: str
    protected: frozenset[str] = frozenset()

    @property
    def ordered(self) -> tuple[str, str]:
        return tuple(sorted((self.a, self.b), key=SOURCES.index))


@dataclass
class FieldResult:
    pairs: dict[str, str] = field(default_factory=dict)
    status: str = "NOT_COMPARED"      # CONSISTENT | CONFLICT | NOT_COMPARED
    outlier: str | None = None
    protected_sources: list[str] = field(default_factory=list)


def build(comparisons: list[Comparison]) -> dict[str, FieldResult]:
    matrix: dict[str, FieldResult] = {}
    for item in comparisons:
        if item.state not in ("MATCH", "MISMATCH") or item.field not in FIELDS:
            continue
        result = matrix.setdefault(item.field, FieldResult())
        key = "~".join(item.ordered)
        # Several barcodes may report the same pair; any disagreement wins over agreement.
        if result.pairs.get(key) != "MISMATCH":
            result.pairs[key] = item.state
        result.protected_sources = sorted(set(result.protected_sources) | item.protected, key=SOURCES.index)
    for result in matrix.values():
        states = set(result.pairs.values())
        result.status = "CONFLICT" if "MISMATCH" in states else "CONSISTENT" if states else "NOT_COMPARED"
        if result.status == "CONFLICT":
            result.outlier = _outlier(result.pairs)
    return dict(sorted(matrix.items(), key=lambda entry: FIELDS.index(entry[0])))


def _outlier(pairs: dict[str, str]) -> str | None:
    """A source is the outlier if it disagrees with every source it was compared with, and
    the remaining sources agree with each other (at least one MATCH and no MISMATCH among them)."""
    sources = {source for key in pairs for source in key.split("~")}
    for candidate in sorted(sources, key=SOURCES.index):
        own = [state for key, state in pairs.items() if candidate in key.split("~")]
        rest = [state for key, state in pairs.items() if candidate not in key.split("~")]
        if own and all(state == "MISMATCH" for state in own) and rest and all(state == "MATCH" for state in rest):
            return candidate
    return None


def summary(matrix: dict[str, FieldResult]) -> str:
    if not matrix:
        return "NOT_APPLICABLE"
    return "REVIEW" if any(item.status == "CONFLICT" for item in matrix.values()) else "PASS"


def signals(matrix: dict[str, FieldResult]) -> list[Signal]:
    found: list[Signal] = []
    for name, result in matrix.items():
        for key, state in result.pairs.items():
            if state != "MISMATCH":
                continue
            first, second = key.split("~")
            protected = sorted({first, second} & set(result.protected_sources), key=SOURCES.index)
            # Disagreeing with signed or check-digit-protected data on an identifying field is strong evidence:
            # either the other source was misread or it was altered. Names, sex and nationality are weaker.
            severity = Severity.HIGH if protected and name in IDENTIFYING else Severity.MEDIUM
            if name == "mrz_data_lines":
                code = f"{first}_{second}_DATA_MISMATCH"
            else:
                code = f"{first}_{second}_{SHORT[name]}_MISMATCH"
            found.append(Signal(code, severity, Category.CONSISTENCY, DETECTOR, (name,), (first, second),
                                {"protected_sources": protected}))
        if result.outlier:
            others = sorted({source for key in result.pairs for source in key.split("~")} - {result.outlier},
                            key=SOURCES.index)
            backed = bool(set(others) & set(result.protected_sources))
            found.append(Signal(f"{result.outlier}_OUTLIER_{SHORT[name]}", Severity.HIGH if backed else Severity.MEDIUM,
                                Category.CONSISTENCY, DETECTOR, (name,), (result.outlier,),
                                {"agreeing_sources": others, "agreement_protected": backed}))
    return found
