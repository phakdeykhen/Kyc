"""CPU YuNet detection and SFace alignment/embedding with pinned local models."""

import hashlib
import importlib
from pathlib import Path
import threading

import numpy as np
from PIL import Image

from kyc.core.metrics import REGISTRY

from .quality import FaceQualityPolicy, assess_face_quality
from .types import (
    FaceAssessment, FaceDetection, FaceEmbedding, FaceEngineUnavailable, FaceSource,
    InvalidFaceEmbedding, SFACE_NAME, SFACE_SHA256, SFACE_VERSION,
    YUNET_NAME, YUNET_SHA256, YUNET_VERSION,
)


def verify_model(path: Path, expected_sha256: str) -> None:
    """Read every byte once before native load; reject LFS pointers and corruption."""
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise FaceEngineUnavailable("Pinned face model is not available locally") from error
    if digest != expected_sha256:
        raise FaceEngineUnavailable("Face model does not match its pinned SHA-256")


class OpenCVFaceEngine:
    detector_name = YUNET_NAME
    detector_version = YUNET_VERSION
    detector_sha256 = YUNET_SHA256
    model_name = SFACE_NAME
    model_version = SFACE_VERSION
    model_sha256 = SFACE_SHA256

    def __init__(self, yunet_path: str | Path, sface_path: str | Path, *,
                 quality_policy: FaceQualityPolicy | None = None,
                 detection_max_dimension: int = 1280, detection_threshold: float = 0.85):
        if not 320 <= detection_max_dimension <= 2560 or not 0 < detection_threshold < 1:
            raise ValueError("Invalid face detection resource limit or threshold")
        self.yunet_path = Path(yunet_path)
        self.sface_path = Path(sface_path)
        self.quality_policy = quality_policy or FaceQualityPolicy()
        self.detection_max_dimension = detection_max_dimension
        self.detection_threshold = detection_threshold
        self._lock = threading.RLock()
        self._cv = None
        self._detector = None
        self._recognizer = None

    def _ensure_loaded(self):
        # OpenCV native DNN objects mutate internal buffers. This lock covers both
        # first initialization and each complete set-size/inference operation.
        with self._lock:
            if self._detector is not None and self._recognizer is not None:
                return
            verify_model(self.yunet_path, self.detector_sha256)
            verify_model(self.sface_path, self.model_sha256)
            try:
                cv = importlib.import_module("cv2")
                version = tuple(int(part) for part in cv.__version__.split(".")[:2])
                if version < (4, 10) or version >= (5, 0):
                    raise FaceEngineUnavailable("Face engine requires OpenCV >=4.10,<5")
                detector = cv.FaceDetectorYN.create(
                    str(self.yunet_path), "", (320, 320), self.detection_threshold,
                    0.3, 5000, cv.dnn.DNN_BACKEND_OPENCV, cv.dnn.DNN_TARGET_CPU,
                )
                recognizer = cv.FaceRecognizerSF.create(
                    str(self.sface_path), "", cv.dnn.DNN_BACKEND_OPENCV, cv.dnn.DNN_TARGET_CPU,
                )
            except FaceEngineUnavailable:
                raise
            except (ImportError, AttributeError, ValueError, RuntimeError) as error:
                raise FaceEngineUnavailable("CPU face engine cannot load its pinned models") from error
            except Exception as error:
                # cv2.error is not available until import succeeds, and differs
                # across wheels. Fail closed rather than inventing a result.
                raise FaceEngineUnavailable("CPU face model initialization failed") from error
            self._cv, self._detector, self._recognizer = cv, detector, recognizer

    def unavailable_reason(self) -> str | None:
        try:
            self._ensure_loaded()
        except FaceEngineUnavailable as error:
            return error.reason_code
        return None

    @staticmethod
    def _bgr(image: Image.Image) -> np.ndarray:
        if not isinstance(image, Image.Image) or min(image.size) < 16:
            raise ValueError("A decoded face image of at least 16 pixels is required")
        return np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8)[:, :, ::-1])

    def detect(self, image: Image.Image) -> list[FaceDetection]:
        with REGISTRY.timer("kyc_face_seconds", operation="detect"):
            return self._detect(image)

    def _detect(self, image: Image.Image) -> list[FaceDetection]:
        self._ensure_loaded()
        original = self._bgr(image)
        original_height, original_width = original.shape[:2]
        scale = min(1.0, self.detection_max_dimension / max(original_height, original_width))
        detection_width = max(16, round(original_width * scale))
        detection_height = max(16, round(original_height * scale))
        with self._lock:
            try:
                pixels = (self._cv.resize(original, (detection_width, detection_height),
                                          interpolation=self._cv.INTER_AREA)
                          if scale < 1 else original)
                self._detector.setInputSize((detection_width, detection_height))
                _, rows = self._detector.detect(pixels)
                if rows is not None:
                    rows = np.array(rows, dtype=np.float32, copy=True)
            except Exception as error:
                raise FaceEngineUnavailable("Face detection inference failed") from error
        if rows is None:
            return []
        rows = np.asarray(rows, dtype=np.float32)
        if rows.ndim != 2 or rows.shape[1] != 15 or not np.isfinite(rows).all():
            raise FaceEngineUnavailable("Face detector produced invalid output")
        scale_x, scale_y = original_width / detection_width, original_height / detection_height
        detections = []
        for row in rows:
            try:
                detection = FaceDetection(
                    bbox=(float(row[0]) * scale_x, float(row[1]) * scale_y,
                          float(row[2]) * scale_x, float(row[3]) * scale_y),
                    landmarks=tuple((float(row[i]) * scale_x, float(row[i + 1]) * scale_y)
                                    for i in range(4, 14, 2)),
                    confidence=float(row[14]),
                )
            except ValueError as error:
                raise FaceEngineUnavailable("Face detector produced invalid geometry") from error
            detections.append(detection)
        return detections

    def assess(self, image: Image.Image, source: FaceSource = "LIVE_SELFIE") -> FaceAssessment:
        return assess_face_quality(image, self.detect(image), source, self.quality_policy)

    def embed(self, image: Image.Image, detection: FaceDetection) -> FaceEmbedding:
        with REGISTRY.timer("kyc_face_seconds", operation="embed"):
            return self._embed(image, detection)

    def _embed(self, image: Image.Image, detection: FaceDetection) -> FaceEmbedding:
        self._ensure_loaded()
        pixels = self._bgr(image)
        if not isinstance(detection, FaceDetection):
            raise ValueError("Embedding requires validated face detection geometry")
        row = np.array((*detection.bbox,
                        *(value for point in detection.landmarks for value in point),
                        detection.confidence), dtype=np.float32)
        with self._lock:
            try:
                aligned = self._recognizer.alignCrop(pixels, row)
                # Copy under lock: some native bindings reuse feature buffers.
                feature = np.array(self._recognizer.feature(aligned), dtype=np.float32, copy=True).reshape(-1)
            except Exception as error:
                raise FaceEngineUnavailable("Face alignment or embedding inference failed") from error
        if feature.shape != (128,) or not np.isfinite(feature).all():
            raise InvalidFaceEmbedding("SFace produced an invalid feature vector")
        norm = float(np.linalg.norm(feature.astype(np.float64)))
        if not np.isfinite(norm) or norm <= 1e-12:
            raise InvalidFaceEmbedding("SFace produced an empty feature vector")
        return FaceEmbedding(feature / norm, self.model_name, self.model_version, self.model_sha256)
