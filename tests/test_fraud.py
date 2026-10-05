"""Phase 12: cross-check engine, fraud detectors, analysis service and API exposure."""

import base64
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
import os
import unittest
from uuid import uuid4

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.routes import _process_after_commit
from kyc.biometrics import FaceEmbedding, serialize_embedding
from kyc.core.crypto import FieldCipher
from kyc.db.models import AuditLog, BarcodeResult, BiometricTemplate, DocumentCheck, DocumentField, FraudSignal, IdentityDocument, KYCSession, MRZResult
from kyc.fraud import crosscheck, engine, metadata
from kyc.fraud.crosscheck import Comparison
from kyc.fraud.engine import (BarcodeFacts, CaptureMetadata, DuplicateFacts, FraudContext, NFCFacts, PortraitFacts)
from kyc.fraud.signals import Category, Severity
from kyc.mrz import parser as mrz_parser
from kyc.services.biometrics import _template_context
from kyc.services.documents import ProcessingOutcome
from tests import nfc_fixtures as fx
from tests.helpers import call
from tests.mrz_build import td3
from tests.test_capture_store import keyring
from tests import test_nfc

MRZ, NFC = frozenset({"MRZ"}), frozenset({"NFC"})


def codes(signals):
    return {item.signal: item.severity for item in signals}


class CrossCheckTests(unittest.TestCase):
    def test_spec_example_dob_disagreement_is_reported_never_resolved(self):
        # OCR DOB 1999-01-01 vs MRZ DOB 1999-01-07 (check-digit valid) → REVIEW, values never chosen.
        matrix = crosscheck.build([Comparison("MRZ", "VISUAL", "date_of_birth", "MISMATCH", MRZ)])
        self.assertEqual(matrix["date_of_birth"].status, "CONFLICT")
        self.assertIsNone(matrix["date_of_birth"].outlier)  # two sources: nobody can be singled out
        self.assertEqual(crosscheck.summary(matrix), "REVIEW")
        self.assertEqual(codes(crosscheck.signals(matrix)), {"MRZ_VISUAL_DOB_MISMATCH": Severity.HIGH})
        self.assertNotIn("1999", json.dumps({name: vars(item) for name, item in matrix.items()}))

    def test_outlier_against_agreeing_signed_sources(self):
        matrix = crosscheck.build([
            Comparison("MRZ", "VISUAL", "date_of_birth", "MISMATCH", MRZ),
            Comparison("NFC", "VISUAL", "date_of_birth", "MISMATCH", NFC),
            Comparison("MRZ", "BARCODE", "date_of_birth", "MATCH", MRZ),
            Comparison("BARCODE", "VISUAL", "date_of_birth", "MISMATCH")])
        self.assertEqual(matrix["date_of_birth"].outlier, "VISUAL")
        found = codes(crosscheck.signals(matrix))
        self.assertEqual(found["VISUAL_OUTLIER_DOB"], Severity.HIGH)
        self.assertEqual(found["NFC_VISUAL_DOB_MISMATCH"], Severity.HIGH)
        self.assertEqual(found["BARCODE_VISUAL_DOB_MISMATCH"], Severity.MEDIUM)  # neither side signed or check-digit protected

    def test_weak_fields_and_unprotected_sources_are_medium(self):
        matrix = crosscheck.build([Comparison("MRZ", "VISUAL", "full_name", "MISMATCH", MRZ),
                                   Comparison("MRZ", "VISUAL", "expiry_date", "MISMATCH")])
        self.assertEqual(codes(crosscheck.signals(matrix)),
                         {"MRZ_VISUAL_NAME_MISMATCH": Severity.MEDIUM, "MRZ_VISUAL_EXPIRY_MISMATCH": Severity.MEDIUM})

    def test_states_other_than_match_or_mismatch_and_multiple_barcodes(self):
        self.assertEqual(crosscheck.summary(crosscheck.build([Comparison("MRZ", "VISUAL", "sex", "MRZ_ONLY")])),
                         "NOT_APPLICABLE")
        matrix = crosscheck.build([Comparison("BARCODE", "VISUAL", "sex", "MATCH"),
                                   Comparison("BARCODE", "VISUAL", "sex", "MISMATCH"),
                                   Comparison("BARCODE", "VISUAL", "sex", "MATCH")])
        self.assertEqual(matrix["sex"].pairs, {"BARCODE~VISUAL": "MISMATCH"})
        consistent = crosscheck.build([Comparison("NFC", "MRZ", "mrz_data_lines", "MATCH", NFC)])
        self.assertEqual(crosscheck.summary(consistent), "PASS")
        conflict = crosscheck.build([Comparison("MRZ", "NFC", "mrz_data_lines", "MISMATCH", NFC)])
        self.assertEqual(codes(crosscheck.signals(conflict)), {"NFC_MRZ_DATA_MISMATCH": Severity.HIGH})


