"""Printed text rows of a rectified Khmer identity card, assigned to fields by layout order.

Printed Khmer labels are often misread (security patterns, worn print), so a field is
found from the card's fixed row order instead: the validity row (two dates), the birth
row (one date, sex/height cues) and the Latin-name row are reliable anchors; the Khmer
name sits above the Latin name, place of birth follows the birth row and the address
fills the rows up to validity. When the anchors are not found nothing is assigned, so
the caller falls back to label matching. No value is inferred from position alone.
"""

from dataclasses import dataclass
import re
import statistics

import cv2
import numpy as np
from PIL import Image, ImageOps

from kyc.domain.identity import OCRLine, OCRWord

Box = tuple[float, float, float, float]
LOOSE_DATE = re.compile(r"[0-9០-៩]{1,2}\D{0,2}[0-9០-៩]{2}\D{0,2}[0-9០-៩]{4}")
UNTIL_CUE = re.compile(r"ដល់|ថ្ងៃ\s")
BIRTH_ROW_CUE = re.compile(r"ប្រុស|ស្រី|ស\.ម|កំពស់|ភេទ")
LATIN_ROW = re.compile(r"[A-Z]{2,}[ .]+[A-Z]{3,}")
COLON = re.compile(r"[:ៈ៖]")
MAX_ADDRESS_ROWS = 3


@dataclass
class Row:
    top: float
    bottom: float
    left: float
    right: float
    ink_height: float
    words: list[OCRWord]
    core: float = 0.0                                   # y of the row's densest ink band
    glyphs: list[tuple[int, int, int, int]] | None = None  # ink components (x0, y0, x1, y1) in pixels
    foreign: set[tuple[int, int, int, int]] | None = None  # components that clearly belong to another row

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    def dates(self) -> int:
        return len(LOOSE_DATE.findall(self.text))


def ink_mask(image: Image.Image) -> np.ndarray:
    """Printed ink: darker than the local background in every channel (coloured guilloche is not)."""
    brightest = np.asarray(image.convert("RGB")).max(axis=2).astype(np.uint8)
    background = cv2.medianBlur(brightest, 51)
    return ((background.astype(np.int16) - brightest.astype(np.int16)) > 35) & (brightest < 140)


def _peaks(profile: np.ndarray, distance: int, height: float) -> list[int]:
    candidates = [i for i in range(1, len(profile) - 1)
                  if profile[i] >= profile[i - 1] and profile[i] > profile[i + 1] and profile[i] >= height]
    kept: list[int] = []
    for index in sorted(candidates, key=lambda i: -profile[i]):
        if all(abs(index - other) >= distance for other in kept):
            kept.append(index)
    return sorted(kept)


def segment_rows(image: Image.Image, zone: Box) -> list[Row]:
    """Text rows of the zone from the horizontal ink profile; split strokes and subscripts are merged back."""
    width, height = image.size
    x0, y0 = round(zone[0] * width), round(zone[1] * height)
    mask = ink_mask(image.crop((x0, y0, round(zone[2] * width), round(zone[3] * height))))
    # Thin guilloche strokes vanish under an opening; printed glyph strokes survive it. The opened mask only
    # locates rows and glyphs, OCR still reads the original pixels.
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    profile = np.convolve(mask.sum(axis=1).astype(float), np.ones(9) / 9, "same")
    if not len(profile) or profile.max() <= 0:
        return []
    floor = 0.04 * profile.max()
    peaks = _peaks(profile, distance=max(1, round(0.028 * height)), height=0.18 * profile.max())
    bounds = [0] + [a + int(np.argmin(profile[a:b])) for a, b in zip(peaks, peaks[1:])] + [len(profile)]
    spans = [[bounds[i], bounds[i + 1]] for i in range(len(peaks))]

    def ink(span):
        rows = np.where(profile[span[0]:span[1]] > floor)[0]
        return (span[0] + rows[0], span[0] + rows[-1] + 1) if len(rows) else (span[0], span[0])

    merged = True
    while merged and len(spans) > 1:
        merged = False
        for i, span in enumerate(spans):
            top, bottom = ink(span)
            if bottom - top >= 0.03 * height:
                continue
            # A sliver joins the neighbour it is closer to.
            if i == len(spans) - 1 or (i > 0 and span[0] - spans[i - 1][1] <= spans[i + 1][0] - span[1]):
                j = i - 1
            else:
                j = i + 1
            low, high = sorted((i, j))
            spans[low] = [spans[low][0], spans[high][1]]
            del spans[high]
            merged = True
            break
    rows = []
    for span in spans:
        top, bottom = ink(span)
        columns = np.where(mask[top:bottom].sum(axis=0) > 1)[0]
        left = (x0 + columns.min()) / width if len(columns) else zone[0]
        right = (x0 + columns.max() + 1) / width if len(columns) else zone[2]
        core = span[0] + int(np.argmax(profile[span[0]:span[1]]))
        rows.append(Row(top=(y0 + span[0]) / height, bottom=(y0 + span[1]) / height, left=left, right=right,
                        ink_height=(bottom - top) / height, words=[], core=(y0 + core) / height, glyphs=[]))
    _assign_glyphs(rows, mask, (x0, y0), image.size)
    return rows


