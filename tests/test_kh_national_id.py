from datetime import date
import unittest

from kyc.documents import khmer
from kyc.documents.adapters import adapter_for
from kyc.documents.adapters.kh_national_id import find_label
from kyc.documents.refine import NumericRefinement, refine_numeric_words
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import OCRLine, OCRWord

TODAY = date(2026, 10, 5)


def line(text, y, confidence=0.95):
    return OCRLine(text=text, confidence=confidence, bbox=(0.3, y, 0.9, y + 0.05))


def front_lines(**overrides):
    values = {"number": "០១០២០៣០៤០", "dob": "១៥.០៣.១៩៩០", "validity": "០១.០១.២០២០ ដល់ថ្ងៃ ៣១.១២.២០២៩",
              "name": "គោត្តនាម និងនាម: សុខ សុភា", "address_conf": 0.95} | overrides
    return [line("ព្រះរាជាណាចក្រកម្ពុជា", 0.02), line("អត្តសញ្ញាណប័ណ្ណ", 0.08), line(values["number"], 0.15),
            line(values["name"], 0.25), line("SOK SOPHEA", 0.31),
            line(f"ថ្ងៃខែឆ្នាំកំណើត: {values['dob']} ភេទ: ស្រី កម្ពស់: ១៦០ ស.ម", 0.40),
            line("ទីកន្លែងកំណើត: ភ្នំពេញ", 0.48), line("អាសយដ្ឋាន: ផ្ទះលេខ ១២ ផ្លូវ ២៧១", 0.56),
            line("សង្កាត់ ទឹកល្អក់", 0.62, values["address_conf"]), line(f"សុពលភាព: {values['validity']}", 0.70),
            line("ភិនភាគ: ប្រជ្រុយ", 0.78)]


BACK = [line("IDKHM0102030401<<<<<<<<<<<<<<<", 0.7), line("9003155F2912316KHM<<<<<<<<<<<0", 0.78)]


