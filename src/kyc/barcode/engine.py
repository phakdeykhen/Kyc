"""Barcode detection and decoding (QR, PDF417, Data Matrix, Aztec, linear codes) via zxing-cpp.

Decoding only yields bytes. Whether those bytes mean anything is decided by the payload
parser, and whether they are genuine only by a signature verifier with a trusted key.
"""

from dataclasses import dataclass
import re

import numpy as np

try:  # optional at import time so the API still starts where the native wheel is missing
    import zxingcpp
except ImportError:  # pragma: no cover - exercised only on hosts without the dependency
    zxingcpp = None


@dataclass(frozen=True)
class BarcodeRead:
    symbology: str
    payload: bytes
    text: str
    bbox: tuple[float, float, float, float]


def available() -> bool:
    return zxingcpp is not None


def decode(pixels: np.ndarray) -> list[BarcodeRead]:
    if zxingcpp is None:
        return []
    height, width = pixels.shape[:2]
    reads, seen = [], set()
    for result in zxingcpp.read_barcodes(np.ascontiguousarray(pixels)):
        if not result.valid:
            continue
        payload = bytes(result.bytes)
        key = (str(result.format), payload)
        if key in seen:
            continue
        seen.add(key)
        position = result.position
        xs = [point.x for point in (position.top_left, position.top_right, position.bottom_right, position.bottom_left)]
        ys = [point.y for point in (position.top_left, position.top_right, position.bottom_right, position.bottom_left)]
        bbox = (round(min(xs) / width, 4), round(min(ys) / height, 4), round(max(xs) / width, 4), round(max(ys) / height, 4))
        name = re.sub(r"[^A-Z0-9]+", "_", str(result.format).split(".")[-1].upper()).strip("_")
        reads.append(BarcodeRead(symbology=name, payload=payload,
                                 text=result.text, bbox=bbox))
    return reads
