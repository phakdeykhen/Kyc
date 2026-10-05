"""Document image normalization before OCR: detect → perspective → orientation → normalize.

Geometry and contrast only. Pixel content inside the document is never edited,
so identity features (portrait, printed text) are preserved.
"""

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from kyc.engines.capture_quality import HeuristicDocumentQualityEngine

TARGET_WIDTH = 1600


def _homography(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    rows = []
    for (x, y), (u, v) in zip(target, source):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    return np.linalg.solve(np.array(rows, dtype=np.float64), source.reshape(8).astype(np.float64))


def rectify(image: Image.Image, corners: np.ndarray, aspect: float, width: int = TARGET_WIDTH) -> Image.Image:
    """Warp the quadrilateral (TL, TR, BR, BL) onto an upright rectangle of the expected aspect."""
    top, right = np.linalg.norm(corners[1] - corners[0]), np.linalg.norm(corners[2] - corners[1])
    bottom, left = np.linalg.norm(corners[2] - corners[3]), np.linalg.norm(corners[3] - corners[0])
    if (left + right) > (top + bottom):
        # Document lies portrait: rotate the corner order so the long edge becomes the top.
        corners = np.roll(corners, -1, axis=0)
    height = round(width / aspect)
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float64)
    coefficients = _homography(corners.astype(np.float64), target)
    return image.transform((width, height), Image.Transform.PERSPECTIVE, tuple(coefficients), Image.Resampling.BICUBIC)


def normalize(image: Image.Image) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    return gray.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))


def prepare_side(pixels: np.ndarray, aspect: float, engine: HeuristicDocumentQualityEngine | None = None) -> tuple[Image.Image, bool]:
    """Returns the OCR-ready side image and whether a document quadrilateral was found."""
    engine = engine or HeuristicDocumentQualityEngine()
    image = Image.fromarray(pixels)
    corners = engine.locate_corners(pixels)
    if corners is None:
        return normalize(image), False
    return normalize(rectify(image, corners, aspect)), True


def rotate_half_turn(image: Image.Image) -> Image.Image:
    return image.rotate(180)
