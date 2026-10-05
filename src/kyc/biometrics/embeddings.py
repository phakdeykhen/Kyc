"""Encrypted-template payload format and same-model 1:1 cosine comparison."""

import json
import math

import numpy as np

from .types import FaceComparison, FaceEmbedding, FaceMatchPolicy, InvalidFaceEmbedding

TEMPLATE_FORMAT = "kyc.face-template.v1"


def serialize_embedding(embedding: FaceEmbedding) -> bytes:
    """Sensitive bytes: encrypt immediately; never return from an API or log."""
    return json.dumps({
        "format": TEMPLATE_FORMAT, "model_name": embedding.model_name,
        "model_version": embedding.model_version, "model_sha256": embedding.model_sha256,
        "dimension": embedding.dimension, "vector": embedding.vector.tolist(),
    }, separators=(",", ":"), allow_nan=False).encode("utf-8")


def deserialize_embedding(payload: bytes) -> FaceEmbedding:
    try:
        if len(payload) > 32_768:
            raise ValueError("Oversized embedding payload")
        value = json.loads(payload)
        if (not isinstance(value, dict) or value.get("format") != TEMPLATE_FORMAT
                or value.get("dimension") != 128):
            raise ValueError("Unknown embedding payload format")
        return FaceEmbedding(
            vector=value["vector"], model_name=value["model_name"],
            model_version=value["model_version"], model_sha256=value["model_sha256"],
        )
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
        raise InvalidFaceEmbedding("Invalid encrypted face template contents") from error


def compare_embeddings(reference: FaceEmbedding, live: FaceEmbedding,
                       policy: FaceMatchPolicy | None = None) -> FaceComparison:
    """Compare only two templates of the claimed identity; never search a set."""
    policy = policy or FaceMatchPolicy()
    model = (reference.model_name, reference.model_version, reference.model_sha256)
    if model != (live.model_name, live.model_version, live.model_sha256):
        raise InvalidFaceEmbedding("Reference and live templates use different models")
    if model != (policy.model_name, policy.model_version, policy.model_sha256):
        raise InvalidFaceEmbedding("Face threshold policy does not apply to the template model")
    # Revalidate vector norms at comparison time as a defense against external mutation.
    norms = []
    for vector in (reference.vector, live.vector):
        norm = float(np.linalg.norm(vector.astype(np.float64)))
        if vector.shape != (128,) or not np.isfinite(vector).all() or not math.isclose(
                norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise InvalidFaceEmbedding("Invalid face template normalization")
        norms.append(norm)
    score = float(np.clip(np.dot(reference.vector.astype(np.float64), live.vector.astype(np.float64))
                          / (norms[0] * norms[1]), -1, 1))
    if not policy.calibrated:
        decision, reasons = "REVIEW", ("UNCALIBRATED_FACE_POLICY",)
    elif score >= policy.pass_threshold:
        decision, reasons = "PASS", ("FACE_MATCH_ABOVE_THRESHOLD",)
    elif score < policy.fail_threshold:
        decision, reasons = "FAIL", ("FACE_MATCH_BELOW_THRESHOLD",)
    else:
        decision, reasons = "REVIEW", ("FACE_MATCH_AMBIGUOUS",)
    return FaceComparison(
        score=score, decision=decision, reason_codes=reasons,
        threshold_policy_version=policy.version, model_name=model[0],
        model_version=model[1], model_sha256=model[2], calibrated=policy.calibrated,
    )
