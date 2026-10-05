"""Internal face engine contracts. Embeddings must only enter encrypted storage."""

from dataclasses import dataclass, field
import math
from typing import Literal, Protocol

import numpy as np
from PIL import Image

YUNET_NAME = "OpenCV YuNet"
YUNET_VERSION = "2023mar"
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SFACE_NAME = "OpenCV SFace"
SFACE_VERSION = "2021dec"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
EMBEDDING_DIMENSION = 128
FaceSource = Literal["LIVE_SELFIE", "DOCUMENT_PORTRAIT"]


class FaceEngineUnavailable(RuntimeError):
    reason_code = "FACE_MODELS_UNAVAILABLE"


class InvalidFaceEmbedding(ValueError):
    reason_code = "INVALID_FACE_EMBEDDING"


@dataclass(frozen=True)
class FaceDetection:
    """Pixel x/y/width/height and five YuNet landmarks in the original image.

    Landmark order: right eye, left eye, nose, right mouth, left mouth.
    Coordinates describe observed locations, not eye visibility or liveness.
    """

    bbox: tuple[float, float, float, float]
    landmarks: tuple[tuple[float, float], ...]
    confidence: float

    def __post_init__(self):
        if (len(self.bbox) != 4 or len(self.landmarks) != 5
                or any(len(point) != 2 for point in self.landmarks)):
            raise ValueError("A face detection requires a box and five landmarks")
        values = (*self.bbox, *(value for point in self.landmarks for value in point), self.confidence)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Face detection coordinates must be finite")
        if min(self.bbox[2:]) <= 0 or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid face dimensions or confidence")


@dataclass(frozen=True)
class FaceAssessment:
    accepted: bool
    scores: dict[str, float]
    reason_codes: tuple[str, ...]
    instructions: tuple[str, ...]
    detection: FaceDetection | None
    face_count: int
    policy_version: str
    detector_name: str = YUNET_NAME
    detector_version: str = YUNET_VERSION
    detector_sha256: str = YUNET_SHA256
    unverified_checks: tuple[str, ...] = ("EYES_VISIBLE", "SEVERE_OCCLUSION")


@dataclass(frozen=True)
class FaceEmbedding:
    vector: np.ndarray = field(repr=False, compare=False)
    model_name: str = SFACE_NAME
    model_version: str = SFACE_VERSION
    model_sha256: str = SFACE_SHA256

    def __post_init__(self):
        try:
            vector = np.array(self.vector, dtype=np.float32, copy=True)
        except (TypeError, ValueError, OverflowError) as error:
            raise InvalidFaceEmbedding("Invalid embedding array") from error
        if vector.shape != (EMBEDDING_DIMENSION,) or not np.isfinite(vector).all():
            raise InvalidFaceEmbedding("Embedding must contain 128 finite values")
        norm = float(np.linalg.norm(vector.astype(np.float64)))
        if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise InvalidFaceEmbedding("Embedding must be L2 normalized")
        if (not isinstance(self.model_name, str) or not self.model_name
                or not isinstance(self.model_version, str) or not self.model_version
                or not isinstance(self.model_sha256, str) or len(self.model_sha256) != 64):
            raise InvalidFaceEmbedding("Embedding model provenance is required")
        try:
            int(self.model_sha256, 16)
        except ValueError as error:
            raise InvalidFaceEmbedding("Invalid model SHA-256") from error
        vector.flags.writeable = False
        object.__setattr__(self, "vector", vector)

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION


@dataclass(frozen=True)
class FaceMatchPolicy:
    """Defaults are evaluation baselines, never a calibrated KYC decision.

    0.363 is OpenCV's LFW cosine benchmark. 0.20 is a development review
    boundary. Deployments must validate both boundaries and assign a new version
    before enabling calibrated decisions for their capture population.
    """

    version: str = "sface-cosine-development-v1"
    pass_threshold: float = 0.363
    fail_threshold: float = 0.20
    calibrated: bool = False
    calibration_reference: str | None = None
    model_name: str = SFACE_NAME
    model_version: str = SFACE_VERSION
    model_sha256: str = SFACE_SHA256

    def __post_init__(self):
        if (not self.version or not math.isfinite(self.pass_threshold)
                or not math.isfinite(self.fail_threshold)
                or not -1 <= self.fail_threshold < self.pass_threshold <= 1
                or not isinstance(self.calibrated, bool)):
            raise ValueError("Face thresholds require -1 <= fail < pass <= 1 and a version")


@dataclass(frozen=True)
class FaceComparison:
    score: float
    decision: str
    reason_codes: tuple[str, ...]
    threshold_policy_version: str
    model_name: str
    model_version: str
    model_sha256: str
    metric: str = "COSINE_SIMILARITY"
    calibrated: bool = False


class FaceEngine(Protocol):
    detector_name: str
    detector_version: str
    detector_sha256: str
    model_name: str
    model_version: str
    model_sha256: str

    def unavailable_reason(self) -> str | None: ...
    def detect(self, image: Image.Image) -> list[FaceDetection]: ...
    def assess(self, image: Image.Image, source: FaceSource = "LIVE_SELFIE") -> FaceAssessment: ...
    def embed(self, image: Image.Image, detection: FaceDetection) -> FaceEmbedding: ...
