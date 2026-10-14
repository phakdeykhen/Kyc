from datetime import date
import unittest

import cv2
import numpy as np
from PIL import Image

from kyc.documents import khmer
from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.documents import card_rows
from kyc.documents.field_ocr import _strip_label, _value, field_region, read_khmer_fields, vote_field
from kyc.domain.enums import CheckResult
from kyc.domain.identity import OCRLine, OCRWord
from tests.test_kh_national_id import BACK, front_lines, line


class FieldCropTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CambodiaNationalIDAdapter()

    def test_name_crop_uses_value_word_boxes_and_preserves_small_marks(self):
        label = "គោត្តនាម និងនាម:"
        name = OCRLine(text=f"{label} សុខ សុភា", confidence=.95, bbox=(.26, .10, .85, .17), words=(
            OCRWord(text=label, confidence=.9, bbox=(.26, .10, .55, .17)),
            OCRWord(text="សុខ", confidence=.95, bbox=(.56, .10, .68, .17)),
            OCRWord(text="សុភា", confidence=.95, bbox=(.69, .10, .85, .17)),
        ))
        region, labelled = field_region(self.adapter, "full_name_local", [name, line("SOK SOPHEA", .20)], True)
        self.assertFalse(labelled)
        self.assertGreater(region[0], .54)
        self.assertLess(region[1], .10)
        self.assertLess(region[3], .20)

    def test_noisy_mrz_is_a_hard_address_boundary(self):
        lines = [line("អាសយដ្ឋាន: ភូមិថ្មី", .50), line("ឃុំស្រែស្តុក", .56),
                 line("cc IDKHM1605811389<<<<ccK<", .62)]
        region, _ = field_region(self.adapter, "address", lines, True)
        self.assertLessEqual(region[3], .62)
        doc = self.adapter.extract_fields({"FRONT": lines, "BACK": []})
        self.assertEqual(doc.address, "ភូមិថ្មី ឃុំស្រែស្តុក")
        self.assertNotIn("IDKHM", doc.address)

    def test_template_fallback_requires_a_rectified_document(self):
        self.assertIsNone(field_region(self.adapter, "full_name_local", [], False))
        self.assertIsNotNone(field_region(self.adapter, "full_name_local", [], True))

    def test_name_template_does_not_shift_to_a_latin_name_row(self):
        latin = line("REN RANIT", .95)
        region, labelled = field_region(self.adapter, "full_name_local", [latin], True)
        self.assertTrue(labelled)
        self.assertEqual(region, self.adapter.layout.khmer_field_regions["full_name_local"])

    def test_detected_label_excludes_preceding_pattern_noise(self):
        reads = [line("ពពព: ហាហា", .05), line("គោត្តនាម និងនាម: សុខ សុភា", .1)]
        self.assertEqual(_value(self.adapter, "full_name_local", reads, True), "សុខ សុភា")


