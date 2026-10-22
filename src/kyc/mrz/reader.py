"""MRZ capture from a corrected document side: dedicated OCR passes over the MRZ band.

General Khmer/Latin OCR mangles the '<' filler, and any single OCR configuration
fails on some images. The band is therefore read several ways (ICAO alphabet only,
unconstrained, enlarged, and column segmentation); every line is tagged MRZ_PASS and the parser's
assembler keeps the combination whose check digits validate.

The composite check digit is the last glyph of the data line and the one line-level OCR most
often loses or misreads (8 read as 6, or swallowed into misread filler). When every field check
digit validates but the composite does not, or validates only over letters misread from filler,
that glyph alone is re-read as a single character; a unanimous re-read is added as one more
candidate line with a filler optional zone. The parser still decides: a composite
that is really misprinted (or altered) still fails.
"""

from collections import Counter

import cv2
import numpy as np
from PIL import Image

from kyc.domain.identity import OCRLine
from kyc.mrz.parser import ALPHABET, FIELD_CHECKS, assemble, clean_line, parse

# (name, alphabet, scale, page segmentation mode)
VARIANTS = (("constrained", ALPHABET, 1, 6), ("unconstrained", None, 1, 6), ("enlarged", ALPHABET, 2, 6),
            ("columns", ALPHABET, 1, 4))


def read_mrz_lines(ocr, image: Image.Image, region: tuple[float, float, float, float]) -> list[OCRLine]:
    width, height = image.size
    box = (round(region[0] * width), round(region[1] * height), round(region[2] * width), round(region[3] * height))
    band = image.crop(box)
    tagged: list[OCRLine] = []
    seen: set[str] = set()
    for name, alphabet, scale, mode in VARIANTS:
        source = band if scale == 1 else band.resize((band.width * scale, band.height * scale), Image.Resampling.LANCZOS)
        for line in ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("eng",), alphabet, mode=mode):
            text = clean_line(line.text)
            if len(text) < 20 or text in seen:
                continue
            seen.add(text)
            # Map the band-relative box back onto the full side image.
            x0, y0, x1, y1 = line.bbox
            bbox = (round(region[0] + x0 * (region[2] - region[0]), 4), round(region[1] + y0 * (region[3] - region[1]), 4),
                    round(region[0] + x1 * (region[2] - region[0]), 4), round(region[1] + y1 * (region[3] - region[1]), 4))
            # Keep the original OCR text for encrypted raw-field provenance. The parser
            # normalizes characters; cleaning here would erase numeric substitutions.
            tagged.append(line.model_copy(update={"bbox": bbox, "words": (),
                                                  "notes": tuple(sorted(set(line.notes) | {"MRZ_PASS", f"MRZ_{name.upper()}"}))}))
    reread = _composite_reread(ocr, image, tagged)
    return tagged + ([reread] if reread else [])


def _composite_reread(ocr, image: Image.Image, lines: list[OCRLine]) -> OCRLine | None:
    """TD1 only: re-read the composite digit when it fails, or when the optional-data zone before it was
    read as letters (misread '<' filler, which can make a wrong composite pass by chance)."""
    block = assemble([line.text for line in lines])
    if block is None or block[0] != "TD1":
        return None
    data_line = block[1][1]
    result = parse(block[0], block[1])
    composite = result.check_digits.get("composite")
    noisy_zone = set(data_line[18:29]) != {"<"}
    if composite is None or (composite["valid"] and not noisy_zone):
        return None
    if not all(result.check_digits.get(name, {}).get("valid") for name in FIELD_CHECKS):
        return None
    rows = [line for line in lines if clean_line(line.text).lstrip("|_ ").startswith(data_line[:15])]
    if not rows:
        return None
    digit = _read_last_glyph(ocr, image, min(line.bbox[1] for line in rows), max(line.bbox[3] for line in rows))
    if digit is None:
        return None
    # The parser accepts this line only if its composite then validates over a filler optional zone.
    text = data_line[:18] + "<" * 11 + digit
    if text == data_line:
        return None
    return rows[0].model_copy(update={"text": text, "confidence": 0.0,
                                      "notes": tuple(sorted(set(rows[0].notes) | {"MRZ_COMPOSITE_REREAD"}))})


def _read_last_glyph(ocr, image: Image.Image, top: float, bottom: float) -> str | None:
    """Locate the rightmost character-sized glyph in the row and read it as one digit, several ways."""
    width, height = image.size
    row_top, row_bottom = round(top * height), round(bottom * height)
    strip = np.array(image.convert("L"))[row_top:row_bottom]
    if strip.size == 0:
        return None
    _, ink = cv2.threshold(strip, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    row = strip.shape[0]
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    glyphs = [tuple(int(v) for v in stats[i][:4]) for i in range(1, count)
              if 0.25 * row <= stats[i][3] < 0.95 * row and 0.2 * stats[i][3] <= stats[i][2] <= 1.1 * stats[i][3]]
    if len(glyphs) < 10:  # not a readable MRZ row
        return None
    typical = float(np.median([glyph[3] for glyph in glyphs]))
    glyphs = [glyph for glyph in glyphs if abs(glyph[3] - typical) <= 0.3 * typical]
    x, y, w, h = max(glyphs, key=lambda glyph: glyph[0] + glyph[2])
    margin = max(3, h // 6)
    cell = image.convert("L").crop((max(0, x - margin), max(0, row_top + y - margin),
                                    min(width, x + w + margin), min(height, row_top + y + h + margin)))
    reads = []
    for scale in (2, 3, 4):
        enlarged = cell.resize((cell.width * scale, cell.height * scale), Image.Resampling.LANCZOS)
        for mode in (10, 8):
            text, _ = ocr.reread(enlarged, (0.0, 0.0, 1.0, 1.0), ("eng",), "0123456789", mode=mode)
            if len(text) == 1:
                reads.append(text)
    votes = Counter(reads)
    # Unanimous and read at least three ways; anything less leaves the original reading alone.
    return reads[0] if len(votes) == 1 and len(reads) >= 3 else None
