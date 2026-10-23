"""Regressions from a real KH ID review case: composite digit misread, unreadable validity label,
and the Khmer script model's name in Debian tessdata."""

from datetime import date
import unittest
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from kyc.documents import card_rows
from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.documents.field_ocr import _read_date_rows
from kyc.domain.identity import OCRLine
from kyc.mrz import reader
from kyc.mrz.parser import read as read_mrz
from kyc.ocr.tesseract import TesseractOCREngine
from tests.mrz_build import td1
from tests.test_kh_national_id import BACK, front_lines, line


def mrz_image(lines: list[str]) -> Image.Image:
    pixels = np.full((260, 1500, 3), 235, np.uint8)
    for index, text in enumerate(lines):
        cv2.putText(pixels, text, (20, 70 + index * 75), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3)
    return Image.fromarray(pixels)


class GlyphOCR:
    """read_region returns the scripted MRZ passes; reread answers every single-glyph read with one digit."""

    def __init__(self, passes, digit):
        self.passes, self.digit, self.rereads = iter(passes), digit, 0

    def read_region(self, image, region, languages, alphabet=None, mode=6):
        return next(self.passes, [])

    def reread(self, image, bbox, languages, alphabet, mode=7):
        self.rereads += 1
        return self.digit, 0.9


class CompositeRereadTests(unittest.TestCase):
    def setUp(self):
        self.lines = td1()  # composite digit is the last character of line 2
        self.computed = self.lines[1][-1]
        self.misread = "6" if self.computed != "6" else "5"

    def passes(self, second):
        return [[OCRLine(text=text, confidence=.8, bbox=(0.0, 0.05 + i * 0.29, 0.98, 0.3 + i * 0.29))
                 for i, text in enumerate((self.lines[0], second, self.lines[2]))], [], [], []]

    def test_a_misread_composite_is_reread_from_its_glyph(self):
        second = self.lines[1][:-1] + self.misread
        self.assertFalse(read_mrz([self.lines[0], second, self.lines[2]]).mrz_valid)
        ocr = GlyphOCR(self.passes(second), self.computed)
        found = reader.read_mrz_lines(ocr, mrz_image(self.lines), (0.0, 0.0, 1.0, 1.0))
        extra = [item for item in found if "MRZ_COMPOSITE_REREAD" in item.notes]
        self.assertEqual([item.text for item in extra], [self.lines[1]])
        self.assertTrue(read_mrz([item.text for item in found]).mrz_valid)

    def test_a_reread_that_confirms_the_wrong_digit_still_fails(self):
        second = self.lines[1][:-1] + self.misread
        ocr = GlyphOCR(self.passes(second), self.misread)
        found = reader.read_mrz_lines(ocr, mrz_image(self.lines), (0.0, 0.0, 1.0, 1.0))
        self.assertFalse(read_mrz([item.text for item in found]).mrz_valid)

    def test_a_valid_composite_is_not_reread(self):
        ocr = GlyphOCR(self.passes(self.lines[1]), self.misread)
        reader.read_mrz_lines(ocr, mrz_image(self.lines), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(ocr.rereads, 0)

    def test_disagreeing_glyph_reads_change_nothing(self):
        second = self.lines[1][:-1] + self.misread
        ocr = GlyphOCR(self.passes(second), self.computed)
        answers = iter([self.computed, self.misread] * 5)
        ocr.reread = lambda *args, **kwargs: (next(answers), 0.9)
        found = reader.read_mrz_lines(ocr, mrz_image(self.lines), (0.0, 0.0, 1.0, 1.0))
        self.assertFalse(any("MRZ_COMPOSITE_REREAD" in item.notes for item in found))


def date_row(top):
    glyphs = [(400 + i * 40, round(top * 1000) + 5, 430 + i * 40, round(top * 1000) + 45) for i in range(12)]
    return card_rows.Row(top=top, bottom=top + .06, left=.25, right=.9, ink_height=.04, words=[],
                         core=top + .03, glyphs=glyphs, foreign=set())


class DateOCR:
    def __init__(self, texts, digits):
        self.texts, self.digits = iter(texts), iter(digits)

    def read_region(self, image, box, languages, alphabet=None, mode=6):
        return [OCRLine(text=next(self.texts, ""), confidence=.5, bbox=(0, 0, 1, 1))]

    def reread(self, image, bbox, languages, alphabet, mode=7):
        return next(self.digits, ""), .5


class DateRowTests(unittest.TestCase):
    image = Image.new("RGB", (1600, 1000), (220, 220, 220))

    def test_agreeing_reads_give_both_validity_dates(self):
        ocr = DateOCR(["SIM; ០៣.០៦.២០២០ ដល់ថ្ងៃ ០២.០៦.២០៣០"] * 10, ["០៣.០៦.២០២០ ០២.០៦.២០៣០"] * 10)
        [found] = _read_date_rows(ocr, self.image, {"validity": [date_row(.5)]}, {"khm", "script/Khmer"}, .225)
        self.assertEqual(found.text, "03.06.2020 02.06.2030")
        self.assertIn("FIELD_ROI:validity", found.notes)
        self.assertIn("DATE_PASSES_AGREED", found.notes)

    def test_split_or_incomplete_reads_add_nothing(self):
        ocr = DateOCR(["០៣.០៦.២០២០ ដល់ថ្ងៃ", "០៣.០៦.២០២០ ០២.០៦.២០៣០", "០៨.០៦.២០២០ ០២.០៦.២០៣០"] + [""] * 20, [""] * 20)
        self.assertEqual(_read_date_rows(ocr, self.image, {"validity": [date_row(.5)]}, {"khm"}, .225), [])

    def test_unreadable_validity_label_falls_back_to_the_row_reread(self):
        lines = [item for item in front_lines() if "សុពលភាព" not in item.text]
        lines.append(line("SIM; ០១.០១.២០២០ ដល ០ ៣១.១២.២០", 0.70))
        dedicated = OCRLine(text="01.01.2020 31.12.2029", confidence=.9, bbox=(.22, .7, .8, .75),
                            notes=("FIELD_ROI:validity", "DATE_ROW_REREAD", "DATE_PASSES_AGREED"))
        document = CambodiaNationalIDAdapter().extract_fields({"FRONT": lines + [dedicated], "BACK": BACK})
        self.assertEqual((document.issue_date, document.expiry_date), (date(2020, 1, 1), date(2029, 12, 31)))
        issue = next(item for item in document.fields if item.field == "issue_date")
        self.assertIn("DATE_ROW_REREAD", issue.flags)

    def test_card_rows_expose_the_birth_and_validity_rows(self):
        from tests.test_khmer_field_ocr import CardRowTests
        rows = CardRowTests().card()
        assigned = card_rows.assign_rows(rows)
        self.assertIs(assigned["date_of_birth"][0], rows[3])
        self.assertIs(assigned["validity"][0], rows[7])


class TessdataLayoutTests(unittest.TestCase):
    def engine(self, installed):
        engine = TesseractOCREngine()
        listing = "List of available languages:\n" + "\n".join(installed) + "\n"
        return engine, mock.patch.object(engine, "_run", return_value=listing)

    def test_debian_flat_script_model_answers_to_its_script_name(self):
        engine, patch = self.engine(["eng", "khm", "Khmer"])
        with patch:
            self.assertIn("script/Khmer", engine.available_languages())
            self.assertEqual(engine._languages(("script/Khmer",)), "Khmer")
            self.assertEqual(engine._languages(("khm", "eng")), "khm+eng")

    def test_homebrew_script_directory_is_used_as_is(self):
        engine, patch = self.engine(["eng", "khm", "script/Khmer"])
        with patch:
            self.assertEqual(engine._languages(("script/Khmer",)), "script/Khmer")


if __name__ == "__main__":
    unittest.main()
