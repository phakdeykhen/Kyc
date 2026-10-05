"""Phase 11: server-side ePassport chip verification against a fictional test PKI."""

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
import sqlalchemy as sa
from sqlalchemy.orm import Session
import unittest

from kyc.core.crypto import FieldCipher
from kyc.db.models import BiometricTemplate, DocumentField, FaceComparison, IdentityDocument, KYCSession, NFCChallenge, NFCResult
from kyc.domain.enums import NFCStatus
from kyc.nfc import active_auth
from kyc.nfc.lds import LDSError, dg1_mrz, dg2_portrait, dg15_public_key, read_tlv
from kyc.nfc.sod import verify_sod
from kyc.nfc.trust import CSCATrustStore
from kyc.nfc.verify import verify_chip
from tests import nfc_fixtures as fx
from tests.helpers import call, multipart
from tests.mrz_build import td3
from tests.test_biometrics_api import BiometricAPICase
from tests.test_capture_store import keyring


class LDSTests(unittest.TestCase):
    def test_data_groups_parse(self):
        chip = fx.make_chip()
        self.assertEqual(dg1_mrz(chip.groups[1])[0][:5], "P<UTO")
        self.assertEqual(dg2_portrait(chip.groups[2]).media_type, "image/jpeg")
        self.assertEqual(dg2_portrait(fx.dg2(fmt="JPEG2000")).media_type, "image/jp2")
        self.assertEqual(dg15_public_key(chip.groups[15]).key_size, 1024)

    def test_long_lengths_and_malformed_input(self):
        tag, value, _ = read_tlv(fx.tlv(0x5F2E, b"x" * 70000))
        self.assertEqual((tag, len(value)), (0x5F2E, 70000))
        for broken in (b"", b"\x61", b"\x61\x05ab", b"\x75\x00"):
            with self.subTest(broken=broken), self.assertRaises(LDSError):
                dg1_mrz(broken) if broken[:1] != b"\x75" else dg2_portrait(broken)


class PassiveAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.chip = fx.make_chip()
        self.trust = CSCATrustStore([self.chip.pki.csca])

    def test_genuine_chip_passes_every_step(self):
        result = verify_sod(self.chip.sod, self.chip.groups, self.trust)
        self.assertTrue(result.passed)
        self.assertEqual(result.data_group_hashes, {"DG1": "MATCH", "DG2": "MATCH", "DG15": "MATCH"})
        self.assertEqual((result.dsc_issuer_country, result.digest_algorithm), ("UT", "sha256"))

    def test_altered_data_group_and_forged_signature_are_tampering(self):
        altered = {**self.chip.groups, 2: fx.dg2()[:-4] + b"\x00\x00\xff\xd9"}
        result = verify_sod(self.chip.sod, altered, self.trust)
        self.assertTrue(result.tampered)
        self.assertEqual(result.data_group_hashes["DG2"], "MISMATCH")
        forged = fx.sod(self.chip.pki, self.chip.groups, tamper_signature=True)
        self.assertEqual(verify_sod(forged, self.chip.groups, self.trust).signature_valid, False)

    def test_untrusted_or_missing_anchors_are_not_verified_and_not_tampering(self):
        for trust, reason in ((CSCATrustStore([fx.make_pki().csca]), "DOCUMENT_SIGNER_NOT_TRUSTED"),
                              (CSCATrustStore(), "CSCA_TRUST_STORE_NOT_CONFIGURED")):
            with self.subTest(reason=reason):
                result = verify_sod(self.chip.sod, self.chip.groups, trust)
                self.assertFalse(result.passed)
                self.assertFalse(result.tampered)
                self.assertIn(reason, result.reasons)

    def test_signer_from_another_country_using_the_same_name_is_rejected(self):
        impostor = fx.make_pki()  # same subject names, different keys
        chip = fx.make_chip(pki=fx.make_pki(dsc_signed_by=impostor))
        result = verify_sod(chip.sod, chip.groups, self.trust)
        self.assertFalse(result.dsc_trusted)

    def test_expired_document_signer(self):
        chip = fx.make_chip(pki=fx.make_pki(dsc_valid_days=-1))
        result = verify_sod(chip.sod, chip.groups, CSCATrustStore([chip.pki.csca]))
        self.assertFalse(result.dsc_within_validity)
        self.assertFalse(result.passed)

    def test_data_group_not_listed_and_garbage(self):
        extra = {**self.chip.groups, 11: fx.tlv(0x6B, b"\x01")}
        self.assertEqual(verify_sod(self.chip.sod, extra, self.trust).data_group_hashes["DG11"], "NOT_IN_SOD")
        self.assertIn("SOD_UNPARSEABLE", verify_sod(b"\x77\x03abc", self.chip.groups, self.trust).reasons)


