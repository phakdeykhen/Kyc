"""MRZ OCR provenance and mapping across independent OCR passes."""

import unittest

from PIL import Image

from kyc.domain.identity import OCRLine
from kyc.mrz.reader import read_mrz_lines
from tests.mrz_build import td3


class RegionOCR:
    def __init__(self, passes):
        self.passes = iter(passes)
        self.sizes = []

    def read_region(self, image, region, languages, alphabet, mode=6):
        self.sizes.append(image.size)
        return next(self.passes)


class MRZReaderTests(unittest.TestCase):
    def test_preserves_raw_ocr_and_maps_bbox_to_the_corrected_page(self):
        raw = td3()[1].replace("900315", "9OO315")
        line = OCRLine(text=raw, confidence=0.7, bbox=(0.1, 0.25, 0.9, 0.5))
        ocr = RegionOCR([[line], [], [], []])
        found = read_mrz_lines(ocr, Image.new("L", (1000, 700)), (0.0, 0.7, 1.0, 1.0))
        self.assertEqual(found[0].text, raw)
        self.assertEqual(found[0].bbox, (0.1, 0.775, 0.9, 0.85))
        self.assertIn("MRZ_PASS", found[0].notes)
        self.assertEqual(ocr.sizes, [(1000, 210), (1000, 210), (2000, 420), (1000, 210)])

    def test_deduplicates_equivalent_reads_but_keeps_distinct_candidates(self):
        first, second = td3()
        line = lambda text: OCRLine(text=text, confidence=0.9, bbox=(0.0, 0.0, 1.0, 0.1))
        ocr = RegionOCR([[line(first), line("PASSPORT")], [line(first.lower())], [line(second)], []])
        found = read_mrz_lines(ocr, Image.new("L", (1000, 700)), (0.0, 0.7, 1.0, 1.0))
        self.assertEqual([item.text for item in found], [first, second])
