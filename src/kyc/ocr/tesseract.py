"""Tesseract 5 OCR engine (Khmer + Latin) behind the generic OCREngine contract.

Runs the CLI as a subprocess with a timeout; images go over stdin, never to disk.
Word results are grouped into lines with averaged confidence and normalized boxes.
"""

from collections import defaultdict
from collections.abc import Sequence
from functools import cached_property
from io import BytesIO
import os
import shutil
import subprocess

from PIL import Image

from kyc.documents.khmer import clean
from kyc.domain.identity import OCRLine, OCRWord


class OCRUnavailable(RuntimeError):
    pass


class TesseractOCREngine:
    def __init__(self, command: str = "tesseract", timeout_seconds: float = 20.0, page_segmentation_mode: int = 6):
        self.command = command
        self.timeout = timeout_seconds
        self.psm = page_segmentation_mode

    @cached_property
    def engine_version(self) -> str:
        output = self._run([self.command, "--version"], b"").splitlines()
        return output[0].strip() if output else "tesseract-unknown"

    def available_languages(self) -> set[str]:
        lines = self._run([self.command, "--list-langs"], b"").splitlines()
        return {line.strip() for line in lines[1:] if line.strip()}

    def is_available(self, languages: Sequence[str] = ("khm", "eng")) -> bool:
        if shutil.which(self.command) is None:
            return False
        try:
            return set(languages) <= self.available_languages()
        except OCRUnavailable:
            return False

    def _run(self, arguments: list[str], payload: bytes) -> str:
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            completed = subprocess.run(arguments, input=payload, capture_output=True, timeout=self.timeout,
                                       check=True, env=environment)
        except FileNotFoundError:
            raise OCRUnavailable("Tesseract is not installed.") from None
        except subprocess.TimeoutExpired:
            raise OCRUnavailable("OCR timed out.") from None
        except subprocess.CalledProcessError as error:
            raise OCRUnavailable(f"OCR failed with exit code {error.returncode}.") from None
        # Older builds print --version / --list-langs to stderr.
        return (completed.stdout or completed.stderr).decode("utf-8", errors="replace")

    def read_lines(self, image: Image.Image, languages: Sequence[str]) -> list[OCRLine]:
        buffer = BytesIO()
        image.save(buffer, "PNG")
        tsv = self._run([self.command, "stdin", "stdout", "-l", "+".join(languages), "--psm", str(self.psm), "tsv"],
                        buffer.getvalue())
        width, height = image.size
        groups: dict[tuple[int, int, int, int], list[tuple[str, float, int, int, int, int]]] = defaultdict(list)
        for row in tsv.splitlines()[1:]:
            parts = row.split("\t")
            if len(parts) != 12 or parts[0] != "5":
                continue
            text, confidence = parts[11].strip(), float(parts[10])
            if not text or confidence < 0:
                continue
            left, top, box_width, box_height = map(int, parts[6:10])
            groups[tuple(map(int, parts[1:5]))].append((text, confidence, left, top, left + box_width, top + box_height))
        lines = []
        for key in sorted(groups, key=lambda item: (min(word[3] for word in groups[item]), item)):
            words = groups[key]
            text = clean(" ".join(word[0] for word in words))
            if not text:
                continue
            box = lambda x0, y0, x1, y1: (round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4))  # noqa: E731
            lines.append(OCRLine(
                text=text,
                confidence=round(sum(word[1] for word in words) / len(words) / 100, 3),
                bbox=box(min(w[2] for w in words), min(w[3] for w in words), max(w[4] for w in words), max(w[5] for w in words)),
                words=tuple(OCRWord(text=w[0], confidence=round(w[1] / 100, 3), bbox=box(*w[2:])) for w in words),
            ))
        return lines

    def reread(self, image: Image.Image, bbox: tuple[float, float, float, float], languages: Sequence[str],
               alphabet: str, mode: int = 7) -> tuple[str, float]:
        """Single-line re-read of a region with a constrained character set."""
        width, height = image.size
        pad = 6
        crop = image.crop((max(0, round(bbox[0] * width) - pad), max(0, round(bbox[1] * height) - pad),
                           min(width, round(bbox[2] * width) + pad), min(height, round(bbox[3] * height) + pad)))
        buffer = BytesIO()
        crop.save(buffer, "PNG")
        tsv = self._run([self.command, "stdin", "stdout", "-l", "+".join(languages), "--psm", str(mode),
                         "-c", f"tessedit_char_whitelist={alphabet}", "tsv"], buffer.getvalue())
        words = [(parts[11].strip(), float(parts[10])) for parts in (row.split("\t") for row in tsv.splitlines()[1:])
                 if len(parts) == 12 and parts[0] == "5" and parts[11].strip() and float(parts[10]) >= 0]
        if not words:
            return "", 0.0
        return "".join(word for word, _ in words), round(min(conf for _, conf in words) / 100, 3)