class DetectorTests(unittest.TestCase):
    def context(self, **values):
        return FraudContext(verification_level="DOCUMENT_FACE_LIVENESS_NFC", session_date=date(2026, 10, 5), **values)

    def test_clean_evidence_passes_and_reports_coverage_honestly(self):
        report = engine.analyze(self.context(comparisons=[Comparison("MRZ", "VISUAL", "document_number", "MATCH", MRZ)],
                                             duplicates=DuplicateFacts(24, 3, recent_sessions=1)))
        self.assertEqual((report.result, report.cross_check_result, report.signals), ("PASS", "PASS", []))
        self.assertEqual(report.coverage["FONT_INCONSISTENCY"]["status"], "NOT_SUPPORTED")
        self.assertEqual(report.coverage["MRZ_MISMATCH"]["status"], "SUPPORTED")

    def test_validity_codes_become_signals_only_from_review_or_fail_checks(self):
        found = codes(engine.detect_validity(self.context(checks={
            "EXPIRY": ("FAIL", ("EXPIRED_DOCUMENT",)), "DATE_CONSISTENCY": ("REVIEW", ("ISSUED_BEFORE_BIRTH",)),
            "MRZ": ("REVIEW", ("MRZ_CHECK_DIGIT_FAILED",)), "OCR_CONFIDENCE": ("PASS", ("EXPIRED_DOCUMENT",))})))
        self.assertEqual(found, {"EXPIRED_DOCUMENT": Severity.MEDIUM, "IMPOSSIBLE_DATES_ISSUED_BEFORE_BIRTH": Severity.MEDIUM,
                                 "MRZ_CHECK_DIGIT_FAILED": Severity.MEDIUM})

    def test_cryptographic_evidence_is_tamper_and_fails_the_fraud_check(self):
        signals = engine.detect_tamper(self.context(
            barcodes=[BarcodeFacts("QRCODE", True, False, "SIGNATURE_INVALID"), BarcodeFacts("PDF417", False, None, None)],
            nfc=NFCFacts("NFC_FAILED", ("ACTIVE_AUTHENTICATION_FAILED", "DATA_GROUP_HASH_MISMATCH"), False)))
        self.assertEqual(codes(signals), {"BARCODE_SIGNATURE_INVALID": Severity.HIGH, "NFC_CHIP_CLONE_SUSPECTED": Severity.HIGH,
                                          "NFC_DATA_GROUP_ALTERED": Severity.HIGH})
        self.assertTrue(all(item.category == Category.TAMPER for item in signals))
        self.assertEqual(engine.result_for(signals), "FAIL")
        weak = engine.detect_tamper(self.context(nfc=NFCFacts("NFC_READ", ("DOCUMENT_SIGNER_NOT_TRUSTED",), None)))
        self.assertEqual((codes(weak), engine.result_for(weak)), ({"NFC_ISSUER_NOT_TRUSTED": Severity.LOW}, "PASS"))
        skipped = engine.detect_tamper(self.context(nfc=NFCFacts("NFC_NOT_SUPPORTED", ("CLIENT_REPORTED_NOT_SUPPORTED",), None)))
        self.assertEqual(codes(skipped), {"NFC_NOT_COMPLETED": Severity.LOW})

    def test_duplicates(self):
        found = codes(engine.detect_duplicates(self.context(duplicates=DuplicateFacts(
            24, 3, other_users=1, same_user_sessions=2, recent_sessions=3, reused_capture_sides=("FRONT",), reused_selfie=True))))
        self.assertEqual(found, {"DOCUMENT_USED_BY_ANOTHER_USER": Severity.HIGH, "DOCUMENT_PREVIOUSLY_USED": Severity.LOW,
                                 "DOCUMENT_VELOCITY": Severity.MEDIUM, "DOCUMENT_CAPTURE_REUSED": Severity.HIGH,
                                 "SELFIE_CAPTURE_REUSED": Severity.HIGH})
        self.assertEqual(engine.detect_duplicates(self.context(duplicates=DuplicateFacts(24, 3, recent_sessions=2))), [])

    def test_metadata_and_portrait(self):
        found = codes(engine.detect_metadata(self.context(metadata=[
            CaptureMetadata("FRONT", "image/jpeg", editor="photoshop", captured_on=date(2026, 9, 1)),
            CaptureMetadata("SELFIE", "image/png", screenshot=True), CaptureMetadata("BACK", "image/jpeg")])))
        self.assertEqual(found, {"EDITING_SOFTWARE_IN_METADATA": Severity.MEDIUM, "SCREENSHOT_METADATA": Severity.MEDIUM,
                                 "OLD_CAPTURE_TIMESTAMP": Severity.LOW})
        uncalibrated = engine.detect_portrait(self.context(portrait=PortraitFacts(0.05, 0.2, False, "dev")))
        self.assertEqual(codes(uncalibrated), {"PORTRAIT_DIFFERS_FROM_CHIP": Severity.MEDIUM})
        calibrated = engine.detect_portrait(self.context(portrait=PortraitFacts(0.05, 0.2, True, "prod")))
        self.assertEqual(codes(calibrated), {"PORTRAIT_DIFFERS_FROM_CHIP": Severity.HIGH})
        self.assertEqual(engine.detect_portrait(self.context(portrait=PortraitFacts(0.6, 0.2, False, "dev"))), [])

    def test_a_detector_can_be_added_without_touching_the_engine(self):
        extra = {"always": lambda context: [engine.Signal("TEST_SIGNAL", Severity.LOW, Category.CONTEXT, "always")]}
        report = engine.analyze(self.context(), {**engine.DETECTORS, **extra})
        self.assertEqual([item.signal for item in report.signals], ["TEST_SIGNAL"])
        self.assertIn("always", report.detectors)