class VoteTests(unittest.TestCase):
    box = (.4, .1, .9, .2)

    def test_agreeing_reads_win_over_a_confident_outlier_without_boosting_confidence(self):
        field = vote_field("full_name_local", [("original", "សុខ សុភា", .75),
            ("upscale", "សុខ សុភា", .77), ("sharp", "ចាន់ ដារ៉ា", .99)], self.box)
        self.assertEqual(field.text, "សុខ សុភា")
        self.assertLess(field.confidence, .8)
        self.assertIn("FIELD_UNCERTAIN", field.notes)
        self.assertIn("ចាន់ ដារ៉ា", field.raw_text)

    def test_whitespace_variations_agree_but_a_single_read_stays_uncertain(self):
        field = vote_field("full_name_local", [("a", "សុខ សុភា", .92), ("b", "សុខសុភា", .94)], self.box)
        self.assertIn("KHMER_MULTIPASS_AGREED", field.notes)
        self.assertNotIn("FIELD_UNCERTAIN", field.notes)
        self.assertAlmostEqual(field.confidence, .93)
        single = vote_field("full_name_local", [("a", "សុខ សុភា", .99)], self.box)
        self.assertIn("FIELD_UNCERTAIN", single.notes)
        self.assertLess(single.confidence, .8)

    def test_empty_khmer_crop_does_not_use_a_latin_name_as_its_value(self):
        field = vote_field("full_name_local", [("a", "SOK SOPHEA", .99)], self.box)
        self.assertEqual((field.text, field.confidence), ("", 0))
        self.assertIn("KHMER_FIELD_NOT_DETECTED", field.notes)

    def test_roi_provenance_and_uncertainty_reach_field_validation(self):
        adapter = CambodiaNationalIDAdapter()
        dedicated = vote_field("full_name_local", [("a", "សុខ សុភា", .95), ("b", "ចាន់ ដារ៉ា", .96)], self.box)
        doc = adapter.extract_fields({"FRONT": front_lines() + [dedicated] + BACK, "BACK": []})
        name = next(field for field in doc.fields if field.field == "full_name_local")
        self.assertIn("[a]", name.raw_value)
        self.assertIn("FIELD_UNCERTAIN", name.flags)
        checks = {check.check_type: check for check in adapter.validate_fields(doc, date(2026, 10, 7))}
        self.assertEqual(checks["KHMER_NAME"].result, CheckResult.REVIEW)

    def test_mixed_mrz_and_corrupt_khmer_marks_are_flagged(self):
        self.assertIn("OCR_NOISE_DETECTED", khmer.khmer_text("ភូមិថ្មី IDKHM123<<<<").flags)
        self.assertIn("OCR_NOISE_DETECTED", khmer.khmer_text("សុខ សុភា . : ត").flags)
        self.assertIn("KHMER_ORDERING_UNCERTAIN", khmer.khmer_text("សុខ្").flags)


    def test_near_agreeing_noisy_reads_support_their_shared_reading(self):
        field = vote_field("address", [("a", "ឃុំជៀប ស្រុកទឹកផុស", .4), ("b", "ឃុំជៀប ស្រុកទឹកផុស", .4),
                                       ("c", "ឃុំជៀប់ ស្រុកទឹកផុស", .6), ("d", "ឃុំជៀប ស្រុកទឹកផស", .6),
                                       ("e", "ម្ម", .9), ("f", "ម្ម", .9)], self.box)
        self.assertEqual(field.text, "ឃុំជៀប ស្រុកទឹកផុស")

    def test_label_debris_before_an_early_colon_is_dropped(self):
        self.assertEqual(_strip_label("ទីកន្លែងកំណើត់ៈ ឃុំតំបែរ ស្រុកតំបែរ"), "ឃុំតំបែរ ស្រុកតំបែរ")
        self.assertEqual(_strip_label("ANE: ផ្ទះ១០៩ ផ្លូវ/ក្រុម១"), "ផ្ទះ១០៩ ផ្លូវ/ក្រុម១")
        # A colon late in the text is not a label separator.
        self.assertEqual(_strip_label("ប្រជ្រុយចំ ១ ស.ម មុខលើចុង:"), "ប្រជ្រុយចំ ១ ស.ម មុខលើចុង")


def row(top, text, left=.24, ink=.06):
    return card_rows.Row(top=top, bottom=top + .07, left=left, right=.9, ink_height=ink,
                         words=[OCRWord(text=text, confidence=.5, bbox=(left, top, .9, top + .07))])