class KhmerNormalizationTests(unittest.TestCase):
    def test_dates_in_both_digit_scripts(self):
        self.assertEqual(khmer.parse_date("១៥.០៣.១៩៩០").value, "1990-03-15")
        self.assertEqual(khmer.parse_date("15/03/1990").value, "1990-03-15")
        self.assertEqual(khmer.parse_date("31.02.2000").flags, ("INVALID_DATE",))
        self.assertIsNone(khmer.parse_date("no date").value)

    def test_mixed_digit_scripts_are_flagged_not_hidden(self):
        result = khmer.parse_date("១៥.០៣.9៩៩០")
        self.assertIn("MIXED_DIGIT_SCRIPTS", result.flags)
        self.assertEqual(result.value, "9990-03-15")  # converted literally, never "corrected"

    def test_sex_names_and_invisible_characters(self):
        self.assertEqual(khmer.parse_sex("ប្រុស").value, "M")
        self.assertEqual(khmer.parse_sex("ស្រី").value, "F")
        self.assertEqual(khmer.parse_sex("?").flags, ("SEX_UNRECOGNIZED",))
        self.assertEqual(khmer.latin_name(" sok​ sophea ").value, "SOK SOPHEA")
        self.assertEqual(khmer.khmer_text("SOK").flags, ("EXPECTED_KHMER_SCRIPT",))
        self.assertEqual(khmer.clean("សុខ​  សុភា"), "សុខ សុភា")

    def test_fuzzy_label_matching_tolerates_ocr_noise(self):
        self.assertEqual(find_label("ថ្ងៃខែឆ្នាំកំណើត: x", "ថ្ងៃខែឆ្នាំកំណើត", 0.78)[2], False)
        self.assertTrue(find_label("ថ្លែខែឆ្នាំកំណើត: x", "ថ្ងៃខែឆ្នាំកំណើត", 0.78)[2])
        self.assertIsNone(find_label("SOK SOPHEA", "ថ្ងៃខែឆ្នាំកំណើត", 0.78))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = adapter_for(DocumentType.KH_NATIONAL_ID)

    def test_registry_only_claims_implemented_adapters(self):
        self.assertIsNotNone(self.adapter)
        self.assertIsNone(adapter_for(DocumentType.PASSPORT))
        self.assertEqual(self.adapter.required_sides(), ("FRONT", "BACK"))

    def test_classification_of_sides_and_other_documents(self):
        front = self.adapter.classify(front_lines(), "FRONT")
        self.assertEqual((front.document_type, front.document_side), (DocumentType.KH_NATIONAL_ID, "FRONT"))
        self.assertGreaterEqual(front.confidence, 0.9)
        back = self.adapter.classify(BACK, "BACK")
        self.assertEqual(back.document_side, "BACK")
        passport = self.adapter.classify([line("KINGDOM OF CAMBODIA PASSPORT", 0.1), line("P<KHMSOK<<SOPHEA<<<<<<<<<<<<<<<<<<<<<<<<<<<", 0.8)], "FRONT")
        self.assertEqual(passport.document_type, DocumentType.PASSPORT)
        unknown = self.adapter.classify([line("hello world", 0.1)], "FRONT")
        self.assertEqual((unknown.document_type, unknown.confidence), (DocumentType.UNKNOWN, 0.0))

    def test_extraction_produces_canonical_fields_with_provenance(self):
        document = self.adapter.extract_fields({"FRONT": front_lines(), "BACK": BACK})
        self.assertEqual(document.document_number, "010203040")
        self.assertEqual(document.full_name_local, "សុខ សុភា")
        self.assertEqual(document.full_name, "SOK SOPHEA")
        self.assertEqual(str(document.date_of_birth), "1990-03-15")
        self.assertEqual(document.sex, "F")
        self.assertEqual(document.place_of_birth, "ភ្នំពេញ")
        self.assertEqual(document.address, "ផ្ទះលេខ ១២ ផ្លូវ ២៧១ សង្កាត់ ទឹកល្អក់")
        self.assertEqual((str(document.issue_date), str(document.expiry_date)), ("2020-01-01", "2029-12-31"))
        self.assertTrue(document.mrz.startswith("IDKHM"))
        self.assertIsNone(document.issuing_authority)  # not on the card: null, never invented
        fields = {item.field: item for item in document.fields}
        self.assertEqual(fields["date_of_birth"].raw_value, "១៥.០៣.១៩៩០")
        self.assertEqual(fields["nationality"].source, "DERIVED")
        self.assertEqual(fields["mrz"].flags, ("UNPARSED_UNTIL_PHASE_5",))
        self.assertTrue(all(item.bbox for item in document.fields if item.source == "OCR" and item.normalized_value))

    def test_clean_card_validates_and_engines_not_built_are_unavailable(self):
        document = self.adapter.extract_fields({"FRONT": front_lines(), "BACK": BACK})
        results = {check.check_type: check.result for check in self.adapter.validate_fields(document, TODAY)}
        for name in ("REQUIRED_FIELDS", "DOCUMENT_NUMBER_FORMAT", "DATE_CONSISTENCY", "EXPIRY", "OCR_CONFIDENCE", "SCRIPT_CONSISTENCY"):
            self.assertEqual(results[name], CheckResult.PASS, name)
        for name in ("MRZ", "BARCODE", "PORTRAIT"):
            self.assertEqual(results[name], CheckResult.UNAVAILABLE)
        self.assertNotIn(CheckResult.PASS, [results[name] for name in ("MRZ", "BARCODE", "PORTRAIT")])

    def test_wrapped_value_takes_the_weakest_line_confidence(self):
        document = self.adapter.extract_fields({"FRONT": front_lines(address_conf=0.5), "BACK": BACK})
        self.assertEqual({item.field: item for item in document.fields}["address"].confidence, 0.5)
        checks = {check.check_type: check for check in self.adapter.validate_fields(document, TODAY)}
        self.assertEqual(checks["OCR_CONFIDENCE"].result, CheckResult.REVIEW)
        self.assertIn("address", checks["OCR_CONFIDENCE"].details["fields"])

    def test_problems_are_flagged_for_review_or_fail(self):
        cases = [
            ({"validity": "០១.០១.២០១០ ដល់ថ្ងៃ ៣១.១២.២០១៩"}, "EXPIRY", CheckResult.FAIL, "EXPIRED_DOCUMENT"),
            ({"dob": "១៥.០៣.9៩៩០"}, "SCRIPT_CONSISTENCY", CheckResult.REVIEW, "MIXED_DIGIT_SCRIPTS"),
            ({"dob": "១៥.០៣.9៩៩០"}, "DATE_CONSISTENCY", CheckResult.REVIEW, "DATE_OF_BIRTH_IMPOSSIBLE"),
            ({"validity": "០១.០១.២០២០ ដល់ថ្ងៃ ៣១.១២.២០៤០"}, "DATE_CONSISTENCY", CheckResult.REVIEW, "UNUSUAL_VALIDITY_PERIOD"),
            ({"number": "១២៣៤"}, "REQUIRED_FIELDS", CheckResult.FAIL, "CRITICAL_FIELD_MISSING"),
            ({"name": "គោត្តនាម និងនាម:"}, "REQUIRED_FIELDS", CheckResult.FAIL, "CRITICAL_FIELD_MISSING"),
        ]
        for overrides, check_type, result, code in cases:
            with self.subTest(code=code, overrides=overrides):
                document = self.adapter.extract_fields({"FRONT": front_lines(**overrides), "BACK": BACK})
                check = {c.check_type: c for c in self.adapter.validate_fields(document, TODAY)}[check_type]
                self.assertEqual(check.result, result)
                self.assertIn(code, check.reason_codes)

    def test_label_value_on_the_next_line(self):
        lines = front_lines()
        lines[3] = line("គោត្តនាម និងនាម:", 0.25)
        lines.insert(4, line("សុខ សុភា", 0.28))
        document = self.adapter.extract_fields({"FRONT": lines, "BACK": BACK})
        self.assertEqual(document.full_name_local, "សុខ សុភា")
        self.assertIn("VALUE_ON_NEXT_LINE", {item.field: item for item in document.fields}["full_name_local"].flags)


