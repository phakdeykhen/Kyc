import base64
import json
import os
import unittest
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import AuditLog, BarcodeResult, DocumentCheck, DocumentField, DocumentImage, IdentityDocument, KYCSession, MRZResult
from kyc.ocr.tesseract import OCRUnavailable, TesseractOCREngine
from kyc.services.documents import pending_sessions
from tests import images
from tests.helpers import call
from tests.test_captures_api import GOOD, GOOD_BACK, PASSPORT, CaptureAPICase
from tests.test_capture_store import keyring
from tests.test_kh_national_id import BACK, CARD_BACK, NSSF_BACK, front_lines, line, nssf_front
from tests.test_mrz import ICAO_TD3, kh_passport_page, page_line
from tests.mrz_build import td3

PII_SETTINGS = {"pii_encryption_keys": keyring("pii-test-v1"), "pii_hmac_key": base64.b64encode(os.urandom(32)).decode()}
PII_VALUES = ("SOPHEA", "010203040", "សុខ", "ភ្នំពេញ", "1990-03-15")


class ScriptedOCR:
    """Returns prepared OCR pages in upload order; lets service tests run without Tesseract."""

    engine_version = "scripted-ocr-test"

    def __init__(self, *pages, error=None):
        self.pages = list(pages)
        self.error = error

    def is_available(self, languages):
        return True

    def available_languages(self):
        return {"khm", "eng"}  # no digit model, so numeric refinement is skipped

    def read_lines(self, image, languages):
        if self.error:
            raise self.error
        return self.pages.pop(0)


