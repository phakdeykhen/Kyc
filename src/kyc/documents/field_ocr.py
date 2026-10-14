"""Khmer field crops and multi-pass voting. No identity values are guessed or logged.

On a located, rectified card each field is cropped from its own printed row (see card_rows);
otherwise detected labels anchor each ROI and fixed bands are the last resort.
Agreement is evidence of repeatability, not a calibrated probability of correctness.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import re
import statistics

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from kyc.documents import card_rows, khmer
from kyc.documents.adapters.khmer_label import is_mrz_line
from kyc.domain.identity import OCRLine

Box = tuple[float, float, float, float]


def field_region(adapter, name: str, lines: list[OCRLine], located: bool) -> tuple[Box, bool] | None:
    """Return a crop and whether it includes the printed field label."""
    visual = sorted((line for line in lines if not is_mrz_line(line)), key=lambda line: (line.bbox[1], line.bbox[0]))
    for index, line in enumerate(visual):
        mark = next((mark for mark in adapter._marks(line) if mark[2] == name), None)
        if mark is None:
            continue
        _, label_end, _, _ = mark
        x0, y0, x1, y1 = line.bbox
        labelled = True
        offset = 0
        for word in line.words:
            # Crop after the label only when an entire word belongs to its value.
            if offset >= label_end and word.text.strip(" :;ៈ៖"):
                x0, labelled = word.bbox[0], False
                break
            offset += len(word.text) + 1
        stop = 1.0
        for following in visual[index + 1:]:
            if following.bbox[1] > y1 and (adapter._marks(following) or not khmer.has_khmer(following.text)):
                stop = following.bbox[1]
                break
        for following in visual[index + 1:index + 1 + adapter.layout.multiline.get(name, 0)]:
            if following.bbox[1] >= stop or adapter._marks(following) or not khmer.has_khmer(following.text):
                break
            x1, y1 = max(x1, following.bbox[2]), max(y1, following.bbox[3])
            x0 = min(x0, following.bbox[0])
        # MRZ-like noise may be interleaved with visual OCR. Its physical band is also a boundary.
        below = [item.bbox[1] for item in lines if is_mrz_line(item) and item.bbox[1] > y0]
        stop = min([stop, *below])
        pad = min(0.012, (y1 - y0) * 0.15)
        return (max(0.0, x0 - 0.008), max(0.0, y0 - pad), min(1.0, x1 + 0.008), min(stop, y1 + pad)), labelled
    region = adapter.layout.khmer_field_regions.get(name)
    return (region, True) if region and located else None


def preprocessing_variants(crop: Image.Image) -> list[tuple[str, Image.Image]]:
    """Same-scale variants. Upscaling is avoided: the Khmer LSTM returns nothing for over-tall text."""
    gray = crop.convert("L")
    clahe = Image.fromarray(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(np.asarray(gray)))
    # Colored security lines lighten in the maximum channel while dark printed ink stays dark.
    suppressed = Image.fromarray(np.asarray(crop.convert("RGB")).max(axis=2))
    ink = Image.fromarray(np.where(card_rows.ink_mask(crop), 0, 255).astype(np.uint8))
    return [
        ("original", gray),
        ("clahe", clahe),
        ("mild_sharpen", gray.filter(ImageFilter.UnsharpMask(radius=1, percent=45, threshold=3))),
        ("mild_denoise", gray.filter(ImageFilter.MedianFilter(size=3))),
        ("color_pattern_suppression", suppressed),
        ("ink_mask", ink),
    ]


def _value(adapter, name: str, reads: list[OCRLine], labelled: bool) -> str:
    # Security-pattern noise before a detected label is not part of the value.
    first_label = next((index for index, line in enumerate(reads)
                        if any(mark[2] == name for mark in adapter._marks(line))), None)
    if first_label is not None:
        reads = reads[first_label:]
    values = []
    started = False
    for line in reads:
        if is_mrz_line(line):
            break
        marks = adapter._marks(line)
        own = next((mark for mark in marks if mark[2] == name), None)
        if own:
            following = [mark[0] for mark in marks if mark[0] >= own[1]]
            value = line.text[own[1]:min(following, default=len(line.text))]
            started = True
        elif marks:
            break
        elif not started and labelled:
            # A damaged label can still have its printed colon. Never invent a name from Latin/MRZ.
            separators = [line.text.find(char) for char in ":ៈ៖" if char in line.text]
            value = line.text[min(separators) + 1:] if separators else ""
            started = True
        else:
            value = line.text
        if khmer.has_khmer(value):
            values.append(value.strip(" :;ៈ៖"))
    return khmer.clean(" ".join(values))


def _distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _similarity(left: str, right: str) -> float:
    return 1 - _distance(left, right) / max(len(left), len(right), 1)


def vote_field(name: str, candidates: list[tuple[str, str, float]], region: Box) -> OCRLine:
    """Soft vote: each reading scores its similarity to every other reading, so near-agreeing
    noisy passes support the reading they share instead of splitting into singleton groups."""
    readings: dict[str, list[tuple[str, str, float]]] = {}
    for candidate in candidates:
        normalized = khmer.khmer_text(candidate[1])
        if normalized.value:
            # Tesseract varies spaces inside Khmer words. The winning printed reading keeps its own spaces.
            readings.setdefault("".join(normalized.value.split()), []).append(candidate)
    notes = [f"FIELD_ROI:{name}", "KHMER_FIELD_OCR_V2", "KHMER_NORMALIZER_NFC_V1"]
    raw = "\n".join(f"[{variant}] {value}" for variant, value, _ in candidates)
    if not readings:
        return OCRLine(text="", confidence=0, bbox=region, notes=tuple(notes + ["KHMER_FIELD_NOT_DETECTED"]), raw_text=raw)
    support = {key: sum(len(group) * _similarity(key, other) for other, group in readings.items()) for key in readings}
    ranked = sorted(readings, key=lambda key: (support[key], len(readings[key]),
                                               statistics.median(c[2] for c in readings[key])), reverse=True)
    winner = readings[ranked[0]]
    representative = sorted(winner, key=lambda c: c[2])[len(winner) // 2]
    confidence = statistics.median(candidate[2] for candidate in winner)
    if len(winner) >= 2:
        notes.append("KHMER_MULTIPASS_AGREED")
    runner_up = max((len(readings[key]) for key in ranked[1:]), default=0)
    if len(winner) < 2 or runner_up:
        notes.append("FIELD_UNCERTAIN")
        confidence = min(confidence, 0.79)
    if runner_up >= len(winner) or (len(ranked) > 1 and support[ranked[1]] >= support[ranked[0]] * 0.95):
        confidence = min(confidence, 0.49)
    return OCRLine(text=representative[1], confidence=round(confidence, 3), bbox=region,
                   notes=tuple(notes), raw_text=raw)


def _strip_label(text: str) -> str:
    """Drop label debris before a colon near the start, then everything that is not Khmer text."""
    colon = card_rows.COLON.search(text)
    # The longest printed label (ថ្ងៃខែឆ្នាំកំណើត) is 16 characters; a later colon belongs to the value.
    if colon and colon.start() <= min(18, len(text) * 0.6):
        text = text[colon.end():]
    text = re.sub(r"[^\u1780-\u17ff\u19e0-\u19ff0-9/ .,\-]", " ", text)
    return khmer.clean(text).strip(" .,-/")


def _passes(name: str, languages: set[str]) -> list[tuple[tuple[str, ...], int]]:
    passes = [(("khm",), 13), (("khm",), 7)]
    if "script/Khmer" in languages:
        passes.append((("script/Khmer",), 13))
    return passes


def _read_rows(ocr, image: Image.Image, adapter, lines: list[OCRLine]) -> list[OCRLine] | None:
    """Structured-row reading of a rectified card, or None when its rows cannot be identified."""
    zone = adapter.layout.viz_regions.get("FRONT")
    if not zone:
        return None
    zone = (adapter.layout.value_starts.get("zone_left", zone[0]), 0.0, zone[2], zone[3])
    rows = card_rows.segment_rows(image, zone)
    card_rows.attach_words(rows, [line for line in lines if not is_mrz_line(line)])
    assigned = card_rows.assign_rows(rows)
    if not assigned:
        return None
    languages = ocr.available_languages()
    jobs = []
    regions: dict[str, list[Box]] = {}
    for name in adapter.layout.khmer_field_regions:
        for index, row in enumerate(assigned.get(name, ())):
            start = card_rows.value_start(row, adapter.layout.value_starts.get(name, zone[0])) if index == 0 \
                else zone[0]
            box = card_rows.glyph_box(row, max(0.0, start - 0.004), image.size)
            if box is None:
                continue
            position = len(regions.setdefault(name, []))
            regions[name].append(box)
            for variant, source in preprocessing_variants(card_rows.isolate(image, row, box)):
                for model, mode in _passes(name, languages):
                    jobs.append((name, position, f"{variant}/{model[0].replace('/', '_')}/psm{mode}", source, model, mode))

    def run(job):
        name, position, label, source, model, mode = job
        reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), model, mode=mode)
        text = " ".join(line.text for line in reads)
        # Only a field's first row starts with its label.
        return name, position, label, _strip_label(text if position == 0 else " " + text), \
            min((line.confidence for line in reads), default=0.0)

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        results = list(pool.map(run, jobs))
    tagged = []
    for name, boxes in regions.items():
        parts = [vote_field(name, [(label, text, confidence) for field, index, label, text, confidence in results
                                   if field == name and index == position], box)
                 for position, box in enumerate(boxes)]
        found = [part for part in parts if part.text]
        if not found:
            tagged.append(parts[0])
            continue
        bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        notes = tuple(dict.fromkeys(note for part in found for note in part.notes)) + ("CARD_ROW_LAYOUT",)
        if len(found) > 1:
            # A wrapped value agrees only as far as its weakest row.
            notes = tuple(note for note in notes if note != "KHMER_MULTIPASS_AGREED" or
                          all("KHMER_MULTIPASS_AGREED" in part.notes for part in found))
        tagged.append(OCRLine(text=" ".join(part.text for part in found), confidence=min(part.confidence for part in found),
                              bbox=bounds, notes=notes, raw_text="\n".join(part.raw_text or "" for part in found)))
    return tagged


def read_khmer_fields(ocr, image: Image.Image, adapter, lines: list[OCRLine], located: bool) -> list[OCRLine]:
    if not hasattr(ocr, "read_region"):
        return []
    languages = ocr.available_languages()
    if "khm" not in languages:
        return []
    if located and adapter.layout.structured_rows:
        structured = _read_rows(ocr, image, adapter, lines)
        if structured is not None:
            return structured
    tagged = []
    for name in adapter.layout.khmer_field_regions:
        found = field_region(adapter, name, lines, located)
        if found is None:
            continue
        region, labelled = found
        if region[3] <= region[1] or region[2] <= region[0]:
            continue
        width, height = image.size
        crop = image.crop(tuple(round(value * (width if index % 2 == 0 else height)) for index, value in enumerate(region)))
        crop = ImageOps.expand(crop, border=8, fill="white")
        candidates = []
        mode = 7 if name == "full_name_local" else 6
        variants = preprocessing_variants(crop)
        for variant, source in variants:
            reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("khm",), mode=mode)
            candidates.append((f"{variant}/khm", _value(adapter, name, reads, labelled),
                               min((line.confidence for line in reads), default=0.0)))
        if "script/Khmer" in languages:
            for variant, source in (variants[0], variants[1]):
                reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("script/Khmer",), mode=mode)
                candidates.append((f"{variant}/script_Khmer", _value(adapter, name, reads, labelled),
                                   min((line.confidence for line in reads), default=0.0)))
        if name == "full_name_local":
            # Raw-line segmentation handles bold display fonts/security lines that
            # the normal single-line segmenter can reject as an empty row.
            for variant, source in (variants[0], variants[3], variants[4], variants[5]):
                reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("khm",), mode=13)
                candidates.append((f"raw_line_{variant}/khm", _value(adapter, name, reads, labelled),
                                   min((line.confidence for line in reads), default=0.0)))
        tagged.append(vote_field(name, candidates, region))
    return tagged
