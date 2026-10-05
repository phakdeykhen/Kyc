"""Phase 6: international passports and generic ID / residence cards (fictional ICAO Utopia holder)."""

from datetime import date
import unittest

from kyc.api.schemas import COUNTRY_CODES
from kyc.documents.adapters import adapter_for
from kyc.documents.iso3166 import ALPHA3_TO_ALPHA2, to_alpha2
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import OCRLine
from kyc.services.documents import DocumentProcessor
from tests.mrz_build import td1, td3

TODAY = date(2026, 10, 5)
UTO_TD3 = td3(state="UTO", number="L898902C3", nationality="UTO", birth="740812", sex="F", expiry="340415",
              surname="ERIKSSON", given="ANNA MARIA")
UTO_TD1 = td1(code="I", state="UTO", number="D23145890", birth="740812", sex="F", expiry="340415", nationality="UTO",
              surname="ERIKSSON", given="ANNA MARIA")


def row(text, y, confidence=0.95, notes=()):
    return OCRLine(text=text, confidence=confidence, bbox=(0.3, y, 0.95, y + 0.03), notes=notes)


def mrz_rows(lines, top=0.84):
    return [row(text, top + index * 0.05, 0.0, ("MRZ_PASS",)) for index, text in enumerate(lines)]


def passport_page(**changes):
    values = {"Passport No / No. du passeport": "L898902C3", "Surname / Nom": "ERIKSSON",
              "Given names / Prénoms": "ANNA MARIA", "Nationality / Nationalité": "UTOPIAN",
              "Date of birth / Date de naissance": "12 AUG 1974", "Sex / Sexe": "F/F",
              "Date of expiry / Date d'expiration": "15 APR 2034"} | changes
    lines, y = [row("UTOPIA PASSPORT / PASSEPORT", 0.01)], 0.05
    for label, value in values.items():
        lines += [row(label, y), row(value, y + 0.03)]
        y += 0.075
    return lines + mrz_rows(UTO_TD3)


def id_front(**changes):
    values = {"Surname": "ERIKSSON", "Given names": "ANNA MARIA", "Document No": "D23145890",
              "Date of birth": "12.08.1974", "Sex": "F", "Nationality": "UTO", "Date of expiry": "15.04.2034"} | changes
    lines, y = [row("UTOPIA IDENTITY CARD", 0.02)], 0.1
    for label, value in values.items():
        lines += [row(label, y), row(value, y + 0.04)]
        y += 0.1
    return lines


class CountryCodeTests(unittest.TestCase):
    def test_alpha3_table_covers_every_supported_country_once(self):
        self.assertEqual(set(ALPHA3_TO_ALPHA2.values()), set(COUNTRY_CODES))
        self.assertEqual(len(ALPHA3_TO_ALPHA2), len(COUNTRY_CODES))

    def test_icao_specific_issuer_codes(self):
        self.assertEqual((to_alpha2("KHM"), to_alpha2("D<<"), to_alpha2("GBD")), ("KH", "DE", "GB"))
        self.assertIsNone(to_alpha2("UNO"))  # United Nations: valid issuer, not a country
        self.assertIsNone(to_alpha2("UTO"))  # ICAO's fictional specimen state


class GenericPassportTests(unittest.TestCase):
    adapter = adapter_for(DocumentType.PASSPORT)

    def test_visual_zone_and_mrz_agree_for_a_foreign_passport(self):
        page = passport_page()
        self.assertEqual(self.adapter.classify(page, "DATA_PAGE").document_side, "DATA_PAGE")
        document = self.adapter.extract_fields({"DATA_PAGE": page})
        fields = {item.field: item for item in document.fields}
        self.assertEqual((document.full_name, document.document_number), ("ANNA MARIA ERIKSSON", "L898902C3"))
        self.assertEqual(fields["full_name"].source, "OCR")  # read from the printed page, not copied from the MRZ
        self.assertEqual((document.sex, document.nationality), ("F", "UTO"))
        checks = {c.check_type: c for c in self.adapter.validate_fields(document, TODAY)}
        consistency = checks["MRZ_CONSISTENCY"]
        self.assertEqual(consistency.result, CheckResult.PASS)
        self.assertEqual(consistency.details["field_consistency"]["full_name"], "MATCH")
        self.assertEqual(consistency.details["field_consistency"]["nationality"], "NOT_COMPARED")  # "UTOPIAN" vs a code

    def test_printed_disagreement_with_the_mrz_is_flagged(self):
        document = self.adapter.extract_fields({"DATA_PAGE": passport_page(**{"Date of birth / Date de naissance": "21 AUG 1974"})})
        check = {c.check_type: c for c in self.adapter.validate_fields(document, TODAY)}["MRZ_CONSISTENCY"]
        self.assertEqual(check.reason_codes, ("MRZ_VISUAL_DATE_OF_BIRTH_MISMATCH",))

    def test_unfamiliar_label_language_falls_back_to_the_mrz(self):
        page = [row("ПАСПОРТ", 0.02), row("Фамилия", 0.1), row("ERIKSSON", 0.13)] + mrz_rows(UTO_TD3)
        document = self.adapter.extract_fields({"DATA_PAGE": page})
        self.assertEqual(document.full_name, "ANNA MARIA ERIKSSON")
        self.assertEqual({item.field: item.source for item in document.fields}["full_name"], "MRZ")