class DocumentProcessingTests(CaptureAPICase):
    extra_settings = PII_SETTINGS

    def use_ocr(self, *pages, error=None):
        self.app.state.document_processor.ocr = ScriptedOCR(*pages, error=error)

    async def capture_both(self, document_type="KH_NATIONAL_ID", level="DOCUMENT_FACE_LIVENESS", front=GOOD, back=GOOD_BACK):
        session_id = await self.create(document_type, level)
        await self.upload(session_id, front, "front")
        code, body, _ = await self.upload(session_id, back, "back")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "DOCUMENT_PROCESSING")  # response is sent before processing runs
        return session_id

    async def session(self, session_id):
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        return body

    async def result(self, session_id):
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, body)
        return body

    async def test_valid_card_is_extracted_and_session_advances_to_selfie(self):
        self.use_ocr(front_lines() + BACK, CARD_BACK)
        session_id = await self.capture_both()
        self.assertEqual((await self.session(session_id))["status"], "SELFIE_REQUIRED")
        result = await self.result(session_id)
        self.assertEqual(result["document"], {"country": "KH", "type": "KH_NATIONAL_ID",
                                              "document_number_masked": "*****3040", "expiry_status": "VALID"})
        self.assertEqual(result["identity"], {"full_name": "SOK SOPHEA", "full_name_local": "សុខ សុភា",
                                              "date_of_birth": "1990-03-15", "sex": "F", "nationality": "KH"})
        self.assertEqual(result["checks"], {"document_quality": "PASS", "document_classification": "PASS",
                                            "document_data": "PASS", "expiry": "PASS", "mrz": "PASS",
                                            "mrz_consistency": "PASS", "barcode": "NOT_APPLICABLE",
                                            "issuing_country": "PASS",
                                            "document_portrait": "UNAVAILABLE"})
        self.assertEqual(result["review_flags"], [])
        self.assertIsNone(result["decision"])  # extraction never decides

    async def test_document_only_level_goes_straight_to_processing(self):
        self.use_ocr(front_lines() + BACK, CARD_BACK)
        session_id = await self.capture_both(level="DOCUMENT_ONLY")
        # PROCESSING, then the Phase 13 assessment: consistent data alone is not proof the card is genuine.
        self.assertEqual((await self.session(session_id))["status"], "MANUAL_REVIEW")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["decision"]["reason_codes"], ["DOCUMENT_AUTHENTICITY_UNVERIFIED"])

    async def test_fields_are_encrypted_bound_and_absent_from_audit_and_checks(self):
        self.use_ocr(front_lines() + BACK, CARD_BACK)
        session_id = await self.capture_both()
        cipher = self.app.state.field_cipher
        with Session(self.engine) as db:
            fields = db.scalars(sa.select(DocumentField)).all()
            document = db.scalar(sa.select(IdentityDocument))
            audits = db.scalars(sa.select(AuditLog)).all()
            checks = db.scalars(sa.select(DocumentCheck)).all()
        self.assertGreaterEqual(len(fields), 10)
        stored = b"".join((item.raw_value_ciphertext or b"") + (item.normalized_value_ciphertext or b"") for item in fields)
        for value in PII_VALUES:
            self.assertNotIn(value.encode(), stored)
        name = next(item for item in fields if item.field_name == "full_name")
        context = f"field/{self.org}/{session_id}/{document.id}/full_name/normalized"
        self.assertEqual(cipher.open(name.normalized_value_ciphertext, name.key_version, context), "SOK SOPHEA")
        with self.assertRaises(InvalidTag):
            cipher.open(name.normalized_value_ciphertext, name.key_version, context.replace("full_name", "address"))
        self.assertEqual(len(document.document_number_hmac), 64)
        self.assertEqual(document.document_number_hmac, cipher.lookup_hash("010203040", "KH_NATIONAL_ID"))
        self.assertIn("KH-NID-ADAPTER", document.extraction_version)
        logged = json.dumps([[a.reason_codes, a.event_metadata] for a in audits] + [c.evidence_metadata for c in checks],
                            ensure_ascii=False)
        for value in PII_VALUES:
            self.assertNotIn(value, logged)
        self.assertIn("DOCUMENT_EXTRACTED", [a.action for a in audits])

    async def test_expired_card_is_accepted_as_evidence_and_flagged(self):
        self.use_ocr(front_lines(validity="០១.០១.២០១០ ដល់ថ្ងៃ ៣១.១២.២០១៩") + BACK, CARD_BACK)
        session_id = await self.capture_both()
        result = await self.result(session_id)
        self.assertEqual(result["document"]["expiry_status"], "EXPIRED")
        self.assertEqual(result["checks"]["expiry"], "FAIL")
        self.assertIn("EXPIRED_DOCUMENT", result["review_flags"])
        self.assertIsNone(result["decision"])  # the risk engine (Phase 13) decides, not the adapter

    async def test_wrong_document_type_requests_recapture_and_clears_captures(self):
        passport = [line("KINGDOM OF CAMBODIA PASSPORT", 0.1)]
        self.use_ocr(passport, CARD_BACK)
        session_id = await self.capture_both()
        self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_REQUIRED")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentImage)), 0)
            check = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "DOCUMENT_PROCESSING"))
            self.assertEqual(check.evidence_metadata["reason_codes"], ["DOCUMENT_TYPE_MISMATCH"])
            self.assertIn("DOCUMENT_RECAPTURE_REQUESTED", db.scalars(sa.select(AuditLog.action)).all())
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(body["sides"], {"FRONT": "ACCEPTED", "BACK": "REQUIRED"})  # old back no longer counts

    async def test_recapture_reasons(self):
        cases = [
            ((front_lines(number="១២៣៤"), []), "CRITICAL_FIELD_UNREADABLE"),  # no MRZ to fall back on
            (([line("hello", 0.1)], CARD_BACK), "DOCUMENT_NOT_RECOGNIZED"),
            ((front_lines(), front_lines()), "BACK_SIDE_EXPECTED"),
        ]
        for pages, reason in cases:
            with self.subTest(reason=reason):
                self.use_ocr(*pages)
                session_id = await self.capture_both()
                self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_REQUIRED")
                with Session(self.engine) as db:
                    codes = [c.evidence_metadata["reason_codes"] for c in db.scalars(sa.select(DocumentCheck).where(
                        DocumentCheck.session_id == UUID(session_id), DocumentCheck.check_type == "DOCUMENT_PROCESSING"))]
                self.assertEqual(codes, [[reason]])

    async def test_sides_uploaded_the_wrong_way_round_are_handled(self):
        self.use_ocr(CARD_BACK, front_lines() + BACK)
        session_id = await self.capture_both()
        self.assertEqual((await self.session(session_id))["status"], "SELFIE_REQUIRED")
        with Session(self.engine) as db:
            check = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "CLASSIFICATION"))
        self.assertEqual(check.evidence_metadata["notes"], ["SIDES_SWAPPED"])

    async def test_document_type_without_an_adapter_waits_honestly(self):
        self.use_ocr()
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", {"user_id": "u", "country": "TH",
                                   "expected_document_type": "DRIVING_LICENSE"}, self.headers)
        session_id = body["session_id"]
        await self.upload(session_id, GOOD, "front")
        await self.upload(session_id, GOOD_BACK, "back")
        self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_PROCESSING")
        outcome = self.app.state.document_processor.process(self.org, UUID(session_id), uuid4())
        self.assertEqual(outcome.status, "NO_ADAPTER")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(AuditLog)
                                       .where(AuditLog.action == "DOCUMENT_ADAPTER_UNAVAILABLE")), 1)

    async def test_ocr_failure_leaves_session_for_retry_and_the_worker_completes_it(self):
        self.use_ocr(error=OCRUnavailable("OCR timed out."))
        session_id = await self.capture_both()
        self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_PROCESSING")
        with Session(self.engine) as db:
            self.assertIn("DOCUMENT_PROCESSING_FAILED", db.scalars(sa.select(AuditLog.action)).all())
        factory = self.app.state.session_factory
        self.assertEqual(pending_sessions(factory, self.org), [UUID(session_id)])
        self.use_ocr(front_lines() + BACK, CARD_BACK)
        outcome = self.app.state.document_processor.process(self.org, UUID(session_id), uuid4())
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(pending_sessions(factory, self.org), [])

    async def test_processor_cannot_reach_another_tenants_session(self):
        session_id = await self.create()
        outcome = self.app.state.document_processor.process(self.other_org, UUID(session_id), uuid4())
        self.assertEqual(outcome.status, "NOT_FOUND")


