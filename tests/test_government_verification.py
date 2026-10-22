"""Official QR handoff, tenant/identity access and separation from KYC decisions."""

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.crypto import FieldCipher
from kyc.barcode.engine import BarcodeRead
from kyc.db.models import AuditLog, BarcodeResult, DocumentCheck, IdentityDocument, KYCSession, Reviewer
from kyc.domain.enums import CheckResult, DocumentType, SessionStatus
from kyc.review.access import new_token
from kyc.services.government import official_verification_url
from tests.test_capture_store import keyring
from tests import test_documents_api, test_tenancy
from tests.test_kh_national_id import NSSF_BACK, nssf_front

OFFICIAL_URL = "https://verify.gov.kh/verify/government-record-fixture?key=" + "a" * 64


class GovernmentScanTests(test_documents_api.CaptureAPICase):
    extra_settings = test_documents_api.PII_SETTINGS

    async def test_scanned_qr_survives_processing_encryption_and_applicant_read(self):
        self.app.state.document_processor.ocr = test_documents_api.ScriptedOCR(nssf_front(), NSSF_BACK)
        session_id = await self.create("KH_NSSF", "DOCUMENT_ONLY")
        code, issued, _ = await self.request_token(session_id)
        self.assertEqual(code, 201, issued)
        qr = BarcodeRead("QR_CODE", OFFICIAL_URL.encode(), OFFICIAL_URL, (0.1, 0.1, 0.3, 0.3))
        with patch("kyc.services.documents.barcode_engine.decode", side_effect=[[qr], []]):
            await self.upload(session_id, test_documents_api.GOOD, "front")
            code, body, _ = await self.upload(session_id, test_documents_api.GOOD_BACK, "back")
            self.assertEqual(code, 200, body)
        device = {"Authorization": f"Bearer {issued['client_token']}", "X-Organization-ID": str(self.org)}
        code, body, _ = await test_documents_api.call(self.app,
            f"/v1/kyc/{session_id}/government-verification", headers=device)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "LINK_AVAILABLE", OFFICIAL_URL))
        self.assertFalse(body["verified"])

    async def request_token(self, session_id):
        return await test_documents_api.call(self.app, f"/v1/kyc/{session_id}/client-token", "POST", {}, self.headers)


class GovernmentURLTests(unittest.TestCase):
    def test_supported_official_record_links(self):
        self.assertEqual(official_verification_url(OFFICIAL_URL), OFFICIAL_URL)
        self.assertEqual(official_verification_url(OFFICIAL_URL.replace("verify.gov.kh", "VERIFY.GOV.KH:443")), OFFICIAL_URL)
        self.assertEqual(official_verification_url("https://verify.gov.kh/verify/public-record"),
                         "https://verify.gov.kh/verify/public-record")

    def test_lookalikes_credentials_non_https_and_unexpected_ports_are_rejected(self):
        for value in (
            OFFICIAL_URL.replace("https:", "http:"), OFFICIAL_URL.replace("https:", "javascript:"),
            OFFICIAL_URL.replace("verify.gov.kh", "verify.gov.kh.evil.test"),
            OFFICIAL_URL.replace("verify.gov.kh", "evil.test@verify.gov.kh"),
            OFFICIAL_URL.replace("verify.gov.kh", "verify.gov.kh@evil.test"),
            OFFICIAL_URL.replace("verify.gov.kh", "verify.gov.kh:8443"),
            OFFICIAL_URL.replace("verify.gov.kh", "verify.gov.kh\\@evil.test"),
            OFFICIAL_URL.replace("verify.gov.kh", "verіfy.gov.kh"),
            OFFICIAL_URL.replace("https://", "//"),
        ):
            with self.subTest(value=value):
                self.assertIsNone(official_verification_url(value))

    def test_redirects_malformed_queries_and_encoded_paths_are_rejected(self):
        for value in (
            OFFICIAL_URL + "&next=https://evil.test", OFFICIAL_URL + "&key=" + "b" * 64,
            OFFICIAL_URL + "#https://evil.test", OFFICIAL_URL.replace("?key=", "?redirect="),
            OFFICIAL_URL[:-1], OFFICIAL_URL.replace("?key=" + "a" * 64, "?key="),
            OFFICIAL_URL.replace("government-record-fixture", "..%2Fredirect"),
            OFFICIAL_URL.replace("/verify/", "/redirect/"),
            OFFICIAL_URL.replace("government-record-fixture", "record\nfixture"),
            "https://verify.gov.kh/", "https://verify.gov.kh/verify/record?key",
        ):
            with self.subTest(value=value):
                self.assertIsNone(official_verification_url(value))


