"""Engine contracts only. Unimplemented engines must never return a fake PASS."""

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, Sequence
from uuid import UUID

from PIL import Image

from kyc.domain.enums import CheckResult, DocumentType, RiskDecision
from kyc.domain.identity import DocumentClassification, IdentityDocument, OCRField, OCRLine
from kyc.engines.capture_quality import DecodedCapture, QualityAssessment


@dataclass(frozen=True)
class CheckEvidence:
    result: CheckResult
    reason_codes: tuple[str, ...]
    evidence_reference: str | None = None
    check_type: str = "UNSPECIFIED"
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RiskAssessment:
    result: RiskDecision
    reason_codes: tuple[str, ...]
    policy_version: str


class DocumentQualityEngine(Protocol):
    """Phase 2 gate: usable-or-recapture only. Never an authenticity decision."""

    def assess(self, capture: DecodedCapture, expected_aspect: float) -> QualityAssessment: ...


class OCREngine(Protocol):
    """Reads text lines from a corrected, normalized side image. Generic; no country rules."""

    engine_version: str

    def read_lines(self, image: "Image.Image", languages: Sequence[str]) -> list[OCRLine]: ...


class DocumentAdapter(Protocol):
    """Owns every country/document-specific rule. CPU-bound work runs in worker threads."""

    document_type: DocumentType
    version: str

    def supports(self, classification: DocumentClassification) -> bool: ...
    def classify(self, lines: list[OCRLine], side_hint: str) -> DocumentClassification: ...
    def required_sides(self) -> tuple[str, ...]: ...
    def extract_fields(self, lines_by_side: dict[str, list[OCRLine]]) -> IdentityDocument: ...
    def validate_fields(self, document: IdentityDocument, today: date) -> list[CheckEvidence]: ...
    def extract_portrait(self, side_image: "Image.Image") -> "Image.Image | None": ...
    def parse_mrz(self, text: str) -> CheckEvidence: ...
    def parse_barcode(self, payload: bytes) -> CheckEvidence: ...
    def parse_qr(self, payload: bytes) -> CheckEvidence: ...
    def get_security_checks(self) -> tuple[str, ...]: ...


class BiometricEngine(Protocol):
    async def assess_quality(self, capture: bytes) -> CheckEvidence: ...
    async def create_encrypted_template(self, capture: bytes, organization_id: UUID) -> str: ...
    async def compare_one_to_one(self, reference: str, live: str, policy_version: str) -> CheckEvidence: ...


class LivenessEngine(Protocol):
    async def assess(self, capture_reference: str, challenge_reference: str | None) -> CheckEvidence: ...


class FraudEngine(Protocol):
    async def analyze(self, document: IdentityDocument, evidence: list[CheckEvidence]) -> list[CheckEvidence]: ...


class RiskEngine(Protocol):
    def evaluate(self, evidence: list[CheckEvidence], policy_version: str) -> RiskAssessment: ...
