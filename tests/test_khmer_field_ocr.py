from datetime import date
import unittest

from PIL import Image

from kyc.documents import khmer
from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.documents.field_ocr import field_region, read_khmer_fields, vote_field
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


class RegionReader:
    def __init__(self):
        self.calls = []

    def available_languages(self):
        return {"khm", "eng", "script/Khmer"}

    def read_region(self, image, box, languages, alphabet=None, mode=6):
        self.calls.append((image.size, languages, alphabet, mode))
        return [OCRLine(text="សុខ សុភា", confidence=.91, bbox=(0, 0, 1, 1))]


class ReaderTests(unittest.TestCase):
    def test_dedicated_name_uses_single_line_khmer_models_and_multiple_scales(self):
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
        self.assertTrue(all(call[1] in (("khm",), ("script/Khmer",)) and call[2] is None and call[3] == 7 for call in reader.calls))
        sizes = {call[0] for call in reader.calls}
        self.assertEqual(len(sizes), 3)


if __name__ == "__main__":
    unittest.main()