class ActiveAuthenticationTests(unittest.TestCase):
    def test_rsa_iso9796_2(self):
        key = fx.aa_rsa_key()
        challenge = os.urandom(8)
        self.assertTrue(active_auth.verify(key.public_key(), challenge, fx.aa_sign(key, challenge)))
        self.assertFalse(active_auth.verify(key.public_key(), os.urandom(8), fx.aa_sign(key, challenge)))
        self.assertFalse(active_auth.verify(fx.aa_rsa_key().public_key(), challenge, fx.aa_sign(key, challenge)))

    def test_ecdsa_plain_signature(self):
        key = ec.generate_private_key(ec.SECP256R1())
        challenge = os.urandom(8)
        r, s = decode_dss_signature(key.sign(challenge, ec.ECDSA(hashes.SHA256())))
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        self.assertTrue(active_auth.verify(key.public_key(), challenge, signature))
        self.assertFalse(active_auth.verify(key.public_key(), b"x" * 8, signature))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.chip = fx.make_chip()
        self.trust = CSCATrustStore([self.chip.pki.csca])
        self.challenge = os.urandom(8)

    def test_status_mapping(self):
        good = verify_chip(self.chip.groups, self.chip.sod, self.trust, self.challenge, fx.aa_sign(self.chip.aa_key, self.challenge))
        self.assertEqual((good.status, good.active_authentication), (NFCStatus.NFC_VERIFIED, True))
        self.assertEqual(good.mrz.document_number, "L898902C3")
        clone = verify_chip(self.chip.groups, self.chip.sod, self.trust, self.challenge, fx.aa_sign(fx.aa_rsa_key(), self.challenge))
        self.assertEqual(clone.status, NFCStatus.NFC_FAILED)
        self.assertIn("ACTIVE_AUTHENTICATION_FAILED", clone.reasons)
        untrusted = verify_chip(self.chip.groups, self.chip.sod, CSCATrustStore())
        self.assertEqual(untrusted.status, NFCStatus.NFC_READ)
        self.assertIn("ACTIVE_AUTHENTICATION_NOT_PERFORMED", untrusted.reasons)
        tampered = fx.make_chip(tamper_dg1=True)
        self.assertEqual(verify_chip(tampered.groups, tampered.sod, CSCATrustStore([tampered.pki.csca])).status, NFCStatus.NFC_FAILED)
        self.assertEqual(verify_chip({}, None, self.trust).status, NFCStatus.NFC_FAILED)
        no_aa = fx.make_chip(with_aa=False)
        plain = verify_chip(no_aa.groups, no_aa.sod, CSCATrustStore([no_aa.pki.csca]))
        self.assertEqual(plain.status, NFCStatus.NFC_VERIFIED)
        self.assertIn("ACTIVE_AUTHENTICATION_NOT_SUPPORTED_BY_CHIP", plain.reasons)