class FakeReader:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def reread(self, image, bbox, languages, alphabet, mode=7):
        self.calls.append(mode)
        return self.answers[mode]


class NumericRefinementTests(unittest.TestCase):
    spec = NumericRefinement(("script/Khmer",), khmer.KHMER_DIGITS + "./")

    def numeric_line(self, text="09០២០៣០៤០"):
        word = OCRWord(text=text, confidence=0.7, bbox=(0.1, 0.1, 0.3, 0.15))
        return OCRLine(text=text, confidence=0.7, bbox=word.bbox, words=(word,))

    def test_disagreeing_reread_replaces_text_and_is_noted(self):
        [result] = refine_numeric_words(FakeReader({7: ("០១០២០៣០៤០", 0.9)}), None, [self.numeric_line()], self.spec)
        self.assertEqual(result.text, "០១០២០៣០៤០")
        self.assertIn("OCR_PASSES_DISAGREED", result.words[0].notes)

    def test_zero_confidence_reread_needs_a_confirming_pass(self):
        agreed = FakeReader({7: ("០១០២០៣០៤០", 0.0), 8: ("០១០២០៣០៤០", 0.0)})
        [result] = refine_numeric_words(agreed, None, [self.numeric_line()], self.spec)
        self.assertIn("CONSTRAINED_PASSES_AGREED", result.words[0].notes)
        self.assertEqual(agreed.calls, [7, 8])
        disagreed = FakeReader({7: ("០១០២០៣០៤០", 0.0), 8: ("០៧០២០៣០៤០", 0.0)})
        [result] = refine_numeric_words(disagreed, None, [self.numeric_line()], self.spec)
        self.assertEqual(result.text, "09០២០៣០៤០")
        self.assertIn("NUMERIC_REREAD_REJECTED", result.words[0].notes)

    def test_structure_changing_reread_is_rejected(self):
        [result] = refine_numeric_words(FakeReader({7: ("១៥០៣១៩៩០", 0.95)}), None, [self.numeric_line("១៥.0៣.9៩៩០")], self.spec)
        self.assertEqual(result.text, "១៥.0៣.9៩៩០")
        self.assertIn("NUMERIC_REREAD_REJECTED", result.words[0].notes)

    def test_non_numeric_words_are_untouched(self):
        reader = FakeReader({})
        word = OCRWord(text="សុខ", confidence=0.9, bbox=(0, 0, 0.1, 0.1))
        original = OCRLine(text="សុខ", confidence=0.9, bbox=word.bbox, words=(word,))
        self.assertEqual(refine_numeric_words(reader, None, [original], self.spec), [original])
        self.assertEqual(reader.calls, [])