class GovernmentVerificationTests(test_tenancy.TenantCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cipher = FieldCipher(keyring("government-test-v1"), os.urandom(32))
        self.app.state.field_cipher = self.cipher
        self.payload = {**self.payload, "expected_document_type": "KH_NSSF", "verification_level": "DOCUMENT_ONLY"}

    async def prepared(self, payload=OFFICIAL_URL):
        session_id = UUID((await self.create())["session_id"])
        document_id = uuid4()
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            record.status = SessionStatus.PROCESSING
            document = IdentityDocument(id=document_id, organization_id=self.org, session_id=session_id,
                                        document_type=DocumentType.KH_NSSF, issuing_country="KH",
                                        processed_at=datetime.now(timezone.utc),
                                        delete_after=datetime.now(timezone.utc) + timedelta(days=1))
            db.add(document)
            db.flush()
            if payload is not None:
                context = f"barcode/{self.org}/{session_id}/{document_id}/FRONT/QR_CODE"
                sealed, version = self.cipher.seal(base64.b64encode(payload.encode()).decode(), context)
                db.add(BarcodeResult(organization_id=self.org, session_id=session_id, document_id=document_id,
                                     symbology="QR_CODE", decoded=True, signature_present=False,
                                     data_consistency={"side": "FRONT", "format": "TEXT", "fields": {}},
                                     payload_ciphertext=sealed, key_version=version))
        return session_id, document_id

    async def government(self, session_id, headers=None):
        return await self.request(f"/v1/kyc/{session_id}/government-verification", headers or self.headers)

    async def test_pending_scan_and_processed_card_without_official_qr(self):
        session_id = (await self.create())["session_id"]
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verified"]), (200, "PENDING", False))
        session_id, _ = await self.prepared(None)
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "NO_OFFICIAL_QR", None))

    async def test_official_link_is_available_but_not_government_verified(self):
        session_id, _ = await self.prepared()
        code, body, headers = await self.government(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "LINK_AVAILABLE")
        self.assertEqual(body["verification_url"], OFFICIAL_URL)
        self.assertFalse(body["verified"])
        self.assertEqual(headers["cache-control"], "no-store")
        with Session(self.engine) as db:
            stored = db.scalar(sa.select(BarcodeResult))
            self.assertNotIn(OFFICIAL_URL.encode(), stored.payload_ciphertext)
            audit_text = json.dumps([[row.reason_codes, row.event_metadata] for row in db.scalars(sa.select(AuditLog))])
            self.assertNotIn(OFFICIAL_URL, audit_text)
            self.assertNotIn("a" * 64, audit_text)

    async def test_identity_scope_controls_private_link_in_summary_and_result(self):
        session_id, _ = await self.prepared()
        for scopes, expected in ((("sessions:read",), "LINK_RESTRICTED"),
                                 (("sessions:read", "results:identity"), "LINK_AVAILABLE")):
            headers, _ = self.make_key(scopes)
            for suffix in ("government-verification", "result"):
                with self.subTest(scopes=scopes, suffix=suffix):
                    code, response, _ = await self.request(f"/v1/kyc/{session_id}/{suffix}", headers)
                    self.assertEqual(code, 200, response)
                    body = response if suffix == "government-verification" else response["government_verification"]
                    self.assertEqual(body["status"], expected)
                    self.assertEqual(body["verification_url"], OFFICIAL_URL if expected == "LINK_AVAILABLE" else None)
                    self.assertFalse(body["verified"])
                    if expected == "LINK_RESTRICTED":
                        self.assertNotIn("a" * 64, json.dumps(response))

    async def test_applicant_token_only_opens_its_own_government_record(self):
        session_id, _ = await self.prepared()
        other_id, _ = await self.prepared(OFFICIAL_URL.replace("government-record-fixture", "other-record"))
        code, issued, _ = await self.request(f"/v1/kyc/{session_id}/client-token", self.headers, "POST", {})
        self.assertEqual(code, 201, issued)
        device = {"Authorization": f"Bearer {issued['client_token']}", "X-Organization-ID": str(self.org)}
        code, body, _ = await self.government(session_id, device)
        self.assertEqual((code, body["verification_url"]), (200, OFFICIAL_URL))
        self.assertEqual((await self.government(other_id, device))[0], 401)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}/result", device))[0], 403)
        self.assertEqual((await self.government(session_id, {**device, "X-Organization-ID": str(self.other_org)}))[0], 401)

    async def test_cross_tenant_record_is_not_visible(self):
        session_id, _ = await self.prepared()
        foreign, _ = self.make_key(("sessions:read", "results:identity"), organization=self.other_org)
        code, body, _ = await self.government(session_id, foreign)
        self.assertEqual(code, 404, body)
        self.assertNotIn(OFFICIAL_URL, json.dumps(body))

    async def test_untrusted_url_is_not_exposed_as_a_government_link(self):
        session_id, _ = await self.prepared(OFFICIAL_URL.replace("verify.gov.kh", "verify.gov.kh.evil.test"))
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "NO_OFFICIAL_QR", None))
        self.assertFalse(body["verified"])

    async def test_unreadable_payload_fails_closed(self):
        session_id, document_id = await self.prepared()
        with Session(self.engine) as db, db.begin():
            row = db.scalar(sa.select(BarcodeResult).where(BarcodeResult.document_id == document_id))
            row.payload_ciphertext = b"invalid-encrypted-payload"
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "UNAVAILABLE", None))
        self.assertFalse(body["verified"])

    async def test_erasure_removes_the_government_link(self):
        session_id, _ = await self.prepared()
        code, body, _ = await self.request(f"/v1/kyc/{session_id}/erase", self.headers, "POST", {})
        self.assertEqual(code, 200, body)
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "ERASED", None))
        self.assertNotIn("a" * 64, json.dumps(body))

    async def test_old_scan_link_is_hidden_during_recapture(self):
        session_id, document_id = await self.prepared()
        with Session(self.engine) as db, db.begin():
            db.get(IdentityDocument, document_id).processed_at = None
            db.get(KYCSession, session_id).status = SessionStatus.DOCUMENT_REQUIRED
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "PENDING", None))

    async def test_expired_scan_cannot_expose_the_private_record_link(self):
        session_id, document_id = await self.prepared()
        with Session(self.engine) as db, db.begin():
            db.get(IdentityDocument, document_id).delete_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.government(session_id)
        self.assertEqual((code, body["status"], body["verification_url"]), (200, "LINK_EXPIRED", None))
        self.assertNotIn("a" * 64, json.dumps(body))

    async def test_reviewer_identity_permission_controls_government_link(self):
        session_id, _ = await self.prepared()
        for role, expected in (("REVIEWER", "LINK_AVAILABLE"), ("AUDITOR", "LINK_RESTRICTED")):
            token, digest = new_token()
            with Session(self.engine) as db, db.begin():
                db.add(Reviewer(organization_id=self.org, display_name="Test staff", role=role, token_sha256=digest))
            headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": str(self.org)}
            code, body, _ = await self.request(f"/v1/review/{session_id}", headers)
            self.assertEqual(code, 200, body)
            government = body["government_verification"]
            self.assertEqual(government["status"], expected)
            self.assertEqual(government["verification_url"], OFFICIAL_URL if role == "REVIEWER" else None)
            if role == "AUDITOR":
                self.assertNotIn("a" * 64, json.dumps(body))

    async def test_official_qr_link_cannot_make_a_document_only_session_verified(self):
        session_id, document_id = await self.prepared()
        with Session(self.engine) as db, db.begin():
            for check_type in ("CAPTURE_QUALITY", "CLASSIFICATION", "REQUIRED_FIELDS", "DOCUMENT_NUMBER_FORMAT"):
                db.add(DocumentCheck(organization_id=self.org, session_id=session_id, document_id=document_id,
                                     check_type=check_type, result=CheckResult.PASS,
                                     evidence_metadata={"reason_codes": []}))
            for check_type in ("EXPIRY", "BARCODE", "MRZ"):
                db.add(DocumentCheck(organization_id=self.org, session_id=session_id, document_id=document_id,
                                     check_type=check_type, result=CheckResult.NOT_APPLICABLE,
                                     evidence_metadata={"reason_codes": []}))
        code, body, _ = await self.request(f"/v1/kyc/{session_id}/verify", self.headers, "POST", {})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "MANUAL_REVIEW")
        self.assertIn("DOCUMENT_AUTHENTICITY_UNVERIFIED", body["decision"]["reason_codes"])
        self.assertFalse((await self.government(session_id))[1]["verified"])