class NFCAPITests(BiometricAPICase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.chip = fx.make_chip()
        self.app.state.csca_trust = CSCATrustStore([self.chip.pki.csca])
        self.cipher = FieldCipher(keyring("pii-test-v1"), os.urandom(32))
        self.app.state.field_cipher = self.cipher

    async def at_nfc(self, printed_number="L898902C3"):
        session_id = self.ready(level="DOCUMENT_FACE_LIVENESS_NFC")
        code, body, _ = await self.upload(session_id)  # real selfie flow → encrypted selfie template
        self.assertEqual(body["status"], "LIVENESS_REQUIRED", body)
        printed = td3(state="UTO", number=printed_number, nationality="UTO", birth="740812", sex="F", expiry="340415",
                      surname="ERIKSSON", given="ANNA MARIA")
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            record.status = "NFC_REQUIRED"  # liveness itself is covered by the Phase 10 tests
            document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.session_id == session_id))
            for name, value in {"mrz": "\n".join(printed), "document_number": printed_number, "date_of_birth": "1974-08-12",
                                "expiry_date": "2034-04-15", "full_name": "ANNA MARIA ERIKSSON"}.items():
                sealed, version = self.cipher.seal(value, f"field/{self.org}/{session_id}/{document.id}/{name}/normalized")
                db.add(DocumentField(organization_id=self.org, session_id=session_id, document_id=document.id, field_name=name,
                                     normalized_value_ciphertext=sealed, key_version=version, confidence=0.95, source="OCR"))
        return session_id

    async def challenge(self, session_id):
        return await call(self.app, f"/v1/kyc/{session_id}/nfc/challenge", "POST", body={}, headers=self.headers)

    async def send(self, session_id, chip=None, challenge=None, signature=None, read_status="READ", groups=(1, 2, 15)):
        chip = chip or self.chip
        fields = {"read_status": read_status, "access_protocol": "PACE"}
        files = []
        if read_status == "READ":
            files = [("sod", "sod.bin", "application/octet-stream", chip.sod)] + [
                (f"dg{n}", f"dg{n}.bin", "application/octet-stream", chip.groups[n]) for n in groups if n in chip.groups]
        if challenge:
            fields["challenge_id"] = challenge["challenge_id"]
            fields["aa_signature"] = (signature or fx.aa_sign(chip.aa_key, bytes.fromhex(challenge["challenge"]))).hex()
        raw, content_type = multipart(fields, files)
        return await call(self.app, f"/v1/kyc/{session_id}/nfc", "POST", raw=raw, content_type=content_type, headers=self.headers)

    async def test_genuine_chip_is_verified_matched_and_never_stored(self):
        session_id = await self.at_nfc()
        code, issued, _ = await self.challenge(session_id)
        self.assertEqual((code, len(bytes.fromhex(issued["challenge"]))), (200, 8))
        code, body, _ = await self.send(session_id, challenge=issued)
        self.assertEqual(code, 200, body)
        self.assertEqual((body["status"], body["nfc_status"]), ("PROCESSING", "NFC_VERIFIED"))
        self.assertEqual((body["passive_authentication"], body["active_authentication"]), (True, True))
        self.assertEqual(body["document_consistency"]["mrz_data_lines"], "MATCH")
        self.assertEqual(body["chip_face_match"]["result"], "REVIEW")  # uncalibrated policy, as for the selfie
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual((result["checks"]["nfc"], result["checks"]["nfc_status"]), ("PASS", "NFC_VERIFIED"))
        self.assertEqual(result["checks"]["chip_document_consistency"], "PASS")
        self.assertEqual(result["checks"]["chip_active_authentication"], "PASS")
        self.assertIsNone(result["decision"])
        with Session(self.engine) as db:
            row = db.scalar(sa.select(NFCResult))
            stored = json.dumps(row.evidence_metadata) + json.dumps(row.data_group_checks)
            self.assertFalse(row.evidence_metadata["raw_chip_data_retained"])
            self.assertEqual(row.trust_store_version, self.app.state.csca_trust.version)
            self.assertNotIn("L898902C3", stored)
            self.assertNotIn("ERIKSSON", stored)
            chip_template = db.scalar(sa.select(BiometricTemplate).where(BiometricTemplate.source == "CHIP_PORTRAIT"))
            self.assertIsNotNone(chip_template.document_id)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceComparison)), 2)

    async def test_cloned_chip_fails_active_authentication(self):
        session_id = await self.at_nfc()
        code, issued, _ = await self.challenge(session_id)
        clone_signature = fx.aa_sign(fx.aa_rsa_key(), bytes.fromhex(issued["challenge"]))
        code, body, _ = await self.send(session_id, challenge=issued, signature=clone_signature)
        self.assertEqual((body["nfc_status"], body["active_authentication"]), ("NFC_FAILED", False))
        self.assertIsNone(body["chip_face_match"])  # an unverified chip never becomes a face reference

    async def test_chip_from_another_passport_is_a_mismatch(self):
        session_id = await self.at_nfc(printed_number="X1234567")
        code, issued, _ = await self.challenge(session_id)
        code, body, _ = await self.send(session_id, challenge=issued)
        self.assertEqual(body["nfc_status"], "NFC_VERIFIED")  # the chip itself is genuine…
        self.assertEqual(body["document_consistency"]["document_number"], "MISMATCH")  # …but not this passport's
        self.assertIn("CHIP_DOCUMENT_MISMATCH", body["reason_codes"])
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"]["chip_document_consistency"], "FAIL")
        self.assertIn("NFC_CHIP_DOCUMENT_MISMATCH", result["review_flags"])

    async def test_ocr_noise_in_the_unprotected_name_line_is_not_a_mismatch(self):
        session_id = await self.at_nfc()
        with Session(self.engine) as db, db.begin():
            field = db.scalar(sa.select(DocumentField).where(DocumentField.field_name == "mrz"))
            context = f"field/{self.org}/{session_id}/{field.document_id}/mrz/normalized"
            noisy = self.cipher.open(field.normalized_value_ciphertext, field.key_version, context).replace("ERIKSSON", "BRIKSSON")
            field.normalized_value_ciphertext, field.key_version = self.cipher.seal(noisy, context)
        code, body, _ = await self.send(session_id)
        self.assertEqual(body["document_consistency"]["mrz_data_lines"], "MATCH")
        self.assertNotIn("CHIP_DOCUMENT_MISMATCH", body["reason_codes"])

    async def test_untrusted_issuer_is_read_not_verified(self):
        self.app.state.csca_trust = CSCATrustStore()
        session_id = await self.at_nfc()
        code, body, _ = await self.send(session_id)
        self.assertEqual(body["nfc_status"], "NFC_READ")
        self.assertIn("CSCA_TRUST_STORE_NOT_CONFIGURED", body["reason_codes"])
        self.assertIn("ACTIVE_AUTHENTICATION_NOT_PERFORMED", body["reason_codes"])

    async def test_client_reported_outcomes_retry_then_record(self):
        session_id = await self.at_nfc()
        code, body, _ = await self.send(session_id, read_status="NOT_AVAILABLE")
        self.assertEqual((body["status"], body["retry_allowed"]), ("NFC_REQUIRED", True))
        code, body, _ = await self.send(session_id, read_status="NOT_SUPPORTED")
        self.assertEqual((body["status"], body["nfc_status"]), ("PROCESSING", "NFC_NOT_SUPPORTED"))
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"]["nfc"], "NOT_APPLICABLE")

    async def test_challenge_replay_expiry_and_attempt_limit(self):
        session_id = await self.at_nfc()
        code, issued, _ = await self.challenge(session_id)
        code, body, _ = await self.send(session_id, read_status="FAILED")
        code, newer, _ = await self.challenge(session_id)
        code, body, _ = await self.send(session_id, challenge=issued)
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_ALREADY_USED"))  # voided by the newer one
        with Session(self.engine) as db, db.begin():
            db.get(NFCChallenge, UUID(newer["challenge_id"])).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.send(session_id, challenge=newer)
        self.assertEqual(body["reason_code"], "CHALLENGE_EXPIRED")
        code, body, _ = await self.send(session_id, read_status="FAILED")
        self.assertEqual(body["status"], "NFC_REQUIRED")
        code, body, _ = await self.send(session_id, read_status="FAILED")
        self.assertEqual((body["status"], body["retry_allowed"]), ("PROCESSING", False))  # attempts exhausted → recorded

    async def test_state_and_level_rules(self):
        not_yet = self.ready(status="LIVENESS_REQUIRED", level="DOCUMENT_FACE_LIVENESS_NFC")
        code, body, _ = await self.challenge(not_yet)
        self.assertEqual(body["reason_code"], "NFC_NOT_OPEN")
        no_nfc = self.ready(status="PROCESSING", level="DOCUMENT_FACE_LIVENESS")
        code, body, _ = await self.challenge(no_nfc)
        self.assertEqual(body["reason_code"], "NFC_NOT_REQUIRED")
        foreign = self.ready(status="NFC_REQUIRED", level="DOCUMENT_FACE_LIVENESS_NFC", organization=self.other_org)
        code, body, _ = await self.challenge(foreign)
        self.assertEqual(code, 404)
