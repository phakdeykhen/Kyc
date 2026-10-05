"""Face detection, usable capture assessment, and private 1:1 biometrics."""

from .embeddings import compare_embeddings, deserialize_embedding, serialize_embedding
from .opencv import OpenCVFaceEngine
from .quality import FaceQualityPolicy, assess_face_quality
from .types import (
    FaceAssessment, FaceComparison, FaceDetection, FaceEmbedding, FaceEngine,
    FaceEngineUnavailable, FaceMatchPolicy, FaceSource, InvalidFaceEmbedding,
)

__all__ = [
    "FaceAssessment", "FaceComparison", "FaceDetection", "FaceEmbedding", "FaceEngine",
    "FaceEngineUnavailable", "FaceMatchPolicy", "FaceQualityPolicy", "FaceSource",
    "InvalidFaceEmbedding", "OpenCVFaceEngine", "assess_face_quality",
    "compare_embeddings", "deserialize_embedding", "serialize_embedding",
]