class MetadataReaderTests(unittest.TestCase):
    def test_editor_screenshot_and_capture_date_are_the_only_facts_read(self):
        image = Image.new("RGB", (64, 48), (200, 180, 160))
        exif = image.getexif()
        exif[0x0131] = "Adobe Photoshop 25.0 (Macintosh)"
        exif[0x8825] = {1: "N"}  # GPS present but never read
        exif.get_ifd(0x8769)[0x9003] = "2024:02:03 10:11:12"
        buffer = BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        self.assertEqual(metadata.read(buffer.getvalue()), ("photoshop", False, date(2024, 2, 3)))
        info = PngInfo()
        info.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta><exif:UserComment>Screenshot</exif:UserComment></x:xmpmeta>')
        buffer = BytesIO()
        image.save(buffer, "PNG", pnginfo=info)
        self.assertEqual(metadata.read(buffer.getvalue()), (None, True, None))
        plain = BytesIO()
        Image.new("RGB", (8, 8)).save(plain, "JPEG")
        self.assertEqual(metadata.read(plain.getvalue()), (None, False, None))
        self.assertEqual(metadata.read(b"not an image"), (None, False, None))


class FraudAPITests(test_nfc.NFCAPICase):
    """Real HTTP flow through the NFC step, then the post-commit analysis."""

    async def result(self, session_id):
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, body)
        return body

    def signal_rows(self, session_id=None):
        with Session(self.engine) as db:
            query = sa.select(FraudSignal)
            if session_id:
                query = query.where(FraudSignal.session_id == session_id)
            return {row.signal: (row.severity, row.category) for row in db.scalars(query)}

    async def test_genuine_matching_chip_is_consistent_and_clean(self):
        session_id = await self.at_nfc()
        code, issued, _ = await self.challenge(session_id)
        code, body, _ = await self.send(session_id, challenge=issued)
        self.assertEqual(body["status"], "PROCESSING")
        result = await self.result(session_id)
        self.assertEqual((result["checks"]["cross_check"], result["checks"]["fraud"]), ("PASS", "PASS"))
        self.assertEqual(result["fraud_signals"], [])
        self.assertIsNone(result["decision"])
        with Session(self.engine) as db:
            check = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "CROSS_CHECK"))
            self.assertEqual(check.evidence_metadata["fields"]["mrz_data_lines"]["pairs"], {"NFC~MRZ": "MATCH"})
            analysis = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "FRAUD_ANALYSIS"))
            self.assertEqual(analysis.evidence_metadata["coverage"]["IMAGE_MANIPULATION"]["status"], "NOT_SUPPORTED")
            self.assertEqual(db.scalar(sa.select(AuditLog.action).where(AuditLog.action == "FRAUD_ANALYZED")), "FRAUD_ANALYZED")

    async def test_chip_from_another_passport_raises_high_consistency_signals(self):
        session_id = await self.at_nfc(printed_number="X1234567")
        code, issued, _ = await self.challenge(session_id)
        await self.send(session_id, challenge=issued)
        result = await self.result(session_id)
        self.assertEqual((result["checks"]["cross_check"], result["checks"]["fraud"]), ("REVIEW", "REVIEW"))
        signals = {item["signal"]: item["severity"] for item in result["fraud_signals"]}
        self.assertEqual(signals["NFC_VISUAL_DOCUMENT_NUMBER_MISMATCH"], "HIGH")
        self.assertIn("NFC_VISUAL_DOCUMENT_NUMBER_MISMATCH", result["review_flags"])
        self.assertEqual(set(result["fraud_signals"][0]), {"signal", "severity", "category"})  # no scores or details
        with Session(self.engine) as db:
            stored = json.dumps([row.evidence_metadata for row in db.scalars(sa.select(FraudSignal))])
            stored += json.dumps([row.evidence_metadata for row in db.scalars(sa.select(DocumentCheck).where(
                DocumentCheck.check_type.in_(("CROSS_CHECK", "FRAUD_ANALYSIS"))))])
        for value in ("X1234567", "L898902C3", "ERIKSSON", "1974"):
            self.assertNotIn(value, stored)

    async def test_cloned_chip_is_tamper_and_fails_the_fraud_check(self):
        session_id = await self.at_nfc()
        code, issued, _ = await self.challenge(session_id)
        await self.send(session_id, challenge=issued, signature=fx.aa_sign(fx.aa_rsa_key(), bytes.fromhex(issued["challenge"])))
        result = await self.result(session_id)
        self.assertEqual(result["checks"]["fraud"], "FAIL")
        self.assertEqual(self.signal_rows(session_id)["NFC_CHIP_CLONE_SUSPECTED"], ("HIGH", "TAMPER"))

    async def test_printed_portrait_that_differs_from_the_chip_portrait(self):
        session_id = await self.at_nfc()
        with Session(self.engine) as db, db.begin():
            # Re-seal the printed portrait's template as a different face (as if the photo had been swapped).
            printed = db.scalar(sa.select(BiometricTemplate).where(BiometricTemplate.source == "DOCUMENT_PORTRAIT"))
            other = np.zeros(128, dtype=np.float32)
            other[5] = 1
            sealed, key = self.app.state.biometric_cipher.seal(
                serialize_embedding(FaceEmbedding(other)), _template_context(db.get(KYCSession, session_id), printed.id,
                                                                             "DOCUMENT_PORTRAIT", printed))
            printed.template_ciphertext, printed.key_version = sealed, key
        code, issued, _ = await self.challenge(session_id)
        await self.send(session_id, challenge=issued)
        self.assertEqual(self.signal_rows(session_id)["PORTRAIT_DIFFERS_FROM_CHIP"], ("MEDIUM", "PORTRAIT"))

    async def test_duplicates_replays_tenant_scope_and_rerun(self):
        first = self.ready(status="PROCESSING", level="DOCUMENT_FACE")
        second = self.ready(status="PROCESSING", level="DOCUMENT_FACE")
        foreign = self.ready(status="PROCESSING", level="DOCUMENT_FACE", organization=self.other_org)
        with Session(self.engine) as db, db.begin():
            for session_id, user in ((first, "customer-a"), (second, "customer-b"), (foreign, "customer-c")):
                db.get(KYCSession, session_id).user_id = user
                db.scalar(sa.select(IdentityDocument).where(IdentityDocument.session_id == session_id)).document_number_hmac = "h" * 64
        analyzer = self.app.state.fraud_analyzer
        self.assertEqual(analyzer.analyze(self.org, second, uuid4()), "REVIEW")
        signals = self.signal_rows(second)
        self.assertEqual(signals["DOCUMENT_USED_BY_ANOTHER_USER"], ("HIGH", "DUPLICATE"))
        # ready() stores the same photo bytes for every session: an identical file is a replay.
        self.assertEqual(signals["DOCUMENT_CAPTURE_REUSED"], ("HIGH", "DUPLICATE"))
        with Session(self.engine) as db:
            details = db.scalar(sa.select(FraudSignal).where(FraudSignal.session_id == second,
                                FraudSignal.signal == "DOCUMENT_USED_BY_ANOTHER_USER")).evidence_metadata["details"]
        self.assertEqual(details["other_users"], 1)  # the other tenant's session is invisible
        before = len(signals)
        analyzer.analyze(self.org, second, uuid4())
        self.assertEqual(len(self.signal_rows(second)), before)  # a re-run replaces, never accumulates
        self.assertEqual(analyzer.analyze(self.org, self.ready(status="LIVENESS_REQUIRED"), uuid4()), "NOT_READY")

    async def test_mrz_and_barcode_are_compared_with_each_other(self):
        session_id = self.ready(status="PROCESSING", level="DOCUMENT_FACE")
        cipher = FieldCipher(keyring("pii-test-v1"), os.urandom(32))
        self.app.state.fraud_analyzer.field_cipher = cipher
        lines = td3()
        parsed = mrz_parser.read(lines)
        with Session(self.engine) as db, db.begin():
            document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.session_id == session_id))
            context = f"field/{self.org}/{session_id}/{document.id}/mrz/normalized"
            sealed, version = cipher.seal("\n".join(lines), context)
            db.add(DocumentField(organization_id=self.org, session_id=session_id, document_id=document.id, field_name="mrz",
                                 normalized_value_ciphertext=sealed, key_version=version, confidence=.99, source="MRZ"))
            db.add(MRZResult(organization_id=self.org, session_id=session_id, document_id=document.id, format="TD3",
                             mrz_valid=True, check_digit_results=parsed.check_digits,
                             field_consistency={"date_of_birth": "MATCH", "document_number": "MATCH"}))
            payload = json.dumps({"document_number": "N01234567", "dob": "1990-03-16", "sex": "F"}).encode()
            sealed, version = cipher.seal(base64.b64encode(payload).decode(),
                                          f"barcode/{self.org}/{session_id}/{document.id}/DATA_PAGE/QRCODE")
            db.add(BarcodeResult(organization_id=self.org, session_id=session_id, document_id=document.id, symbology="QRCODE",
                                 decoded=True, format_valid=True, signature_present=False,
                                 data_consistency={"side": "DATA_PAGE", "fields": {"date_of_birth": "MISMATCH"}},
                                 payload_ciphertext=sealed, key_version=version))
        self.app.state.fraud_analyzer.analyze(self.org, session_id, uuid4())
        signals = self.signal_rows(session_id)
        self.assertEqual(signals["MRZ_BARCODE_DOB_MISMATCH"][0], "HIGH")       # check-digit-protected MRZ disagrees
        self.assertEqual(signals["BARCODE_OUTLIER_DOB"][0], "HIGH")            # MRZ and printed page agree
        self.assertNotIn("MRZ_BARCODE_DOCUMENT_NUMBER_MISMATCH", signals)


class DocumentOnlyTriggerTests(unittest.TestCase):
    def test_accepted_document_runs_the_analysis_after_commit(self):
        calls = []
        state = type("State", (), {})()
        state.document_processor = type("P", (), {"process": lambda self, *args: ProcessingOutcome("ACCEPTED")})()
        state.fraud_analyzer = type("A", (), {"analyze": lambda self, *args: calls.append(args)})()
        _process_after_commit(state, uuid4(), uuid4(), uuid4())
        self.assertEqual(len(calls), 1)
        state.document_processor = type("P", (), {"process": lambda self, *args: ProcessingOutcome("RECAPTURE")})()
        _process_after_commit(state, uuid4(), uuid4(), uuid4())
        self.assertEqual(len(calls), 1)