class NSSFProcessingTests(DocumentProcessingTests):
    """Runs the inherited Phase 3 tests too, proving the shared engine change kept them green."""

    async def test_nssf_card_is_extracted_without_expiry_or_mrz(self):
        self.use_ocr(nssf_front(), NSSF_BACK)
        session_id = await self.capture_both("KH_NSSF")
        self.assertEqual((await self.session(session_id))["status"], "SELFIE_REQUIRED")
        result = await self.result(session_id)
        self.assertEqual(result["document"], {"country": "KH", "type": "KH_NSSF",
                                              "document_number_masked": "******5678", "expiry_status": "NOT_APPLICABLE"})
        self.assertEqual(result["identity"]["full_name"], "CHAN DARA")
        self.assertEqual(result["identity"]["full_name_local"], "ចាន់ ដារ៉ា")
        self.assertEqual(result["checks"]["expiry"], "NOT_APPLICABLE")
        self.assertEqual(result["checks"]["mrz"], "NOT_APPLICABLE")
        self.assertEqual(result["checks"]["document_data"], "PASS")
        self.assertEqual(result["checks"]["document_classification"], "PASS")
        with Session(self.engine) as db:
            names = set(db.scalars(sa.select(DocumentField.field_name)))
            document = db.scalar(sa.select(IdentityDocument))
        self.assertTrue({"national_id_number", "employer"} <= names)
        self.assertEqual(document.document_number_hmac, self.app.state.field_cipher.lookup_hash("0012345678", "KH_NSSF"))

    async def test_wrong_khmer_card_for_the_claimed_type_is_sent_back(self):
        for claimed, pages in [("KH_NSSF", (front_lines() + BACK, CARD_BACK)), ("KH_NATIONAL_ID", (nssf_front(), NSSF_BACK))]:
            with self.subTest(claimed=claimed):
                self.use_ocr(*pages)
                session_id = await self.capture_both(claimed)
                self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_REQUIRED")
                with Session(self.engine) as db:
                    check = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.session_id == UUID(session_id),
                                                                     DocumentCheck.check_type == "DOCUMENT_PROCESSING"))
                self.assertEqual(check.evidence_metadata["reason_codes"], ["DOCUMENT_TYPE_MISMATCH"])


PASSPORT_PHOTO = images.encode(images.passport_page())