class GenericIDTests(unittest.TestCase):
    adapter = adapter_for(DocumentType.NATIONAL_ID)

    def test_front_labels_and_back_td1_mrz(self):
        back = mrz_rows(UTO_TD1, 0.6)
        self.assertEqual(self.adapter.classify(id_front(), "FRONT").document_side, "FRONT")
        self.assertEqual(self.adapter.classify(back, "BACK").document_side, "BACK")
        document = self.adapter.extract_fields({"FRONT": id_front(), "BACK": back})
        self.assertEqual((document.document_number, document.full_name, str(document.date_of_birth)),
                         ("D23145890", "ANNA MARIA ERIKSSON", "1974-08-12"))
        checks = {c.check_type: c for c in self.adapter.validate_fields(document, TODAY)}
        self.assertEqual((checks["MRZ"].result, checks["MRZ_CONSISTENCY"].result), (CheckResult.PASS, CheckResult.PASS))
        self.assertEqual(checks["MRZ_CONSISTENCY"].details["field_consistency"]["nationality"], "MATCH")

    def test_unreadable_front_is_carried_by_a_valid_back_mrz(self):
        front = [row("ⴰⵙⵜⴰⵢ", 0.2)]  # a script the generic adapter does not read
        self.assertEqual(self.adapter.classify(front, "FRONT").confidence, 0.4)  # REVIEW, not PASS
        document = self.adapter.extract_fields({"FRONT": front, "BACK": mrz_rows(UTO_TD1, 0.6)})
        self.assertEqual(document.full_name, "ANNA MARIA ERIKSSON")
        required = {c.check_type: c for c in self.adapter.validate_fields(document, TODAY)}["REQUIRED_FIELDS"]
        self.assertEqual(required.result, CheckResult.PASS)

    def test_passport_mrz_or_nothing_readable_is_not_an_id_card(self):
        self.assertEqual(self.adapter.classify(mrz_rows(UTO_TD3), "BACK").document_type, DocumentType.PASSPORT)
        self.assertEqual(self.adapter.classify([row("hello", 0.5)], "BACK").confidence, 0.0)

    def test_residence_card_shares_the_engine_with_its_own_type(self):
        adapter = adapter_for(DocumentType.RESIDENCE_CARD)
        self.assertEqual(adapter.classify(mrz_rows(UTO_TD1), "BACK").document_type, DocumentType.RESIDENCE_CARD)
        self.assertEqual(adapter.version, "GENERIC-RESIDENCE-ADAPTER-2026.10.1")


class IssuingCountryTests(unittest.TestCase):
    def check(self, mrz_lines, session_country):
        document = adapter_for(DocumentType.PASSPORT).extract_fields({"DATA_PAGE": mrz_rows(mrz_lines)})
        return DocumentProcessor._issuing_country_check(document, session_country)

    def test_match_mismatch_and_non_state_issuer(self):
        thai = td3(state="THA", nationality="THA", number="AA1234567", birth="900101", expiry="320101", surname="SRI", given="SOMCHAI")
        self.assertEqual(self.check(thai, "TH").result, CheckResult.PASS)
        mismatch = self.check(thai, "VN")
        self.assertEqual((mismatch.result, mismatch.reason_codes), (CheckResult.REVIEW, ("ISSUING_STATE_DIFFERS_FROM_SESSION_COUNTRY",)))
        self.assertEqual(mismatch.details["issuing_country"], "TH")
        laissez_passer = td3(state="UNO", nationality="UTO", number="UN1234567", birth="900101", expiry="320101", surname="DOE", given="J")
        self.assertEqual(self.check(laissez_passer, "TH").result, CheckResult.NOT_APPLICABLE)
        self.assertEqual(self.check(UTO_TD3, "TH").reason_codes, ("ISSUING_STATE_UNRECOGNIZED",))
