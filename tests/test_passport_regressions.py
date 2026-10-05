"""Passport trust boundaries and evidence provenance, using fictional holders."""

from datetime import date
import json
import unittest

from kyc.documents.adapters.generic_mrz import GenericMRZAdapter
from kyc.documents.adapters.kh_passport import CambodiaPassportAdapter
from kyc.documents.adapters.kh_nssf import CambodiaNSSFAdapter
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import OCRLine
from tests.mrz_build import td1, td3

TODAY = date(2026, 10, 5)


def row(text, y, notes=()):
    return OCRLine(text=text, confidence=0.95, bbox=(0.3, y, 0.95, y + 0.03), notes=notes)


def mrz_rows(lines):
    return [row(text, 0.84 + index * 0.05, ("MRZ_PASS",)) for index, text in enumerate(lines)]


def visual_rows(**changes):
    values = {"Passport No": "N01234567", "Surname": "SOK", "Given names": "SOPHEA",
              "Nationality": "CAMBODIAN", "Date of birth": "15 MAR 1990", "Sex": "F",
              "Place of birth": "PHNOM PENH", "Date of issue": "11 AUG 2020",
              "Date of expiry": "11 AUG 2030"} | changes
    return [row("KINGDOM OF CAMBODIA PASSPORT", 0.01)] + [
        row(label + ": " + value, 0.06 + index * 0.06)
        for index, (label, value) in enumerate(values.items()) if value is not None]


def checks(adapter, document):
    return {item.check_type: item for item in adapter.validate_fields(document, TODAY)}