def _assign_glyphs(rows: list[Row], mask: np.ndarray, origin: tuple[int, int], size: tuple[int, int]) -> None:
    """Each printed ink component belongs to the row whose core is nearest its centre. Khmer vowels and
    subscripts reach past the gap between rows, so a row is cropped from its glyphs, not from a band."""
    if not rows:
        return
    width, height = size
    pitch = statistics.median([row.bottom - row.top for row in rows]) * height
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    cores = np.array([row.core * height for row in rows])
    owned: list[tuple[tuple[int, int, int, int], list[int]]] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area < 12 or h > 1.6 * pitch or (w > 0.25 * width and area < 0.15 * w * h):
            continue  # specks, and guilloche strokes crossing several rows
        glyph = (origin[0] + x, origin[1] + y, origin[0] + x + w, origin[1] + y + h)
        # Distance from each row's core to the component's vertical extent: a subscript starts just under its
        # own row's core, a vowel sign ends just above it. Ambiguous marks are kept by both rows.
        distance = np.maximum(0, np.maximum(glyph[1] - cores, cores - glyph[3]))
        order = np.argsort(distance)
        nearest = [int(order[0])]
        if len(order) > 1 and distance[order[1]] <= max(2.0, 1.6 * distance[order[0]]):
            nearest.append(int(order[1]))
        owned.append((glyph, nearest))
    for position, row in enumerate(rows):
        row.glyphs = [glyph for glyph, nearest in owned if position in nearest]
        row.foreign = {glyph for glyph, nearest in owned if position not in nearest}
    for row in rows:
        if not row.glyphs:
            continue
        # Text is one horizontal run from the label column; isolated bits far to the right are pattern noise.
        glyphs = sorted(row.glyphs)
        kept = [glyphs[0]]
        for glyph in glyphs[1:]:
            if glyph[0] - max(item[2] for item in kept) > 0.05 * width:
                break
            kept.append(glyph)
        row.glyphs = kept
        row.right = max(glyph[2] for glyph in kept) / width


def glyph_box(row: Row, start: float, size: tuple[int, int]) -> Box | None:
    """Tight box around the row's own glyphs from x=start, or None when it holds none."""
    width, height = size
    glyphs = [glyph for glyph in row.glyphs or () if glyph[2] > start * width]
    if not glyphs:
        return None
    # The opening trims stroke edges; a few pixels of margin give them back.
    margin = 4
    return (start, max(0, min(g[1] for g in glyphs) - margin) / height,
            min(width, max(g[2] for g in glyphs) + margin) / width, min(height, max(g[3] for g in glyphs) + margin) / height)


def isolate(image: Image.Image, row: Row, box: Box, pad: int = 10) -> Image.Image:
    """Crop box with other rows' ink painted over by the local background, so their vowels and
    subscripts do not become characters of this row."""
    width, height = image.size
    left, top, right, bottom = round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height)
    crop = np.array(image.crop((left, top, right, bottom)))
    fill = np.median(crop.reshape(-1, crop.shape[-1]) if crop.ndim == 3 else crop.reshape(-1), axis=0)
    for x0, y0, x1, y1 in row.foreign or ():
        if x1 <= left or x0 >= right or y1 <= top or y0 >= bottom:
            continue
        crop[max(0, y0 - top):max(0, y1 - top), max(0, x0 - left):max(0, x1 - left)] = fill
    result = Image.fromarray(crop.astype(np.uint8))
    background = tuple(int(v) for v in np.atleast_1d(fill)) if result.mode != "L" else int(fill)
    return ImageOps.expand(result, border=pad, fill=background)


def attach_words(rows: list[Row], lines: list[OCRLine]) -> None:
    """Give each row the OCR words whose vertical centre falls inside it."""
    for line in lines:
        for word in line.words or (OCRWord(text=line.text, confidence=line.confidence, bbox=line.bbox),):
            centre = (word.bbox[1] + word.bbox[3]) / 2
            row = next((row for row in rows if row.top <= centre < row.bottom), None)
            if row is not None and word not in row.words:
                row.words.append(word)
    for row in rows:
        row.words.sort(key=lambda word: word.bbox[0])


def value_start(row: Row, default: float) -> float:
    """x where the value begins: just after the label's colon, else the layout's label width."""
    for word in row.words:
        match = COLON.search(word.text)
        if match and 0.28 <= word.bbox[2] <= 0.52:
            fraction = (match.end()) / max(1, len(word.text))
            return word.bbox[0] + (word.bbox[2] - word.bbox[0]) * fraction
    return default


def _number_row(row: Row) -> bool:
    """The unlabelled card number printed top right."""
    return row.left > 0.45 or (bool(re.search(r"[0-9០-៩]{8,}", row.text.replace(" ", ""))) and row.top < 0.06)


def assign_rows(rows: list[Row]) -> dict[str, list[Row]]:
    """{field: rows} from the anchors, or {} when the card's row structure is not recognised."""
    validity = max((i for i, row in enumerate(rows)
                    if row.dates() >= 2 or (row.dates() >= 1 and UNTIL_CUE.search(row.text))), default=None)
    if validity is None:
        return {}
    birth = next((i for i, row in enumerate(rows[:validity])
                  if row.top > 0.1 and (BIRTH_ROW_CUE.search(row.text) or row.dates() == 1)), None)
    if birth is None or birth < 2 or validity - birth < 3:
        return {}
    # The Latin name is printed on its own row between the Khmer name and the birth row.
    name = birth - 2
    if _number_row(rows[name]) and not LATIN_ROW.search(rows[birth - 1].text):
        name = birth - 1  # the Latin row merged into a neighbour
    if _number_row(rows[name]):
        return {}
    address = rows[birth + 2:validity][:MAX_ADDRESS_ROWS]
    return {"full_name_local": [rows[name]], "place_of_birth": [rows[birth + 1]], "address": address}
