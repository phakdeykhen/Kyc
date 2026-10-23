"""Second, constrained OCR pass over numeric words (document numbers, dates).

General-purpose Khmer+Latin models confuse Khmer digits with Latin look-alikes
(១/9, ០/0). When an adapter declares which digit alphabet its document prints,
numeric words are re-read with only that alphabet allowed. This is a second
reading of the same pixels, not a semantic correction: if the two passes
disagree, the line carries an OCR_PASSES_DISAGREED note that lowers confidence.
"""

from dataclasses import dataclass
import re

from PIL import Image

from kyc.domain.identity import OCRLine, OCRWord

NUMERIC_WORD = re.compile(r"^[0-9០-៩OoIl|]{1,12}([./\-][0-9០-៩OoIl|]{1,4}){0,2}$")
SEPARATORS = re.compile(r"[./\-]")
# Used only when two independent constrained reads agree but Tesseract reports no confidence.
AGREEMENT_CONFIDENCE = 0.75


@dataclass(frozen=True)
class NumericRefinement:
    languages: tuple[str, ...]
    alphabet: str
    min_digits: int = 3


def installed_languages(wanted: tuple[str, ...], available: set[str]) -> tuple[str, ...] | None:
    """Resolve script models across tessdata layouts: Homebrew installs `script/Khmer`, while the
    Debian/Ubuntu package used by the container image installs the same model as `Khmer`."""
    resolved = []
    for name in wanted:
        if name in available:
            resolved.append(name)
        elif name.startswith("script/") and name.removeprefix("script/") in available:
            resolved.append(name.removeprefix("script/"))
        else:
            return None
    return tuple(resolved)


def _digit_count(text: str) -> int:
    return sum(character.isdigit() for character in text)


def refine_numeric_words(ocr, image: Image.Image, lines: list[OCRLine], spec: NumericRefinement) -> list[OCRLine]:
    refined_lines = []
    for line in lines:
        words = list(line.words)
        for index, word in enumerate(words):
            if not NUMERIC_WORD.match(word.text) or _digit_count(word.text) < spec.min_digits:
                continue
            text, confidence = ocr.reread(image, word.bbox, spec.languages, spec.alphabet)
            extra: tuple[str, ...] = ()
            if text and confidence < 0.5:
                # Constrained LSTM reads sometimes report 0 confidence for a correct string.
                # A second constrained read in single-word mode must agree before it is used.
                confirm, confirm_confidence = ocr.reread(image, word.bbox, spec.languages, spec.alphabet, mode=8)
                if confirm == text:
                    confidence, extra = max(confidence, confirm_confidence, AGREEMENT_CONFIDENCE), ("CONSTRAINED_PASSES_AGREED",)
            # Accept only a re-read with the same separator structure and a usable confidence.
            if not text or SEPARATORS.findall(text) != SEPARATORS.findall(word.text) or confidence < 0.5:
                words[index] = word.model_copy(update={"notes": word.notes + ("NUMERIC_REREAD_REJECTED",)})
            elif text != word.text:
                words[index] = OCRWord(text=text, confidence=confidence, bbox=word.bbox,
                                       notes=word.notes + ("NUMERIC_REREAD", "OCR_PASSES_DISAGREED") + extra)
            else:
                words[index] = word.model_copy(update={"confidence": max(word.confidence, confidence),
                                                       "notes": word.notes + ("NUMERIC_REREAD",) + extra})
        if words == list(line.words):
            refined_lines.append(line)
            continue
        notes = tuple(sorted({note for word in words for note in word.notes}))
        refined_lines.append(line.model_copy(update={
            "text": " ".join(word.text for word in words), "words": tuple(words), "notes": notes,
            "confidence": round(sum(word.confidence for word in words) / len(words), 3)}))
    return refined_lines