class PassportProcessingTests(DocumentProcessingTests):
    async def capture_page(self, document_type, level="DOCUMENT_FACE_LIVENESS"):
        session_id = await self.create(document_type, level) if document_type.startswith("KH_") else await self._create_foreign(level)
        code, body, _ = await self.upload(session_id, PASSPORT_PHOTO, None, side="DATA_PAGE")
        self.assertEqual(body["status"], "DOCUMENT_PROCESSING", body)
        return session_id

    async def _create_foreign(self, level):
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", {"user_id": "traveller", "country": "TH",
                                   "expected_document_type": "PASSPORT", "verification_level": level}, self.headers)
        return body["session_id"]

    async def test_cambodian_passport_is_extracted_and_mrz_result_stored(self):
        self.use_ocr(kh_passport_page())
        session_id = await self.capture_page("KH_PASSPORT")
        self.assertEqual((await self.session(session_id))["status"], "SELFIE_REQUIRED")
        result = await self.result(session_id)
        self.assertEqual(result["document"]["type"], "KH_PASSPORT")
        self.assertEqual(result["document"]["document_number_masked"], "*****4567")
        self.assertEqual(result["identity"]["full_name"], "SOPHEA SOK")
        self.assertEqual((result["checks"]["mrz"], result["checks"]["mrz_consistency"]), ("PASS", "PASS"))
        with Session(self.engine) as db:
            stored = db.scalar(sa.select(MRZResult))
        self.assertEqual((stored.format, stored.mrz_valid), ("TD3", True))
        self.assertTrue(all(item["valid"] for item in stored.check_digit_results.values()))
        self.assertEqual(stored.field_consistency["document_number"], "MATCH")
        self.assertEqual(result["mrz"], {"format": stored.format, "mrz_valid": True,
                                         "check_digit_results": stored.check_digit_results,
                                         "field_consistency": stored.field_consistency})
        self.assertNotIn("N01234567", json.dumps(stored.check_digit_results) + json.dumps(stored.field_consistency))

    async def test_visual_mrz_disagreement_reaches_the_result_as_a_review_flag(self):
        self.use_ocr(kh_passport_page(number="NO1234567"))
        session_id = await self.capture_page("KH_PASSPORT")
        result = await self.result(session_id)
        self.assertEqual(result["checks"]["mrz_consistency"], "REVIEW")
        self.assertIn("MRZ_VISUAL_DOCUMENT_NUMBER_MISMATCH", result["review_flags"])
        self.assertIsNone(result["decision"])

    async def test_raw_mrz_is_encrypted_and_evidence_contains_no_identity_values(self):
        self.use_ocr(kh_passport_page())
        session_id = await self.capture_page("KH_PASSPORT")
        result = await self.result(session_id)
        with Session(self.engine) as db:
            document = db.scalar(sa.select(IdentityDocument))
            fields = db.scalars(sa.select(DocumentField)).all()
            mrz = next(item for item in fields if item.field_name == "mrz")
            checks = db.scalars(sa.select(DocumentCheck)).all()
            audits = db.scalars(sa.select(AuditLog)).all()
            stored = db.scalar(sa.select(MRZResult))
        context = f"field/{self.org}/{session_id}/{document.id}/mrz/normalized"
        self.assertEqual(self.app.state.field_cipher.open(mrz.normalized_value_ciphertext, mrz.key_version, context),
                         "\n".join(td3()))
        self.assertEqual(mrz.side, "DATA_PAGE")
        self.assertTrue(all(item.side in (None, "DATA_PAGE") for item in fields))
        ciphertext = b"".join((item.raw_value_ciphertext or b"") + (item.normalized_value_ciphertext or b"")
                              for item in fields)
        metadata = json.dumps([c.evidence_metadata for c in checks] +
                              [[a.event_metadata, a.reason_codes] for a in audits] +
                              [stored.check_digit_results, stored.field_consistency])
        for value in ("N01234567", "SOPHEA", "1990-03-15", *td3()):
            self.assertNotIn(value.encode(), ciphertext)
            self.assertNotIn(value, metadata)
        self.assertNotIn("P<KHM", json.dumps(result))
        self.assertEqual(document.document_number_hmac,
                         self.app.state.field_cipher.lookup_hash("N01234567", "KH_PASSPORT"))

    async def test_bad_checksum_is_stored_as_review_with_visual_fields_preserved(self):
        broken = td3()
        broken[1] = broken[1][:19] + "9" + broken[1][20:]
        self.use_ocr(kh_passport_page(mrz=broken))
        session_id = await self.capture_page("KH_PASSPORT")
        result = await self.result(session_id)
        self.assertEqual(result["status"], "SELFIE_REQUIRED")
        self.assertEqual(result["checks"]["mrz"], "REVIEW")
        self.assertIn("MRZ_CHECK_DIGIT_FAILED", result["review_flags"])
        self.assertEqual(result["identity"]["date_of_birth"], "1990-03-15")
        self.assertIsNone(result["decision"])
        with Session(self.engine) as db:
            stored = db.scalar(sa.select(MRZResult))
        self.assertFalse(stored.mrz_valid)
        self.assertFalse(stored.check_digit_results["date_of_birth"]["valid"])
        self.assertFalse(result["mrz"]["mrz_valid"])

    async def test_foreign_passport_uses_the_generic_mrz_adapter(self):
        current = td3(state="UTO", number="L898902C3", nationality="UTO", birth="740812", sex="F", expiry="340415",
                      surname="ERIKSSON", given="ANNA MARIA")
        self.use_ocr([page_line("UTOPIA PASSPORT", 0.05)] + [page_line(t, 0.85 + i * 0.06, 0.0, ("MRZ_PASS",)) for i, t in enumerate(current)])
        session_id = await self.capture_page("PASSPORT")
        self.assertEqual((await self.session(session_id))["status"], "SELFIE_REQUIRED")
        result = await self.result(session_id)
        self.assertEqual(result["identity"]["full_name"], "ANNA MARIA ERIKSSON")
        self.assertEqual(result["identity"]["nationality"], "UTO")
        self.assertEqual(result["document"]["expiry_status"], "VALID")

    async def test_unreadable_mrz_on_a_foreign_passport_means_recapture(self):
        self.use_ocr([page_line("UTOPIA PASSPORT", 0.05), page_line("Surname ERIKSSON", 0.2)])
        session_id = await self.capture_page("PASSPORT")
        self.assertEqual((await self.session(session_id))["status"], "DOCUMENT_REQUIRED")

    async def test_passport_shown_in_an_id_card_session_is_sent_back(self):
        self.use_ocr(kh_passport_page(), [])
        session_id = await self.capture_both("KH_NATIONAL_ID")
        with Session(self.engine) as db:
            check = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "DOCUMENT_PROCESSING"))
        self.assertEqual(check.evidence_metadata["reason_codes"], ["DOCUMENT_TYPE_MISMATCH"])


