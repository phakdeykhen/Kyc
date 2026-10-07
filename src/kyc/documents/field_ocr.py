"""Khmer field crops and multi-pass voting. No identity values are guessed or logged.

Detected labels anchor each ROI; fixed bands are used only on a located, rectified card.
Agreement is evidence of repeatability, not a calibrated probability of correctness.
"""

from collections import defaultdict
import statistics

import cv2
import numpy as np
from PIL import Image, ImageFilter

from kyc.documents import khmer
from kyc.documents.adapters.khmer_label import LATIN_NAME_LINE, TITLE_CUES, is_mrz_line
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
    if name == "full_name_local":
        # The printed Latin line anchors the Khmer row above it when its label was misread.
        # It supplies coordinates only; its text is never used as the Khmer identity value.
        headings = tuple(cue for cues in TITLE_CUES.values() for cue in cues) + ("KINGDOM OF CAMBODIA",)
        latin = next((line for line in visual if LATIN_NAME_LINE.fullmatch(line.text) and len(line.text.split()) >= 2
                      and not any(cue in line.text for cue in headings)), None)
        if latin:
            x0, y0, x1, y1 = latin.bbox
            height = y1 - y0
            return (max(0.0, x0 - (x1 - x0) * .65), max(0.0, y0 - height * 1.8),
                    min(1.0, x1 + (x1 - x0) * .5), max(0.0, y0 - height * .08)), True
    region = adapter.layout.khmer_field_regions.get(name)
    return (region, True) if region and located else None


def preprocessing_variants(crop: Image.Image) -> list[tuple[str, Image.Image]]:
    gray = crop.convert("L")
    clahe = Image.fromarray(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(np.asarray(gray)))
    return [
        ("original", crop),
        ("upscale_2x", crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)),
        ("upscale_3x", crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)),
        ("clahe", clahe),
        ("mild_sharpen", gray.filter(ImageFilter.UnsharpMask(radius=1, percent=45, threshold=3))),
        ("mild_denoise", gray.filter(ImageFilter.MedianFilter(size=3))),
    ]


def _value(adapter, name: str, reads: list[OCRLine], labelled: bool) -> str:
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


def vote_field(name: str, candidates: list[tuple[str, str, float]], region: Box) -> OCRLine:
    groups: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for candidate in candidates:
        normalized = khmer.khmer_text(candidate[1])
        if normalized.value:
            # Tesseract varies spaces inside Khmer words. The winning printed reading keeps its own spaces.
            key = "".join(normalized.value.split())
            groups[key].append(candidate)
    notes = [f"FIELD_ROI:{name}", "KHMER_FIELD_OCR_V1", "KHMER_NORMALIZER_NFC_V1"]
    raw = "\n".join(f"[{variant}] {value}" for variant, value, _ in candidates)
    if not groups:
        return OCRLine(text="", confidence=0, bbox=region, notes=tuple(notes + ["KHMER_FIELD_NOT_DETECTED"]), raw_text=raw)
    ranked = sorted(groups.values(), key=lambda group: (len(group), statistics.median(c[2] for c in group)), reverse=True)
    winner = ranked[0]
    representative = sorted(winner, key=lambda c: c[2])[len(winner) // 2]
    confidence = statistics.median(candidate[2] for candidate in winner)
    if len(winner) >= 2:
        notes.append("KHMER_MULTIPASS_AGREED")
    if len(winner) < 2 or len(ranked) > 1:
        notes.append("FIELD_UNCERTAIN")
        confidence = min(confidence, 0.79)
    if len(ranked) > 1 and len(winner) == len(ranked[1]):
        confidence = min(confidence, 0.49)
    return OCRLine(text=representative[1], confidence=round(confidence, 3), bbox=region,
                   notes=tuple(notes), raw_text=raw)


def read_khmer_fields(ocr, image: Image.Image, adapter, lines: list[OCRLine], located: bool) -> list[OCRLine]:
    if not hasattr(ocr, "read_region"):
        return []
    languages = ocr.available_languages()
    if "khm" not in languages:
        return []
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
        candidates = []
        mode = 7 if name == "full_name_local" else 6
        variants = preprocessing_variants(crop)
        for variant, source in variants:
            reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("khm",), mode=mode)
            candidates.append((f"{variant}/khm", _value(adapter, name, reads, labelled),
                               min((line.confidence for line in reads), default=0.0)))
        if "script/Khmer" in languages:
            for variant, source in (variants[0], variants[3]):
                reads = ocr.read_region(source, (0.0, 0.0, 1.0, 1.0), ("script/Khmer",), mode=mode)
                candidates.append((f"{variant}/script_Khmer", _value(adapter, name, reads, labelled),
                                   min((line.confidence for line in reads), default=0.0)))
        tagged.append(vote_field(name, candidates, region))
    return tagged
