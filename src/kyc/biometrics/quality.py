"""Versioned face usability measurements; not liveness or occlusion assurance."""

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
from PIL import Image

from .types import FaceAssessment, FaceDetection, FaceSource

UNVERIFIED_REASONS = ("FACE_EYE_VISIBILITY_UNVERIFIED", "FACE_OCCLUSION_UNVERIFIED")


@dataclass(frozen=True)
class FaceQualityPolicy:
    version: str = "face-usability-heuristic-v1"
    calibrated: bool = False
    minimum_selfie_face_pixels: int = 120
    minimum_portrait_face_pixels: int = 64
    minimum_selfie_face_area: float = 0.06
    maximum_selfie_face_area: float = 0.70
    maximum_center_offset: float = 0.22
    minimum_brightness: float = 45
    maximum_brightness: float = 225
    maximum_clipped_fraction: float = 0.35
    minimum_laplacian_variance: float = 25
    maximum_roll_degrees: float = 20
    maximum_landmark_asymmetry: float = 0.55


def _unit(value: float) -> float:
    return round(float(np.clip(value, 0, 1)), 6)


def assess_face_quality(
    image: Image.Image,
    detections: Sequence[FaceDetection],
    source: FaceSource = "LIVE_SELFIE",
    policy: FaceQualityPolicy | None = None,
) -> FaceAssessment:
    """Evaluate the original pixels using measured detector geometry.

    `accepted` authorizes embedding of a usable image only. Five predicted
    landmarks cannot verify eye visibility, severe occlusion, or authenticity;
    callers must preserve the unverified checks and avoid a quality PASS claim.
    Document portrait regions are not subjected to selfie framing constraints.
    """
    if source not in ("LIVE_SELFIE", "DOCUMENT_PORTRAIT"):
        raise ValueError("Unsupported face capture source")
    policy = policy or FaceQualityPolicy()
    reasons: list[str] = []
    instructions: list[str] = []
    scores = {name: 0.0 for name in (
        "detection_confidence", "face_size", "centering", "sharpness",
        "illumination", "pose", "landmark_geometry",
    )}

    def reject(reason: str, instruction: str):
        reasons.append(reason)
        if instruction not in instructions:
            instructions.append(instruction)

    def result(detection: FaceDetection | None):
        return FaceAssessment(
            accepted=not reasons, scores=scores,
            reason_codes=tuple(reasons) + UNVERIFIED_REASONS,
            instructions=tuple(instructions), detection=detection,
            face_count=len(detections), policy_version=policy.version,
        )

    if len(detections) != 1:
        if not detections:
            reject("FACE_NOT_DETECTED", "CENTER_FACE")
        else:
            reject("MULTIPLE_FACES", "ONE_FACE_ONLY")
        return result(None)

    face = detections[0]
    x, y, width, height = face.bbox
    image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Empty face capture")
    scores["detection_confidence"] = _unit(face.confidence)
    minimum_pixels = (policy.minimum_selfie_face_pixels if source == "LIVE_SELFIE"
                      else policy.minimum_portrait_face_pixels)
    scores["face_size"] = _unit(min(width, height) / minimum_pixels)
    if min(width, height) < minimum_pixels:
        reject("FACE_TOO_SMALL", "MOVE_CLOSER")

    area_ratio = width * height / (image_width * image_height)
    center_offset = max(abs((x + width / 2) / image_width - 0.5),
                        abs((y + height / 2) / image_height - 0.5))
    scores["centering"] = _unit(1 - center_offset / 0.5)
    if source == "LIVE_SELFIE":
        if area_ratio < policy.minimum_selfie_face_area:
            reject("FACE_TOO_FAR", "MOVE_CLOSER")
        if area_ratio > policy.maximum_selfie_face_area:
            reject("FACE_TOO_CLOSE", "MOVE_BACK")
        if center_offset > policy.maximum_center_offset:
            reject("FACE_OFF_CENTER", "CENTER_FACE")

    # Even well centered boxes may cut off a chin or forehead at the image edge.
    if x < -width * 0.02 or y < -height * 0.02 or x + width > image_width + width * 0.02 or y + height > image_height + height * 0.02:
        reject("FACE_CROPPED", "MOVE_BACK")
    left, top = max(0, int(math.floor(x))), max(0, int(math.floor(y)))
    right, bottom = min(image_width, int(math.ceil(x + width))), min(image_height, int(math.ceil(y + height)))
    if right <= left or bottom <= top:
        reject("FACE_OUTSIDE_IMAGE", "CENTER_FACE")
        return result(face)
    crop = image.convert("L").crop((left, top, right, bottom))
    crop.thumbnail((160, 160), Image.Resampling.LANCZOS)
    gray = np.asarray(crop, dtype=np.float32)
    brightness = float(gray.mean())
    clipped_fraction = float(((gray < 8) | (gray > 247)).mean())
    scores["illumination"] = _unit((1 - abs(brightness - 128) / 128) * (1 - clipped_fraction))
    if brightness < policy.minimum_brightness:
        reject("FACE_TOO_DARK", "MORE_LIGHT")
    elif brightness > policy.maximum_brightness:
        reject("FACE_OVEREXPOSED", "REDUCE_LIGHT")
    elif clipped_fraction > policy.maximum_clipped_fraction:
        reject("FACE_LIGHTING_UNEVEN", "EVEN_LIGHTING")
    if min(gray.shape) >= 3:
        laplacian = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2]
                     + gray[1:-1, 2:] - 4 * gray[1:-1, 1:-1])
        variance = float(laplacian.var())
    else:
        variance = 0.0
    scores["sharpness"] = _unit(variance / (policy.minimum_laplacian_variance * 4))
    # Illumination failures do not establish motion blur.
    if policy.minimum_brightness <= brightness <= policy.maximum_brightness and variance < policy.minimum_laplacian_variance:
        reject("FACE_BLURRY", "HOLD_STILL")

    eyes = sorted(face.landmarks[:2], key=lambda point: point[0])
    eye_left, eye_right = eyes
    nose = face.landmarks[2]
    mouth_left, mouth_right = sorted(face.landmarks[3:], key=lambda point: point[0])
    eye_span = math.dist(eye_left, eye_right)
    roll = abs(math.degrees(math.atan2(eye_right[1] - eye_left[1], eye_right[0] - eye_left[0])))
    asymmetry = abs(math.dist(nose, eye_left) - math.dist(nose, eye_right)) / max(eye_span, 1e-6)
    scores["pose"] = _unit(1 - max(roll / 90, asymmetry))
    eye_mid_y = (eye_left[1] + eye_right[1]) / 2
    mouth_mid_y = (mouth_left[1] + mouth_right[1]) / 2
    plausible_landmarks = (
        eye_span >= width * 0.18
        and mouth_right[0] - mouth_left[0] >= width * 0.10
        and eye_mid_y < nose[1] < mouth_mid_y
        and all(x - width * 0.08 <= px <= x + width * 1.08
                and y - height * 0.08 <= py <= y + height * 1.08 for px, py in face.landmarks)
    )
    scores["landmark_geometry"] = float(plausible_landmarks)
    if not plausible_landmarks:
        reject("FACE_LANDMARKS_UNRELIABLE", "FACE_CAMERA")
    if roll > policy.maximum_roll_degrees or asymmetry > policy.maximum_landmark_asymmetry:
        reject("FACE_POSE_UNACCEPTABLE", "FACE_CAMERA")
    return result(face)