class PassportTrustRegressions(unittest.TestCase):
    def test_passport_fields_point_to_the_data_page_and_name_covers_both_parts(self):
        adapter = CambodiaPassportAdapter()
        document = adapter.extract_fields({"DATA_PAGE": visual_rows() + mrz_rows(td3())})
        fields = {item.field: item for item in document.fields}
        self.assertTrue(all(item.side == "DATA_PAGE" for item in fields.values() if item.source == "OCR"))
        self.assertEqual(fields["full_name"].bbox, (0.3, 0.12, 0.95, 0.21))
        self.assertEqual(fields["mrz"].bbox, (0.3, 0.84, 0.95, 0.92))

    def test_raw_mrz_keeps_original_ocr_while_normalized_mrz_is_assembled(self):
        lines = td3()
        original = [line[:12] + " " + line[12:] for line in lines]
        document = GenericMRZAdapter().extract_fields({"DATA_PAGE": mrz_rows(original)})
        field = next(item for item in document.fields if item.field == "mrz")
        self.assertEqual(field.raw_value, "\n".join(original))
        self.assertEqual(field.normalized_value, "\n".join(lines))
        self.assertEqual(document.mrz, field.normalized_value)

    def test_english_label_case_does_not_change_the_extraction(self):
        adapter = CambodiaPassportAdapter()
        lines = [item.model_copy(update={"text": item.text.upper()}) for item in visual_rows()]
        document = adapter.extract_fields({"DATA_PAGE": lines})
        self.assertEqual((document.document_number, document.full_name), ("N01234567", "SOPHEA SOK"))
        self.assertEqual((document.date_of_birth, document.expiry_date), (date(1990, 3, 15), date(2030, 8, 11)))

    def test_adapter_reuse_does_not_compare_another_sessions_mrz(self):
        adapter = CambodiaPassportAdapter()
        first = adapter.extract_fields({"DATA_PAGE": visual_rows() + mrz_rows(td3())})
        second = adapter.extract_fields({"DATA_PAGE": visual_rows(**{"Passport No": "N07654321"})
                                         + mrz_rows(td3(number="N07654321"))})
        self.assertEqual(checks(adapter, first)["MRZ_CONSISTENCY"].result, CheckResult.PASS)
        self.assertEqual(checks(adapter, second)["MRZ_CONSISTENCY"].result, CheckResult.PASS)
        self.assertEqual(checks(CambodiaPassportAdapter(), first)["MRZ_CONSISTENCY"].result, CheckResult.PASS)

    def test_wrong_document_layout_cannot_supply_canonical_fallback_fields(self):
        for adapter, lines, code in [
            (CambodiaPassportAdapter(), td3(state="UTO", nationality="UTO"), "MRZ_ISSUING_STATE_UNEXPECTED"),
            (CambodiaPassportAdapter(), td1(), "MRZ_FORMAT_UNEXPECTED"),
            (GenericMRZAdapter(), td1(), "MRZ_DOCUMENT_CODE_UNEXPECTED"),
        ]:
            with self.subTest(adapter=type(adapter).__name__, code=code):
                document = adapter.extract_fields({"DATA_PAGE": mrz_rows(lines)})
                self.assertIsNone(document.document_number)
                self.assertIsNone(document.full_name)
                self.assertIsNone(document.date_of_birth)
                self.assertIn(code, checks(adapter, document)["MRZ"].reason_codes)

    def test_fallback_keeps_the_unreadable_visual_evidence_and_mrz_source_box(self):
        adapter = CambodiaPassportAdapter()
        document = adapter.extract_fields({"DATA_PAGE": visual_rows(**{"Passport No": "unreadable"}) + mrz_rows(td3())})
        fields = {item.field: item for item in document.fields}
        self.assertEqual((document.document_number, fields["document_number"].source), ("N01234567", "MRZ"))
        self.assertEqual(fields["document_number"].bbox, (0.3, 0.89, 0.95, 0.92))
        self.assertEqual((fields["document_number_visual"].raw_value, fields["document_number_visual"].source),
                         ("unreadable", "OCR"))
        self.assertEqual(fields["document_number_visual"].side, "DATA_PAGE")

    def test_missing_given_names_are_filled_before_composing_the_full_name(self):
        adapter = CambodiaPassportAdapter()
        document = adapter.extract_fields({"DATA_PAGE": visual_rows(**{"Given names": None}) + mrz_rows(td3())})
        self.assertEqual((document.given_names, document.surname, document.full_name), ("SOPHEA", "SOK", "SOPHEA SOK"))
        fields = {item.field: item for item in document.fields}
        self.assertEqual(fields["surname"].source, "OCR")
        self.assertEqual(fields["given_names"].source, "MRZ")
        self.assertEqual(fields["full_name"].source, "DERIVED")
        consistency = checks(adapter, document)["MRZ_CONSISTENCY"].details["field_consistency"]
        self.assertEqual(consistency["full_name"], "MRZ_ONLY")
        self.assertEqual(consistency["surname"], "MATCH")

    def test_a_visual_component_conflict_survives_partial_name_fallback(self):
        adapter = CambodiaPassportAdapter()
        document = adapter.extract_fields({"DATA_PAGE": visual_rows(**{"Given names": None, "Surname": "CHAN"}) + mrz_rows(td3())})
        self.assertEqual(document.full_name, "SOPHEA CHAN")
        check = checks(adapter, document)["MRZ_CONSISTENCY"]
        self.assertEqual(check.details["field_consistency"]["full_name"], "MRZ_ONLY")
        self.assertEqual(check.details["field_consistency"]["surname"], "MISMATCH")
        self.assertIn("MRZ_VISUAL_SURNAME_MISMATCH", check.reason_codes)

    def test_printed_nationality_disagreement_is_not_hidden_by_a_derived_country(self):
        adapter = CambodiaPassportAdapter()
        for nationality, expected in [("CAMBODIAN / ខ្មែរ", CheckResult.PASS), ("AMERICAN", CheckResult.REVIEW),
                                       ("CAMBODIAN / AMERICAN", CheckResult.REVIEW)]:
            with self.subTest(nationality=nationality):
                document = adapter.extract_fields({"DATA_PAGE": visual_rows(**{"Nationality": nationality}) + mrz_rows(td3())})
                check = checks(adapter, document)["MRZ_CONSISTENCY"]
                self.assertEqual(check.result, expected)
                if expected == CheckResult.REVIEW:
                    self.assertIn("MRZ_VISUAL_NATIONALITY_MISMATCH", check.reason_codes)

    def test_unprotected_fields_and_missing_visual_comparison_are_reported_truthfully(self):
        adapter = GenericMRZAdapter()
        document = adapter.extract_fields({"DATA_PAGE": mrz_rows(td3())})
        fields = {item.field: item for item in document.fields}
        self.assertIn("CHECK_DIGIT_VALID", fields["document_number"].flags)
        for name in ("full_name", "surname", "given_names", "nationality", "sex", "issuing_state"):
            self.assertIn("NOT_CHECK_DIGIT_PROTECTED", fields[name].flags)
            self.assertNotIn("CHECK_DIGIT_VALID", fields[name].flags)
            self.assertIsNotNone(fields[name].bbox)
        self.assertEqual(checks(adapter, document)["MRZ_CONSISTENCY"].result, CheckResult.NOT_APPLICABLE)

    def test_valid_khmer_mrz_alone_identifies_a_data_page_and_foreign_mrz_is_a_different_type(self):
        adapter = CambodiaPassportAdapter()
        khmer = adapter.classify(mrz_rows(td3()), "DATA_PAGE")
        self.assertEqual((khmer.document_type, khmer.document_side), (DocumentType.KH_PASSPORT, "DATA_PAGE"))
        self.assertGreaterEqual(khmer.confidence, adapter.policy.accept_classification)
        foreign = adapter.classify(mrz_rows(td3(state="UTO", nationality="UTO")), "DATA_PAGE")
        self.assertEqual(foreign.document_type, DocumentType.PASSPORT)

    def test_good_visual_pass_can_rescue_a_failed_dedicated_mrz_pass(self):
        adapter = GenericMRZAdapter()
        lines = td3()
        failed = mrz_rows([lines[0], lines[1].replace("900315", "900316")])
        visual = [row(text, 0.75 + index * 0.05) for index, text in enumerate(lines)]
        classification = adapter.classify(failed + visual, "DATA_PAGE")
        self.assertGreaterEqual(classification.confidence, adapter.policy.accept_classification)
        document = adapter.extract_fields({"DATA_PAGE": failed + visual})
        self.assertEqual(checks(adapter, document)["MRZ"].result, CheckResult.PASS)

    def test_check_evidence_has_no_raw_identity_values(self):
        adapter = CambodiaPassportAdapter()
        document = adapter.extract_fields({"DATA_PAGE": visual_rows() + mrz_rows(td3())})
        evidence = json.dumps([{"reasons": item.reason_codes, "details": item.details}
                               for item in adapter.validate_fields(document, TODAY)])
        for value in (document.document_number, document.full_name, "1990-03-15", "2030-08-11", "P<KHMSOK", "PHNOM PENH"):
            self.assertNotIn(value, evidence)

    def test_checksum_success_does_not_hide_invalid_mrz_data(self):
        adapter = GenericMRZAdapter()
        document = adapter.extract_fields({"DATA_PAGE": mrz_rows(td3(birth="901332"))})
        check = checks(adapter, document)["MRZ"]
        self.assertTrue(all(item["valid"] for item in check.details["check_digit_results"].values()))
        self.assertFalse(check.details["data_valid"])
        self.assertFalse(check.details["mrz_valid"])
        self.assertIn("MRZ_DATA_INVALID", check.reason_codes)
        self.assertNotIn("MRZ_CHECK_DIGIT_FAILED", check.reason_codes)
        self.assertIsNone(document.document_number)
        self.assertIsNone(document.full_name)

    def test_a_card_without_mrz_cannot_take_identity_fields_from_another_cards_mrz(self):
        adapter = CambodiaNSSFAdapter()
        document = adapter.extract_fields({"FRONT": [], "BACK": mrz_rows(td1())})
        self.assertIsNone(document.document_number)
        self.assertIsNone(document.full_name)
        self.assertIn("MRZ_UNEXPECTED_ON_DOCUMENT", checks(adapter, document)["MRZ"].reason_codes)


if __name__ == "__main__":
    unittest.main()