class CardRowTests(unittest.TestCase):
    def card(self):
        return [row(.0, "011301069", left=.62, ink=.03), row(.08, "គោត្តនាមនិងនាម: ខុន ពិសិដ្ឋ"),
                row(.16, "KHON PISETH", left=.4, ink=.035), row(.21, "ថ្ងៃខែឆ្នាំកំណើត: ២០.០៥.២០០១ ភេទ: ប្រុស"),
                row(.28, "ទីកន្លែងកំណើត: ឃុំតាខ្មៅ"), row(.34, "អាសយដ្ឋាន: ផ្ទះគ្មានលេខ"), row(.41, "សង្កាត់ព្រែកថ្មី"),
                row(.49, "សុពលភាព: ២០.១០.២០១៧ ដល់ថ្ងៃ ១៩.១០.២០២៧"), row(.55, "ភិនភាគ: ប្រជ្រុយ")]

    def test_rows_are_assigned_by_card_order_even_with_unreadable_labels(self):
        rows = self.card()
        for item in rows:
            item.words[0] = item.words[0].model_copy(update={"text": item.words[0].text.replace("គោត្តនាមនិងនាម", "ធាគ្គនា")})
        assigned = card_rows.assign_rows(rows)
        self.assertIs(assigned["full_name_local"][0], rows[1])
        self.assertIs(assigned["place_of_birth"][0], rows[4])
        self.assertEqual(assigned["address"], rows[5:7])

    def test_a_noisy_latin_row_is_not_taken_for_the_khmer_name(self):
        rows = self.card()
        rows[2] = row(.16, "%. , ស40៩ ‘58:", ink=.061)
        self.assertIs(card_rows.assign_rows(rows)["full_name_local"][0], rows[1])

    def test_no_assignment_without_validity_and_birth_anchors(self):
        rows = self.card()
        rows[7] = row(.49, "ពពព")
        self.assertEqual(card_rows.assign_rows(rows), {})
        self.assertEqual(card_rows.assign_rows(self.card()[:4]), {})

    def test_value_starts_after_the_label_colon(self):
        label = OCRWord(text="អាសយដ្ឋាន:", confidence=.5, bbox=(.24, .3, .35, .36))
        item = card_rows.Row(top=.3, bottom=.37, left=.24, right=.7, ink_height=.06, words=[label])
        self.assertAlmostEqual(card_rows.value_start(item, .33), .35)
        item.words = []
        self.assertEqual(card_rows.value_start(item, .33), .33)

    def test_segmentation_separates_rows_and_ignores_thin_pattern_lines(self):
        pixels = np.full((400, 800, 3), 210, np.uint8)
        for top in (40, 120, 200):
            for left in range(60, 500, 40):
                pixels[top:top + 30, left:left + 25] = 20  # glyph-like blocks
        cv2.line(pixels, (0, 390), (799, 10), (60, 60, 60), 1)  # guilloche stroke
        rows = card_rows.segment_rows(Image.fromarray(pixels), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(abs(r.right - 485 / 800) < .02 for r in rows))
        crop = card_rows.isolate(Image.fromarray(pixels), rows[1], card_rows.glyph_box(rows[1], 0.0, (800, 400)))
        self.assertLess(crop.height, 80)


class RegionReader:
    def __init__(self):
        self.calls = []

    def available_languages(self):
        return {"khm", "eng", "script/Khmer"}

    def read_region(self, image, box, languages, alphabet=None, mode=6):
        self.calls.append((image.size, languages, alphabet, mode))
        return [OCRLine(text="សុខ សុភា", confidence=.91, bbox=(0, 0, 1, 1))]


class ReaderTests(unittest.TestCase):
    def test_dedicated_name_uses_single_line_khmer_models_at_the_printed_scale(self):
        adapter = CambodiaNationalIDAdapter()
        reader = RegionReader()
        label = "គោត្តនាម និងនាម:"
        row = OCRLine(text=f"{label} សុខ សុភា", confidence=.9, bbox=(.26, .1, .9, .2), words=(
            OCRWord(text=label, confidence=.9, bbox=(.26, .1, .55, .2)),
            OCRWord(text="សុខ សុភា", confidence=.9, bbox=(.56, .1, .9, .2)),
        ))
        # No template fallback for the other fields because the document was not located.
        [result] = read_khmer_fields(reader, Image.new("RGB", (1600, 1000)), adapter, [row], False)
        self.assertEqual(result.text, "សុខ សុភា")
        self.assertTrue(all(call[1] in (("khm",), ("script/Khmer",)) and call[2] is None and call[3] in (7, 13) for call in reader.calls))
        # Upscaled Khmer rows make the LSTM return nothing; every variant keeps the crop's own size.
        sizes = {call[0] for call in reader.calls}
        self.assertEqual(len(sizes), 1)


if __name__ == "__main__":
    unittest.main()
