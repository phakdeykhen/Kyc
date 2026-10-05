"""Pose evidence from YuNet's five landmarks (right eye, left eye, nose, right mouth, left mouth).

The nose is expressed in the affine frame centred on the eye midpoint m:
nose = m + a·(le − re) + b·(mouth − m). Affine maps preserve these
coordinates exactly, so a flat face (printed photo, phone or monitor) that is moved,
rotated, tilted or zoomed keeps (a, b) constant under the near-affine projection of a
handheld camera. A real head is not flat: the nose stands in front of the eye/mouth plane,
so turning left/right changes `a` (yaw) and looking up/down changes `b` (pitch), and the
centred frame keeps the two nearly independent.

Deformation of the eye/mouth triangle (its aspect ratio) shows the face *looked* different
between frames. Deformation with unchanged (a, b) is the signature of a tilted flat picture.
"""

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class PoseSample:
    a: float            # nose offset along the eye axis, in eye distances (≈0 frontal)
    b: float            # nose position from eye line (0) to mouth (1) (≈0.5 frontal)
    aspect: float       # inter-eye distance / eye-to-mouth distance
    scale: float        # inter-eye distance in pixels
    centre: tuple[float, float]


def pose(landmarks) -> PoseSample:
    right_eye, left_eye, nose, right_mouth, left_mouth = (np.asarray(point, dtype=np.float64) for point in landmarks)
    mouth = (right_mouth + left_mouth) / 2
    middle = (right_eye + left_eye) / 2
    basis = np.column_stack((left_eye - right_eye, mouth - middle))
    if abs(np.linalg.det(basis)) < 1e-6:
        raise ValueError("Degenerate facial landmarks")
    a, b = np.linalg.solve(basis, nose - middle)
    eye_distance = float(np.linalg.norm(left_eye - right_eye))
    eye_mouth = float(np.linalg.norm(mouth - (right_eye + left_eye) / 2))
    if not (math.isfinite(a) and math.isfinite(b)) or eye_mouth <= 0:
        raise ValueError("Degenerate facial landmarks")
    centre = tuple(float(value) for value in (right_eye + left_eye + mouth) / 3)
    return PoseSample(float(a), float(b), eye_distance / eye_mouth, eye_distance, centre)
