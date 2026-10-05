"""MRZ capture from a corrected document side: dedicated OCR passes over the MRZ band.

General Khmer/Latin OCR mangles the '<' filler, and any single OCR configuration
fails on some images. The band is therefore read several ways (ICAO alphabet only,
unconstrained, enlarged, and column segmentation); every line is tagged MRZ_PASS and the parser's
assembler keeps the combination whose check digits validate.
"""

from PIL import Image

from kyc.domain.identity import OCRLine
from kyc.mrz.parser import ALPHABET, clean_line

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
    return tagged