class ProcessingWithoutPIIKeysTests(CaptureAPICase):
    async def test_extraction_is_unavailable_and_nothing_is_stored(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        await self.upload(session_id, GOOD_BACK, "back")
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, UUID(session_id)).status.value, "DOCUMENT_PROCESSING")
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentField)), 0)
            self.assertIn("PII_ENCRYPTION_NOT_CONFIGURED", db.scalars(sa.select(AuditLog.action)).all())


TESSERACT = TesseractOCREngine()


@unittest.skipUnless(images.fonts_available() and TESSERACT.is_available(("khm", "eng")),
                     "Tesseract with khm+eng and Khmer fonts are required for the real OCR test.")
class RealOCREndToEndTests(CaptureAPICase):
    extra_settings = PII_SETTINGS

    async def test_photographed_specimen_card_is_read_by_real_ocr(self):
        session_id = await self.create()
        front = images.encode(images.photographed(images.kh_id_front()))
        back = images.encode(images.photographed(images.kh_id_back()))
        code, body, _ = await self.upload(session_id, front, "front")
        self.assertEqual(body["capture_status"], "ACCEPTED", body)
        code, body, _ = await self.upload(session_id, back, "back")
        self.assertEqual(body["capture_status"], "ACCEPTED", body)
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "SELFIE_REQUIRED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["document"]["document_number_masked"], "*****3040")
        self.assertEqual(result["identity"]["full_name"], "SOK SOPHEA")
        self.assertEqual(result["identity"]["full_name_local"], "សុខ សុភា")
        self.assertEqual(result["identity"]["date_of_birth"], "1990-03-15")
        self.assertEqual(result["identity"]["sex"], "F")
        self.assertEqual(result["checks"]["document_classification"], "PASS")
        # Phase 5: the back MRZ is read by the dedicated pass, validated, and agrees with the front.
        self.assertEqual((result["checks"]["mrz"], result["checks"]["mrz_consistency"]), ("PASS", "PASS"))
        with Session(self.engine) as db:
            fields = {f.field_name: f for f in db.scalars(sa.select(DocumentField))}
        # Anything the engine was unsure about is visibly flagged, never silently accepted.
        for item in fields.values():
            if item.source == "OCR" and item.confidence < 0.8 and item.normalized_value_ciphertext:
                self.assertIn("LOW_OCR_CONFIDENCE", result["review_flags"])

    async def test_photographed_nssf_specimen_is_read_by_real_ocr(self):
        session_id = await self.create("KH_NSSF")
        for path, card in [("front", images.kh_nssf_front()), ("back", images.kh_nssf_back())]:
            code, body, _ = await self.upload(session_id, images.encode(images.photographed(card)), path)
            self.assertEqual(body["capture_status"], "ACCEPTED", body)
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "SELFIE_REQUIRED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["document"]["type"], "KH_NSSF")
        self.assertEqual(result["document"]["document_number_masked"], "******5678")
        # Phase 7: the QR on the back decodes and agrees with the printed front.
        self.assertEqual(result["checks"]["barcode"], "PASS")
        with Session(self.engine) as db:
            stored = db.scalar(sa.select(BarcodeResult))
        self.assertEqual((stored.symbology, stored.decoded, stored.format_valid), ("QR_CODE", True, True))
        self.assertEqual(stored.data_consistency["fields"]["document_number"], "MATCH")
        self.assertNotIn(b"CHAN", stored.payload_ciphertext)
        self.assertEqual(result["identity"]["full_name"], "CHAN DARA")
        self.assertEqual(result["identity"]["full_name_local"], "ចាន់ ដារ៉ា")
        self.assertEqual(result["identity"]["date_of_birth"], "1988-07-02")
        self.assertEqual(result["identity"]["sex"], "M")
        self.assertEqual(result["checks"]["document_classification"], "PASS")

    @unittest.skipUnless(images.passport_fonts_available(), "Passport specimen fonts are required.")
    async def test_photographed_cambodian_passport_is_read_by_real_ocr(self):
        session_id = await self.create("KH_PASSPORT")
        code, body, _ = await self.upload(session_id, images.encode(images.photographed(images.kh_passport())), None, side="DATA_PAGE")
        self.assertEqual(body["capture_status"], "ACCEPTED", body)
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "SELFIE_REQUIRED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"]["mrz"], "PASS")
        self.assertEqual(result["identity"]["full_name"], "SOPHEA SOK")
        self.assertEqual(result["identity"]["date_of_birth"], "1990-03-15")
        # Whatever the visual OCR misread is surfaced by the MRZ cross-check, never silently accepted.
        if result["checks"]["mrz_consistency"] == "REVIEW":
            self.assertTrue(any(code.startswith("MRZ_VISUAL_") for code in result["review_flags"]))

    @unittest.skipUnless(images.passport_fonts_available(), "Passport specimen fonts are required.")
    async def test_photographed_foreign_passport_is_read_from_its_mrz(self):
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", {"user_id": "traveller", "country": "TH",
                                   "expected_document_type": "PASSPORT"}, self.headers)
        session_id = body["session_id"]
        await self.upload(session_id, images.encode(images.photographed(images.foreign_passport())), None, side="DATA_PAGE")
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "SELFIE_REQUIRED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["identity"]["full_name"], "ANNA MARIA ERIKSSON")
        self.assertEqual(result["document"]["document_number_masked"], "*****02C3")
        self.assertEqual(result["checks"]["mrz"], "PASS")
        # Phase 6: the printed page is read too and agrees with the MRZ.
        self.assertEqual(result["checks"]["mrz_consistency"], "PASS")

    async def test_photographed_foreign_id_card_is_read_front_and_back(self):
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", {"user_id": "resident", "country": "TH",
                                   "expected_document_type": "NATIONAL_ID"}, self.headers)
        session_id = body["session_id"]
        for path, card in [("front", images.foreign_id_front()), ("back", images.foreign_id_back())]:
            code, body, _ = await self.upload(session_id, images.encode(images.photographed(card)), path)
            self.assertEqual(body["capture_status"], "ACCEPTED", body)
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "SELFIE_REQUIRED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["identity"]["full_name"], "ANNA MARIA ERIKSSON")
        self.assertEqual((result["checks"]["mrz"], result["checks"]["mrz_consistency"]), ("PASS", "PASS"))
        self.assertEqual(result["checks"]["issuing_country"], "REVIEW")  # ICAO's fictional "UTO" is no ISO country

    async def test_card_without_identity_text_is_sent_back(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        await self.upload(session_id, GOOD_BACK, "back")
        code, session, _ = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(session["status"], "DOCUMENT_REQUIRED")
