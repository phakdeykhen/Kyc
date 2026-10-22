from datetime import date
import unittest
from unittest.mock import patch

from kyc.documents.adapters import adapter_for
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import OCRLine
from kyc.mrz import parser
from tests.mrz_build import td1, td3

TODAY = date(2026, 10, 5)
# ICAO Doc 9303 published specimens (fictional Utopia holder).
ICAO_TD3 = ["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", "L898902C36UTO7408122F1204159ZE184226B<<<<<10"]
ICAO_TD1 = ["I<UTOD231458907<<<<<<<<<<<<<<<", "7408122F1204159UTO<<<<<<<<<<<6", "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"]
ICAO_TD2 = ["I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<", "D231458907UTO7408122F1204159<<<<<<<6"]


class ParserTests(unittest.TestCase):
    def test_icao_specimens_in_all_three_formats(self):
        for lines, form, number in [(ICAO_TD3, "TD3", "L898902C3"), (ICAO_TD1, "TD1", "D23145890"), (ICAO_TD2, "TD2", "D23145890")]:
            with self.subTest(form=form):
                result = parser.read(lines, TODAY)
                self.assertEqual(result.format, form)
                self.assertTrue(result.mrz_valid, result.check_digits)
                self.assertEqual(result.document_number, number)
                self.assertEqual((result.surname, result.given_names), ("ERIKSSON", "ANNA MARIA"))
                self.assertEqual((result.date_of_birth, result.expiry_date), (date(1974, 8, 12), date(2012, 4, 15)))
                self.assertEqual((result.sex, result.nationality, result.issuing_state), ("F", "UTO", "UTO"))

    def test_check_digit_algorithm(self):
        self.assertEqual(parser.check_digit("L898902C3"), "6")
        self.assertEqual(parser.check_digit("740812"), "2")
        self.assertEqual(parser.check_digit("<<<<<<"), "0")

    def test_a_single_corrupted_digit_is_caught(self):
        result = parser.read([ICAO_TD3[0], ICAO_TD3[1].replace("740812", "740813")], TODAY)
        self.assertFalse(result.mrz_valid)
        self.assertFalse(result.check_digits["date_of_birth"]["valid"])
        self.assertFalse(result.check_digits["composite"]["valid"])
        self.assertTrue(result.check_digits["document_number"]["valid"])

    def test_numeric_substitutions_only_in_numeric_fields_and_reported(self):
        result = parser.read([ICAO_TD3[0], "L898902C36UTO74O8122F12O4159ZE184226B<<<<<10"], TODAY)
        self.assertTrue(result.mrz_valid)
        self.assertEqual(result.substitutions, 2)
        self.assertIn("MRZ_NUMERIC_CHARACTER_SUBSTITUTION", result.flags)
        # Alphanumeric document numbers are never "corrected": an O here stays an O and fails its check digit.
        self.assertFalse(parser.read([ICAO_TD3[0], ICAO_TD3[1].replace("L898902C3", "L8989O2C3")], TODAY).mrz_valid)

    def test_lost_trailing_filler_is_restored_but_data_lines_are_not_stretched(self):
        result = parser.read(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<", ICAO_TD3[1]], TODAY)
        self.assertEqual(result.format, "TD3")
        self.assertTrue(result.mrz_valid)
        self.assertIn("MRZ_LINE_LENGTH_ADJUSTED", result.flags)
        # Three TD1 lines must never be padded into a TD3 reading.
        self.assertEqual(parser.read(ICAO_TD1, TODAY).format, "TD1")

    def test_best_block_is_chosen_across_ocr_passes(self):
        noisy = ["P<KHMSOK<<SOPHEA<<<<<<<<<<<<<<<< SSS KKK KKK", td3()[1], "P<KHMSOK<<SOPHEA<<<<<<<<<<<<<<",
                 "N0O12345679KHM9003152F3008111<<<<<<<<<<<<<<<2"]
        result = parser.read(noisy, TODAY)
        self.assertTrue(result.mrz_valid)
        self.assertEqual(result.full_name, "SOPHEA SOK")

    def test_filler_misreads_in_names_are_removed_and_flagged(self):
        lines = td1()
        lines[2] = "SOK<<SOPHEA<<<<<<<<<<<<<<<<K<K"
        result = parser.read(lines, TODAY)
        self.assertEqual(result.full_name, "SOPHEA SOK")
        self.assertIn("MRZ_NAME_FILLER_NOISE_REMOVED", result.flags)
        single = parser.read(["P<UTOLI<<A<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<", ICAO_TD3[1]], TODAY)
        self.assertEqual(single.given_names, "A")  # a genuine one-letter name survives

    def test_name_line_most_passes_agree_on_wins_over_a_stray_letter(self):
        # Real-OCR regression: one pass reads filler as "<K", the others agree on the clean name;
        # the overlong constrained read ("…<K<K", 31 characters) still counts as a vote.
        first, second, _ = td1()
        passes = [first, second, "SOK<<SOPHEA<<<<<<<<<<<<<<<<<K<K", "SOK<<SOPHEA<K<<<<<<<<", "SOK<<SOPHEA<<<<<<<<<<<<<<<<KKK"]
        result = parser.read(passes, TODAY)
        self.assertEqual((result.surname, result.given_names), ("SOK", "SOPHEA"))

    def test_td1_first_line_with_lost_or_misread_filler_and_o_for_zero_is_repaired(self):
        # Real-card regression: line 1 was never read at full width, and '0' in the number came out as 'O'.
        first, second, third = td1(number="010203040")
        passes = ["IDKHMO102030402<<<<<<<", "IDKHMO102030402<<<<<CCECEECCECCC", second, third]
        result = parser.read(passes, TODAY)
        self.assertEqual(result.document_number, "010203040")
        self.assertTrue(result.check_digits["document_number"]["valid"])
        self.assertIn("MRZ_CHAR_CORRECTED", result.flags)

    def test_td1_first_line_shifted_by_an_inserted_character_is_not_repaired(self):
        # "IDKHM" + "O" + "010203040" + check: the real check digit lands in the optional zone.
        first, second, third = td1(number="010203040")
        shifted = "IDKHMO" + first[5:15] + "K<<<<<<<"
        self.assertEqual(parser._td1_head_repairs(shifted), [])
        self.assertIsNone(parser.read([shifted, second, third], TODAY))

    def test_comparison_reports_disagreement_without_resolving_it(self):
        result = parser.read(td3(), TODAY)
        outcome = parser.compare(result, {"document_number": "NO1234567", "date_of_birth": "1990-03-15",
                                          "full_name": "SOPHEA SOK", "nationality": "KH"})
        self.assertEqual(outcome["document_number"], "MISMATCH")
        self.assertEqual(outcome["date_of_birth"], "MATCH")
        self.assertEqual(outcome["full_name"], "MATCH")
        self.assertEqual(outcome["nationality"], "MATCH")
        self.assertEqual(outcome["expiry_date"], "MRZ_ONLY")

    def test_unreadable_input(self):
        self.assertIsNone(parser.read(["hello world", "not an mrz"], TODAY))

    def test_parse_rejects_wrong_counts_lengths_and_characters(self):
        valid = tuple(td3())
        for form, lines in [("TD4", valid), ("TD3", valid[:1]), ("TD3", (valid[0] + "<", valid[1])),
                            ("TD3", (valid[0], valid[1][:-1])), ("TD3", (valid[0].lower(), valid[1])),
                            ("TD3", (valid[0].replace("SOK", "SÓK"), valid[1]))]:
            with self.subTest(form=form, lines=lines), self.assertRaises(ValueError):
                parser.parse(form, lines, TODAY)
        for text in ("12 34", "abc", "²", "123!"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parser.check_digit(text)

    def test_check_digit_substitutions_are_counted_once_and_used_in_composite(self):
        lines = [ICAO_TD3[0].ljust(44, "<"), ICAO_TD3[1]]
        characters = list(lines[1])
        for index, character in {9: "G", 19: "Z", 42: "I", 43: "O"}.items():
            characters[index] = character
        lines[1] = "".join(characters)
        result = parser.read(lines, TODAY)
        self.assertTrue(result.mrz_valid, result.check_digits)
        self.assertEqual(result.substitutions, 4)
        self.assertEqual(result.lines[1], lines[1])  # original OCR evidence is retained

        lines = td1(number="D23145890", birth="740812", expiry="120415", nationality="UTO", state="UTO")
        lines[0] = lines[0][:14] + "T" + lines[0][15:]
        lines[1] = lines[1][:6] + "Z" + lines[1][7:29] + "G"
        result = parser.read(lines, TODAY)
        self.assertTrue(result.mrz_valid, result.check_digits)
        self.assertEqual(result.substitutions, 3)

    def test_icao_unknown_birth_date_is_distinguished_from_corrupt_date(self):
        for birth in ("<<<<<<", "90<<<<", "9003<<", "9<0315"):
            with self.subTest(birth=birth):
                result = parser.read(td3(birth=birth), TODAY)
                self.assertTrue(result.mrz_valid, result.flags)
                self.assertIsNone(result.date_of_birth)
                self.assertIn("MRZ_DATE_OF_BIRTH_INCOMPLETE", result.flags)
                self.assertNotIn("MRZ_DATE_OF_BIRTH_INVALID", result.flags)
        result = parser.read(td1(birth="<<<<<<"), TODAY)
        self.assertTrue(result.mrz_valid, result.flags)

    def test_checksum_success_cannot_make_impossible_or_illegal_fields_valid(self):
        cases = [(td3(birth="900230"), "MRZ_DATE_OF_BIRTH_INVALID"),
                 (td3(birth="90<032"), "MRZ_DATE_OF_BIRTH_INVALID"),
                 (td3(expiry="301332"), "MRZ_EXPIRY_INVALID"),
                 (td3(expiry="<<<<<<"), "MRZ_EXPIRY_INVALID"),
                 (td3(sex="X"), "MRZ_SEX_INVALID"),
                 (td3(nationality="123"), "MRZ_NATIONALITY_INVALID"),
                 (td3(surname="S0K"), "MRZ_NAME_INVALID"),
                 (td3(number=""), "MRZ_DOCUMENT_NUMBER_INVALID")]
        for lines, flag in cases:
            with self.subTest(flag=flag):
                result = parser.read(lines, TODAY)
                self.assertTrue(all(item["valid"] for item in result.check_digits.values()))
                self.assertFalse(result.data_valid)
                self.assertFalse(result.mrz_valid)
                self.assertIn(flag, result.flags)

    def test_filler_check_digits_are_only_allowed_for_unused_td3_personal_number(self):
        for personal in ("", "ABC123"):
            lines = td3(personal=personal)
            characters = list(lines[1])
            characters[42] = "<"
            characters[43] = parser.check_digit("".join(characters[0:10] + characters[13:20] + characters[21:43]))
            result = parser.read([lines[0], "".join(characters)], TODAY)
            self.assertEqual(result.mrz_valid, not personal)
        lines = td3(birth="<<<<<<")
        characters = list(lines[1])
        characters[19] = "<"
        characters[43] = parser.check_digit("".join(characters[0:10] + characters[13:20] + characters[21:43]))
        result = parser.read([lines[0], "".join(characters)], TODAY)
        self.assertFalse(result.check_digits["date_of_birth"]["valid"])

    def test_extended_td1_document_number_checks_entire_number_and_retains_optional_data(self):
        lines = td1(number="ABC123456", optional_2="MEMBER")
        full_number = "ABC123456XYZ"
        overflow = "XYZ" + parser.check_digit(full_number) + "<OTHER"
        lines[0] = lines[0][:14] + "<" + overflow.ljust(15, "<")
        lines[1] = lines[1][:29] + parser.check_digit(lines[0][5:30] + lines[1][:7] + lines[1][8:15] + lines[1][18:29])
        result = parser.read(lines, TODAY)
        self.assertTrue(result.mrz_valid, result.check_digits)
        self.assertEqual(result.document_number, full_number)
        self.assertEqual(result.optional_data, "OTHER<<<<<MEMBER")
        self.assertIn("MRZ_DOCUMENT_NUMBER_EXTENDED", result.flags)
        broken = [lines[0].replace("XYZ", "XYA"), *lines[1:]]
        self.assertFalse(parser.read(broken, TODAY).check_digits["document_number"]["valid"])

    def test_extended_td2_document_number_and_ocr_overflow_check_digit(self):
        lines = [ICAO_TD2[0].ljust(36, "<"), ICAO_TD2[1].ljust(36, "<")]
        full_number = "D23145890ABC"
        overflow = ("ABC" + parser.check_digit(full_number) + "<").ljust(7, "<")
        lines[1] = lines[1][:9] + "<" + lines[1][10:28] + overflow + "0"
        lines[1] = lines[1][:35] + parser.check_digit(lines[1][:10] + lines[1][13:20] + lines[1][21:35])
        result = parser.read(lines, TODAY)
        self.assertTrue(result.mrz_valid, result.check_digits)
        self.assertEqual(result.document_number, full_number)
        self.assertIsNone(result.optional_data)
        self.assertEqual(overflow[3], "2")
        lines[1] = lines[1][:31] + "Z" + lines[1][32:]
        result = parser.read(lines, TODAY)
        self.assertTrue(result.mrz_valid, result.check_digits)
        self.assertEqual(result.substitutions, 1)

    def test_malformed_extension_cannot_become_a_valid_truncated_number(self):
        for optional in ("", "A", "ABC123456789012"):
            lines = td1(optional_1=optional)
            lines[0] = lines[0][:14] + "<" + lines[0][15:]
            lines[1] = lines[1][:29] + parser.check_digit(lines[0][5:30] + lines[1][:7] + lines[1][8:15] + lines[1][18:29])
            result = parser.read(lines, TODAY)
            self.assertFalse(result.mrz_valid)
            self.assertIn("MRZ_DOCUMENT_NUMBER_EXTENSION_INVALID", result.flags)

    def test_document_codes_and_blank_issuing_state_are_validated(self):
        for form, lines, code in [("TD3", td3(), "V<"), ("TD1", td1(), "AI"), ("TD1", td1(), "IV"),
                                  ("TD2", [line.ljust(36, "<") for line in ICAO_TD2], "AC")]:
            lines[0] = code + lines[0][2:]
            result = parser.parse(form, tuple(lines), TODAY)
            self.assertFalse(result.mrz_valid)
            self.assertIn("MRZ_DOCUMENT_CODE_INVALID", result.flags)
        result = parser.read(td3(state="<<<"), TODAY)
        self.assertFalse(result.mrz_valid)
        self.assertIn("MRZ_ISSUING_STATE_INVALID", result.flags)
        self.assertTrue(parser.read(td1(code="AC"), TODAY).mrz_valid)

    def test_century_inference_uses_full_birth_date_and_supplied_today(self):
        result = parser.read(td3(birth="261231", expiry="750101"), TODAY)
        self.assertEqual(result.date_of_birth, date(1926, 12, 31))
        self.assertEqual(result.expiry_date, date(2075, 1, 1))
        future = parser.read(td3(birth="900315", expiry="300811"), date(2126, 10, 5))
        self.assertEqual((future.date_of_birth, future.expiry_date), (date(2090, 3, 15), date(2130, 8, 11)))

    def test_multiline_input_and_unrelated_ocr_lines_do_not_expand_parse_search(self):
        noise = [f"UNRELATED OCR CONTENT ON PAGE {index:08d}" for index in range(200)]
        with patch.object(parser, "parse", wraps=parser.parse) as parse_spy:
            result = parser.read(noise + ["\n".join(td3())], TODAY)
        self.assertTrue(result.mrz_valid)
        self.assertLessEqual(parse_spy.call_count, 3)

    def test_name_prefix_is_not_a_match_without_mrz_truncation_evidence(self):
        result = parser.read(td3(), TODAY)
        self.assertEqual(parser.compare(result, {"full_name": "SOPHEA SOK EXTRA"})["full_name"], "MISMATCH")
        self.assertEqual(parser.compare(result, {"full_name": "SOK SOPHEA"})["full_name"], "MATCH")
        surname, given = "ABCDEFGHIJKLMNOPQRSTUVWXY", "ABCDEFGHIJKL"
        truncated = parser.read(td3(surname=surname, given=given), TODAY)
        self.assertEqual(parser.compare(truncated, {"full_name": given + "ZZ " + surname})["full_name"], "MATCH")
        empty = parser.MRZResult(format="TD3", lines=tuple(td3()))
        self.assertEqual(parser.compare(empty, {"full_name": "SOPHEA SOK"})["full_name"], "VIZ_ONLY")


def page_line(text, y, confidence=0.95, notes=()):
    return OCRLine(text=text, confidence=confidence, bbox=(0.3, y, 0.9, y + 0.04), notes=notes)


def kh_passport_page(number="N01234567", dob="15 MAR 1990", mrz=None):
    rows = [("ព្រះរាជាណាចក្រកម្ពុជា KINGDOM OF CAMBODIA", None), ("លិខិតឆ្លងដែន PASSPORT", None),
            ("Passport No / លេខលិខិតឆ្លងដែន", number), ("Surname / នាមត្រកូល", "SOK"), ("Given names / នាមខ្លួន", "SOPHEA"),
            ("Nationality / សញ្ជាតិ", "CAMBODIAN"), ("Date of birth / ថ្ងៃខែឆ្នាំកំណើត", dob), ("Sex / ភេទ", "F"),
            ("Date of issue / ថ្ងៃចេញ", "11 AUG 2020"), ("Date of expiry / ថ្ងៃផុតកំណត់", "11 AUG 2030")]
    lines, y = [], 0.02
    for label, value in rows:
        lines.append(page_line(label, y))
        y += 0.035
        if value is not None:
            lines.append(page_line(value, y))
            y += 0.035
    return lines + [page_line(text, 0.85 + index * 0.06, 0.9, ("MRZ_PASS",)) for index, text in enumerate(mrz or td3())]


class PassportAdapterTests(unittest.TestCase):
    def test_cambodian_passport_reads_visual_zone_and_validates_mrz(self):
        adapter = adapter_for(DocumentType.KH_PASSPORT)
        classification = adapter.classify(kh_passport_page(), "DATA_PAGE")
        self.assertEqual((classification.document_type, classification.document_side), (DocumentType.KH_PASSPORT, "DATA_PAGE"))
        document = adapter.extract_fields({"DATA_PAGE": kh_passport_page()})
        self.assertEqual((document.document_number, document.full_name), ("N01234567", "SOPHEA SOK"))
        self.assertEqual((str(document.date_of_birth), str(document.expiry_date)), ("1990-03-15", "2030-08-11"))
        self.assertEqual((document.surname, document.given_names, document.nationality), ("SOK", "SOPHEA", "KH"))
        checks = {c.check_type: c for c in adapter.validate_fields(document, TODAY)}
        for name in ("REQUIRED_FIELDS", "MRZ", "MRZ_CONSISTENCY", "EXPIRY", "DATE_CONSISTENCY"):
            self.assertEqual(checks[name].result, CheckResult.PASS, name)
        self.assertEqual(checks["MRZ"].details["format"], "TD3")

    def test_visual_mrz_mismatch_is_the_spec_example(self):
        adapter = adapter_for(DocumentType.KH_PASSPORT)
        document = adapter.extract_fields({"DATA_PAGE": kh_passport_page(dob="21 MAR 1990")})
        check = {c.check_type: c for c in adapter.validate_fields(document, TODAY)}["MRZ_CONSISTENCY"]
        self.assertEqual(check.result, CheckResult.REVIEW)
        self.assertEqual(check.reason_codes, ("MRZ_VISUAL_DATE_OF_BIRTH_MISMATCH",))
        self.assertEqual(str(document.date_of_birth), "1990-03-21")  # never silently replaced by the MRZ value

    def test_passport_number_label_never_takes_a_title_word(self):
        adapter = adapter_for(DocumentType.KH_PASSPORT)
        document = adapter.extract_fields({"DATA_PAGE": kh_passport_page()})
        self.assertNotIn("PASSPORT", document.document_number)

    def test_foreign_mrz_for_a_cambodian_session_is_flagged(self):
        adapter = adapter_for(DocumentType.KH_PASSPORT)
        foreign = kh_passport_page(mrz=td3(state="UTO", nationality="UTO"))
        checks = {c.check_type: c for c in adapter.validate_fields(adapter.extract_fields({"DATA_PAGE": foreign}), TODAY)}
        self.assertIn("MRZ_ISSUING_STATE_UNEXPECTED", checks["MRZ"].reason_codes)

    def test_generic_adapter_takes_everything_from_a_valid_mrz(self):
        adapter = adapter_for(DocumentType.PASSPORT)
        lines = [page_line("UTOPIA PASSPORT", 0.05)] + [page_line(text, 0.85 + i * 0.06, 0.0, ("MRZ_PASS",))
                                                          for i, text in enumerate(ICAO_TD3)]
        self.assertEqual(adapter.classify(lines, "DATA_PAGE").document_type, DocumentType.PASSPORT)
        document = adapter.extract_fields({"DATA_PAGE": lines})
        self.assertEqual((document.document_number, document.full_name, document.nationality), ("L898902C3", "ANNA MARIA ERIKSSON", "UTO"))
        # No printed labels were read, so every value present came from the MRZ.
        self.assertTrue(all(item.source == "MRZ" for item in document.fields if item.field != "mrz" and item.normalized_value))
        checks = {c.check_type: c for c in adapter.validate_fields(document, TODAY)}
        self.assertEqual(checks["EXPIRY"].result, CheckResult.FAIL)  # the ICAO specimen expired in 2012
        self.assertEqual(checks["MRZ"].result, CheckResult.PASS)
        self.assertEqual(checks["OCR_CONFIDENCE"].result, CheckResult.PASS)  # valid check digits outweigh a 0 OCR score

    def test_generic_adapter_rejects_invalid_or_non_passport_mrz(self):
        adapter = adapter_for(DocumentType.PASSPORT)
        broken = [page_line(ICAO_TD3[0], 0.85, notes=("MRZ_PASS",)), page_line(ICAO_TD3[1].replace("7408122", "7408129"), 0.9, notes=("MRZ_PASS",))]
        document = adapter.extract_fields({"DATA_PAGE": broken})
        self.assertIsNone(document.full_name)  # names need a fully valid MRZ
        required = {c.check_type: c for c in adapter.validate_fields(document, TODAY)}["REQUIRED_FIELDS"]
        self.assertEqual(required.result, CheckResult.FAIL)
        id_card = [page_line(text, 0.8 + i * 0.05, notes=("MRZ_PASS",)) for i, text in enumerate(ICAO_TD1)]
        self.assertEqual(adapter.classify(id_card, "DATA_PAGE").document_type, DocumentType.NATIONAL_ID)
