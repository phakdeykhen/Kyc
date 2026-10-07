"""Deterministic document capture quality gate (Phase 2).

Heuristic computer vision on numpy + Pillow. It only decides whether an image is
usable enough to spend OCR/model calls on; it never asserts authenticity. Policy
thresholds are versioned, not yet calibrated on field data, and never returned
to clients. A trained detector can replace this behind DocumentQualityEngine.
"""

from dataclasses import dataclass, field
from io import BytesIO
import math
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
SCORE_NAMES = ("blur_score", "glare_score", "brightness_score", "shadow_score",
               "document_coverage", "perspective_score", "resolution_score", "overall_quality")


class CaptureRejected(ValueError):
    """The upload is not an image this gate will analyze. Nothing is stored."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DecodedCapture:
    pixels: np.ndarray  # H x W x 3 uint8, EXIF orientation applied, metadata dropped
    format: str
    media_type: str


@dataclass(frozen=True)
class DocumentQualityPolicy:
    # 2026.10.2: an image already cropped to the document (uploads, scanner apps) is accepted.
    version: str = "DOC-CAPTURE-HEURISTIC-2026.10.2"
    calibrated: bool = False
    analysis_long_side: int = 400
    detail_long_side: int = 1000
    min_contrast: float = 28.0
    min_component_fraction: float = 0.005
    min_rectangularity: float = 0.82
    secondary_document_ratio: float = 0.30
    secondary_document_fraction: float = 0.02
    min_coverage: float = 0.20
    max_coverage: float = 0.92
    aspect_tolerance: float = 0.25
    min_document_long_side_px: int = 600
    min_image_long_side_px: int = 1000
    blur_reference: float = 220.0
    min_blur: float = 0.35
    min_glare: float = 0.40
    min_brightness: float = 0.50
    min_shadow: float = 0.35
    min_perspective: float = 0.75
    min_overall: float = 0.50
    # A whole image whose shape matches the document within this tolerance, and in which the document
    # fills the frame (or cannot be told apart from it), is treated as already cropped to the document.
    precrop_aspect_tolerance: float = 0.06


@dataclass(frozen=True)
class QualityAssessment:
    accepted: bool
    scores: dict[str, float]
    reason_codes: tuple[str, ...]
    instructions: tuple[str, ...]
    geometry: dict = field(default_factory=dict)
    policy_version: str = DocumentQualityPolicy.version


def decode_capture(data: bytes, *, max_bytes: int, max_pixels: int) -> DecodedCapture:
    if not data:
        raise CaptureRejected("EMPTY_FILE", "The uploaded file is empty.")
    if len(data) > max_bytes:
        raise CaptureRejected("FILE_TOO_LARGE", "The uploaded file is too large.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                image_format = probe.format
                if image_format not in ALLOWED_FORMATS:
                    raise CaptureRejected("UNSUPPORTED_FORMAT", "Upload a JPEG, PNG or WebP image.")
                width, height = probe.size
                if width * height > max_pixels:
                    raise CaptureRejected("IMAGE_DIMENSIONS_TOO_LARGE", "The image has too many pixels.")
                if getattr(probe, "n_frames", 1) > 1:
                    raise CaptureRejected("ANIMATED_IMAGE", "Upload a single still image.")
                probe.verify()
            with Image.open(BytesIO(data)) as image:
                # Orientation is applied once here; all other metadata (GPS, device) is discarded.
                pixels = np.asarray(ImageOps.exif_transpose(image).convert("RGB"), dtype=np.uint8)
    except CaptureRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise CaptureRejected("UNREADABLE_IMAGE", "The file could not be read as an image.") from None
    return DecodedCapture(pixels=pixels, format=image_format, media_type=ALLOWED_FORMATS[image_format])


def _resize(pixels: np.ndarray, long_side: int) -> np.ndarray:
    height, width = pixels.shape[:2]
    scale = long_side / max(height, width)
    if scale >= 1:
        return pixels
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return np.asarray(Image.fromarray(pixels).resize(size, Image.Resampling.BOX))


def _luminance(pixels: np.ndarray) -> np.ndarray:
    rgb = pixels.astype(np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(values.astype(np.float32), radius + 1, mode="edge")
    integral = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    total = integral[size:, size:] - integral[:-size, size:] - integral[size:, :-size] + integral[:-size, :-size]
    return total[:values.shape[0], :values.shape[1]]


def _otsu(values: np.ndarray) -> float:
    histogram, edges = np.histogram(values, bins=128)
    centers = (edges[:-1] + edges[1:]) / 2
    weight = histogram.cumsum()
    mean = (histogram * centers).cumsum()
    total, grand = weight[-1], mean[-1]
    between = np.zeros_like(centers)
    valid = (weight > 0) & (weight < total)
    w0, m0 = weight[valid], mean[valid]
    between[valid] = (grand * w0 - m0 * total) ** 2 / (w0 * (total - w0))
    return float(centers[int(np.argmax(between))])


def _components(mask: np.ndarray) -> np.ndarray:
    """4-connected labels via row runs and union-find; -1 marks background."""
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    changes = np.diff(padded, axis=1)
    rows_start, cols_start = np.nonzero(changes == 1)
    _, cols_end = np.nonzero(changes == -1)
    parent = list(range(len(cols_start)))

    def root(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    row_offsets = np.searchsorted(rows_start, np.arange(height + 1))
    for row in range(1, height):
        previous = range(row_offsets[row - 1], row_offsets[row])
        current = range(row_offsets[row], row_offsets[row + 1])
        i, j = previous.start, current.start
        while i < previous.stop and j < current.stop:
            if cols_start[i] < cols_end[j] and cols_start[j] < cols_end[i]:
                a, b = root(i), root(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            if cols_end[i] < cols_end[j]:
                i += 1
            else:
                j += 1
    labels = np.full((height, width), -1, dtype=np.int64)
    for run, (row, begin, end) in enumerate(zip(rows_start, cols_start, cols_end)):
        labels[row, begin:end] = root(run)
    return labels


def _ramp(value: float, zero: float, one: float) -> float:
    if one == zero:
        return 1.0
    return float(min(1.0, max(0.0, (value - zero) / (one - zero))))


def _shoelace(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


class HeuristicDocumentQualityEngine:
    def __init__(self, policy: DocumentQualityPolicy | None = None):
        self.policy = policy or DocumentQualityPolicy()

    def assess(self, capture: DecodedCapture, expected_aspect: float) -> QualityAssessment:
        p = self.policy
        pixels = capture.pixels
        height, width = pixels.shape[:2]
        small = _resize(pixels, p.analysis_long_side).astype(np.float32)
        factor = max(height, width) / max(small.shape[:2])
        reasons: list[str] = []
        instructions: list[str] = []

        def flag(reason: str, instruction: str) -> None:
            if reason not in reasons:
                reasons.append(reason)
            if instruction not in instructions:
                instructions.append(instruction)

        document = self._locate(small)
        geometry: dict = {"document_detected": document is not None}
        scores = dict.fromkeys(SCORE_NAMES, 0.0)
        precropped = self._precropped(height, width, document, expected_aspect)

        if precropped:
            # The image is the document: no background, so no framing checks. Image quality still applies.
            landscape = width >= height
            scores.update(perspective_score=1.0, document_coverage=1.0,
                          resolution_score=round(_ramp(max(height, width), 400, p.detail_long_side), 3))
            geometry = {"document_detected": True, "precropped": True,
                        "orientation": "LANDSCAPE" if landscape else "PORTRAIT",
                        "rotation_hint_degrees": 0 if landscape else 90, "skew_degrees": 0.0,
                        "corners": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
            if max(height, width) < p.min_document_long_side_px:
                flag("RESOLUTION_TOO_LOW", "USE_HIGHER_RESOLUTION")
            inset_y, inset_x = round(height * 0.03), round(width * 0.03)
            region = pixels[inset_y:height - inset_y, inset_x:width - inset_x]
        elif document is None:
            flag("DOCUMENT_NOT_DETECTED", "USE_CONTRASTING_BACKGROUND")
            region = pixels
        else:
            corners, coverage, touches_edge, secondary = document
            sides = [float(np.linalg.norm(corners[(i + 1) % 4] - corners[i])) for i in range(4)]
            top, right, bottom, left = sides
            horizontal, vertical = (top + bottom) / 2, (left + right) / 2
            landscape = horizontal >= vertical
            aspect = max(horizontal, vertical) / max(1e-6, min(horizontal, vertical))
            cosines = []
            for i in range(4):
                a, b = corners[i - 1] - corners[i], corners[(i + 1) % 4] - corners[i]
                cosines.append(abs(float(np.dot(a, b))) / max(1e-6, float(np.linalg.norm(a) * np.linalg.norm(b))))
            side_ratio = (min(top, bottom) / max(top, bottom, 1e-6)) * (min(left, right) / max(left, right, 1e-6))
            scores["perspective_score"] = round(side_ratio * (1 - float(np.mean(cosines))), 3)
            scores["document_coverage"] = round(coverage, 3)
            document_long_px = max(horizontal, vertical) * factor
            scores["resolution_score"] = round(_ramp(document_long_px, 400, p.detail_long_side), 3)
            edge = corners[1] - corners[0] if landscape else corners[3] - corners[0]
            skew = math.degrees(math.atan2(float(edge[1]), float(edge[0])))
            geometry.update({
                "orientation": "LANDSCAPE" if landscape else "PORTRAIT",
                "rotation_hint_degrees": 0 if landscape else 90,
                "skew_degrees": round(skew, 1),
                "corners": [[round(float(x) / small.shape[1], 4), round(float(y) / small.shape[0], 4)] for x, y in corners],
            })

            if secondary:
                flag("MULTIPLE_DOCUMENTS", "ONE_DOCUMENT_ONLY")
            if touches_edge:
                flag("DOCUMENT_CROPPED", "MOVE_BACK" if coverage > 0.6 else "CENTER_DOCUMENT")
            if coverage < p.min_coverage:
                flag("DOCUMENT_TOO_FAR", "MOVE_CLOSER")
            elif coverage > p.max_coverage:
                flag("DOCUMENT_TOO_CLOSE", "MOVE_BACK")
            if scores["perspective_score"] < p.min_perspective:
                flag("PERSPECTIVE_DISTORTED", "ALIGN_DOCUMENT")
            elif abs(aspect / expected_aspect - 1) > p.aspect_tolerance:
                flag("DOCUMENT_SHAPE_UNEXPECTED", "ALIGN_DOCUMENT")
            if document_long_px < p.min_document_long_side_px:
                flag("RESOLUTION_TOO_LOW", "USE_HIGHER_RESOLUTION" if max(height, width) < p.min_image_long_side_px else "MOVE_CLOSER")

            xs, ys = corners[:, 0] * factor, corners[:, 1] * factor
            x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
            inset_x, inset_y = (x1 - x0) * 0.08, (y1 - y0) * 0.08
            region = pixels[int(max(0, y0 + inset_y)):int(min(height, y1 - inset_y)),
                            int(max(0, x0 + inset_x)):int(min(width, x1 - inset_x))]
            if region.size == 0:
                region = pixels

        detail = _resize(region, p.detail_long_side)
        luminance = _luminance(detail)
        laplacian = (luminance[1:-1, :-2] + luminance[1:-1, 2:] + luminance[:-2, 1:-1] + luminance[2:, 1:-1]
                     - 4 * luminance[1:-1, 1:-1])
        scores["blur_score"] = round(_ramp(float(laplacian.var()), 0, p.blur_reference), 3)
        rgb = detail.astype(np.int16)
        saturated = (luminance >= 250) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 24)
        # Glare washes ink out; printed white beside dark ink (QR modules, text backgrounds) is not glare.
        radius = max(2, round(0.02 * max(luminance.shape)))
        dark_nearby = _box_sum(luminance < 100, radius) > 0
        saturated &= ~dark_nearby
        scores["glare_score"] = round(1 - _ramp(float(saturated.mean()), 0, 0.05), 3)
        mean_luminance = float(luminance.mean())
        scores["brightness_score"] = round(min(_ramp(mean_luminance, 25, 70), 1 - _ramp(mean_luminance, 215, 252)), 3)
        scores["shadow_score"] = round(self._shadow(luminance), 3)

        if scores["brightness_score"] < p.min_brightness:
            flag("TOO_DARK", "MORE_LIGHT") if mean_luminance < 128 else flag("TOO_BRIGHT", "LESS_LIGHT")
        elif (document is not None or precropped) and scores["blur_score"] < p.min_blur:
            # Sharpness is only judged under usable exposure; dark frames read as blurry.
            flag("IMAGE_BLURRY", "HOLD_STILL")
        if scores["glare_score"] < p.min_glare:
            flag("GLARE_DETECTED", "REDUCE_GLARE")
        if scores["shadow_score"] < p.min_shadow:
            flag("SHADOW_DETECTED", "AVOID_SHADOW")

        coverage_score = 1.0 if precropped else 0.0 if document is None else min(_ramp(scores["document_coverage"], 0.05, 0.35),
                                                            1 - _ramp(scores["document_coverage"], 0.85, 1.0))
        components = [scores[name] for name in ("blur_score", "glare_score", "brightness_score", "shadow_score",
                                                 "perspective_score", "resolution_score")] + [coverage_score]
        overall = float(np.exp(np.mean(np.log(np.clip(components, 1e-3, 1.0)))))
        scores["overall_quality"] = round(overall, 3)
        if not reasons and overall < p.min_overall:
            flag("OVERALL_QUALITY_LOW", "RETAKE_PHOTO")
        return QualityAssessment(accepted=not reasons, scores=scores, reason_codes=tuple(reasons),
                                 instructions=tuple(instructions), geometry=geometry, policy_version=p.version)

    def locate_corners(self, pixels: np.ndarray, expected_aspect: float | None = None) -> np.ndarray | None:
        """Document quadrilateral (TL, TR, BR, BL) in source pixel coordinates, or None.

        With expected_aspect, an image already cropped to the document returns its own corners.
        """
        small = _resize(pixels, self.policy.analysis_long_side).astype(np.float32)
        found = self._locate(small)
        height, width = pixels.shape[:2]
        if expected_aspect is not None and self._precropped(height, width, found, expected_aspect):
            return np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
        if found is None:
            return self._edge_corners(pixels, expected_aspect) if expected_aspect is not None else None
        if expected_aspect is not None:
            corners = found[0]
            top, right, bottom, left = [float(np.linalg.norm(corners[(i + 1) % 4] - corners[i])) for i in range(4)]
            horizontal, vertical = (top + bottom) / 2, (left + right) / 2
            aspect = max(horizontal, vertical) / max(1e-6, min(horizontal, vertical))
            if abs(aspect / expected_aspect - 1) > self.policy.aspect_tolerance:
                # Printed ink/portraits on a light scanner crop can be the foreground component.
                # Warping that component as a card discards fields and destroys glyph proportions.
                return self._edge_corners(pixels, expected_aspect)
        factor = max(pixels.shape[:2]) / max(small.shape[:2])
        return found[0] * factor

    def _edge_corners(self, pixels: np.ndarray, expected_aspect: float) -> np.ndarray | None:
        """Geometry fallback for complex phone backgrounds and cropped scanner images.

        Only large convex quadrilaterals of the expected shape qualify. Text blocks,
        portraits and MRZ bands cannot become a document merely by being dark.
        """
        small = _resize(pixels, 800)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        image_area = small.shape[0] * small.shape[1]
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.policy.min_coverage * image_area:
                continue
            polygon = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            points = polygon[:, 0].astype(np.float32)
            sums, differences = points.sum(axis=1), points[:, 0] - points[:, 1]
            corners = np.array([points[sums.argmin()], points[differences.argmax()],
                                points[sums.argmax()], points[differences.argmin()]])
            if len(np.unique(corners, axis=0)) != 4:
                continue
            top, right, bottom, left = [float(np.linalg.norm(corners[(i + 1) % 4] - corners[i])) for i in range(4)]
            horizontal, vertical = (top + bottom) / 2, (left + right) / 2
            aspect = max(horizontal, vertical) / max(1e-6, min(horizontal, vertical))
            if abs(aspect / expected_aspect - 1) > self.policy.aspect_tolerance:
                continue
            if min(top, bottom) / max(top, bottom) * min(left, right) / max(left, right) < self.policy.min_perspective:
                continue
            candidates.append((area, corners))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1] * (max(pixels.shape[:2]) / max(small.shape[:2]))

    def _precropped(self, height: int, width: int, document, expected_aspect: float) -> bool:
        """The image is the document itself: the frame has the document's shape, and either the
        region found reaches all four image edges (no background anywhere) or nothing document-shaped
        stands out because the border is the document. A card that is merely too close still shows
        background on some side and gets MOVE_BACK; 4:3 and 16:9 camera frames never qualify."""
        p = self.policy
        frame_aspect = max(height, width) / max(1, min(height, width))
        if abs(frame_aspect / expected_aspect - 1) > p.precrop_aspect_tolerance:
            return False
        if document is None:
            return True
        corners, coverage, sides_touched, _ = document
        if sides_touched == 4:
            return True
        horizontal = (np.linalg.norm(corners[1] - corners[0]) + np.linalg.norm(corners[2] - corners[3])) / 2
        vertical = (np.linalg.norm(corners[3] - corners[0]) + np.linalg.norm(corners[2] - corners[1])) / 2
        aspect = max(horizontal, vertical) / max(1e-6, min(horizontal, vertical))
        framed_card = coverage >= p.min_coverage and abs(aspect / expected_aspect - 1) <= p.aspect_tolerance
        return not framed_card and not sides_touched

    def _locate(self, small: np.ndarray):
        """Largest foreground region that contrasts with the frame border, as a quadrilateral."""
        p = self.policy
        height, width = small.shape[:2]
        band = max(2, round(0.03 * min(height, width)))
        border = np.concatenate([small[:band].reshape(-1, 3), small[-band:].reshape(-1, 3),
                                 small[:, :band].reshape(-1, 3), small[:, -band:].reshape(-1, 3)])
        distance = np.linalg.norm(small - np.median(border, axis=0), axis=2)
        radius = max(1, round(0.01 * max(height, width)))
        distance = _box_sum(distance, radius) / (2 * radius + 1) ** 2
        if float(np.percentile(distance, 99)) < p.min_contrast:
            return None
        # Half the Otsu level keeps darker document zones (headers, portraits, shadows) attached.
        mask = distance > max(0.5 * _otsu(distance), p.min_contrast)
        # Close small gaps (text, holograms), then fill enclosed holes.
        close = max(1, round(0.012 * max(height, width)))
        window = (2 * close + 1) ** 2
        mask = _box_sum(mask, close) > 0
        mask = _box_sum(mask, close) >= window - 0.5
        holes = _components(~mask)
        outside = np.unique(np.concatenate([holes[0], holes[-1], holes[:, 0], holes[:, -1]]))
        mask |= (holes >= 0) & ~np.isin(holes, outside)
        labels = _components(mask)
        ids, counts = np.unique(labels[mask], return_counts=True)
        keep = counts >= p.min_component_fraction * height * width
        ids, counts = ids[keep], counts[keep]
        if not len(ids):
            return None
        order = np.argsort(counts)[::-1]
        main_id, main_area = ids[order[0]], int(counts[order[0]])
        secondary = len(order) > 1 and counts[order[1]] >= max(p.secondary_document_ratio * main_area,
                                                                  p.secondary_document_fraction * height * width)
        ys, xs = np.nonzero(labels == main_id)
        corners = np.array([
            (xs[np.argmin(xs + ys)], ys[np.argmin(xs + ys)]),
            (xs[np.argmax(xs - ys)], ys[np.argmax(xs - ys)]),
            (xs[np.argmax(xs + ys)], ys[np.argmax(xs + ys)]),
            (xs[np.argmin(xs - ys)], ys[np.argmin(xs - ys)]),
        ], dtype=np.float32)
        quad_area = _shoelace(corners + np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32))
        if quad_area <= 0 or main_area / quad_area < p.min_rectangularity:
            return None
        # How many image edges the region reaches (0–4); non-zero means the document may be cut off.
        touches = sum((xs.min() <= 1, ys.min() <= 1, xs.max() >= width - 2, ys.max() >= height - 2))
        return corners, main_area / (height * width), int(touches), bool(secondary)

    @staticmethod
    def _shadow(luminance: np.ndarray) -> float:
        rows, cols = 4, 6
        height, width = luminance.shape
        if height < rows * 4 or width < cols * 4:
            return 1.0
        blocks = [np.percentile(luminance[r * height // rows:(r + 1) * height // rows,
                                          c * width // cols:(c + 1) * width // cols], 90)
                  for r in range(rows) for c in range(cols)]
        ratio = float(np.percentile(blocks, 10)) / max(1.0, float(np.percentile(blocks, 95)))
        return _ramp(ratio, 0.45, 0.85)
