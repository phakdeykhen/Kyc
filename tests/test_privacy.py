"""Phase 17 privacy controls: document-processing consent, data-subject erasure and key rotation."""

import base64
from datetime import datetime, timedelta, timezone
import os
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kyc.core.crypto import FieldCipher
from kyc.db.models import (AuditLog, BarcodeResult, BiometricTemplate, Consent, DocumentField, DocumentImage, IdentityDocument,
                           KYCSession, ManualReview, WebhookDelivery, WebhookEndpoint)
from kyc.services import tenancy
from kyc.api.dependencies import TenantContext
from kyc.services.consent import record_document_consent
from kyc.services.erasure import erase_session
from kyc.services.retention import purge_organization
from kyc.services.rekey import Keyrings, rekey_organization, retired_versions_in_use
from kyc.storage.biometrics import BiometricCipher
from kyc.storage.captures import LocalEncryptedCaptureStore, parse_keyring
from kyc.webhooks.dispatcher import endpoint_secrets
from kyc.webhooks.secrets import WebhookSecretCipher
from tests.helpers import call, multipart
from tests.test_capture_store import keyring
from tests.test_webhooks import WebhookCase


def add_template(engine, cipher, organization_id, session_id):
    template_id = uuid4()
    context = f"biometric/{organization_id}/{session_id}/{template_id}/LIVE_SELFIE/sface/2021dec/{'a' * 64}"
    sealed, version = cipher.seal(b"\x01" * 512, context)
    with Session(engine) as db, db.begin():
        db.add(BiometricTemplate(id=template_id, organization_id=organization_id, session_id=session_id,
                                 source="LIVE_SELFIE", model_name="sface", model_version="2021dec", model_sha256="a" * 64,
                                 embedding_dimension=128, template_ciphertext=sealed, key_version=version,
                                 delete_after=datetime.now(timezone.utc) + timedelta(hours=1)))
    return template_id, context


class DocumentConsentTests(WebhookCase):
    async def upload(self, session_id, headers):
        raw, content_type = multipart({"side": "DATA_PAGE"}, [("file", "page.jpg", "image/jpeg", b"not really an image")])
        return await call(self.app, f"/v1/kyc/{session_id}/documents", "POST", raw=raw, content_type=content_type,
                          headers=headers)

    async def test_uploads_wait_for_consent_given_from_the_device(self):
        self.app.state.settings.require_document_consent = True
        created = await self.create_session()
        session_id = created["session_id"]
        code, body, _ = await self.upload(session_id, self.headers)
        self.assertEqual((code, body["reason_code"]), (422, "DOCUMENT_CONSENT_REQUIRED"))
        self.assertEqual(self.count(DocumentImage), 0, "Nothing is stored before consent")
        code, token, _ = await call(self.app, f"/v1/kyc/{session_id}/client-token", "POST", body={}, headers=self.headers)
        device = {"Authorization": f"Bearer {token['client_token']}", "X-Organization-ID": str(self.org)}
        code, consent, _ = await call(self.app, f"/v1/kyc/{session_id}/consent", "POST",
                                      body={"scope": "DOCUMENT_PROCESSING", "granted": True}, headers=device)
        self.assertEqual(code, 201, consent)
        self.assertEqual(consent["policy_version"], self.settings.document_consent_policy_version)
        code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/consent", "POST",
                                body={"scope": "DOCUMENT_PROCESSING", "granted": True}, headers=device)
        self.assertEqual(code, 200, "Repeating consent is harmless")
        code, body, _ = await self.upload(session_id, device)
        self.assertNotEqual(body.get("reason_code"), "DOCUMENT_CONSENT_REQUIRED", body)
        for refused in ({"scope": "DOCUMENT_PROCESSING", "granted": False}, {"scope": "MARKETING", "granted": True},
                        {"granted": 1}, {"granted": "true"}):
            code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/consent", "POST", body=refused, headers=device)
            self.assertEqual(code, 422)
        with Session(self.engine) as db:
            rows = db.scalars(sa.select(Consent).where(Consent.session_id == UUID(session_id))).all()
            self.assertEqual([(row.scope, row.granted, row.revoked_at) for row in rows], [("DOCUMENT_PROCESSING", True, None)])
            actions = db.scalars(sa.select(AuditLog.action).where(AuditLog.session_id == UUID(session_id))).all()
            self.assertEqual(actions.count("DOCUMENT_CONSENT_GRANTED"), 1)

    async def test_policy_change_requires_a_new_consent(self):
        self.settings.require_document_consent = True
        session_id = (await self.create_session())["session_id"]
        body = {"scope": "DOCUMENT_PROCESSING", "granted": True}
        code, initial, _ = await call(self.app, f"/v1/kyc/{session_id}/consent", "POST", body, self.headers)
        self.assertEqual(code, 201, initial)
        self.settings.document_consent_policy_version = "DOCUMENT-CONSENT-UPDATED"
        code, rejected, _ = await self.upload(session_id, self.headers)
        self.assertEqual((code, rejected["reason_code"]), (422, "DOCUMENT_CONSENT_REQUIRED"))
        self.assertEqual(self.count(DocumentImage), 0)
        code, current, _ = await call(self.app, f"/v1/kyc/{session_id}/consent", "POST", body, self.headers)
        self.assertEqual((code, current["policy_version"]), (201, "DOCUMENT-CONSENT-UPDATED"))
        code, rejected, _ = await self.upload(session_id, self.headers)
        self.assertNotEqual(rejected.get("reason_code"), "DOCUMENT_CONSENT_REQUIRED", rejected)
        with Session(self.engine) as db:
            rows = db.scalars(sa.select(Consent).where(Consent.session_id == UUID(session_id))
                              .order_by(Consent.created_at)).all()
            self.assertEqual(len(rows), 2)
            self.assertIsNotNone(rows[0].revoked_at)
            self.assertIsNone(rows[1].revoked_at)

    async def test_consent_service_rejects_a_foreign_or_expired_session(self):
        session_id = UUID((await self.create_session())["session_id"])
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            for actor in (TenantContext(self.other_org), TenantContext(self.org, session_id=uuid4())):
                with self.assertRaises(HTTPException) as foreign:
                    record_document_consent(db, actor, record, "v1", uuid4())
                self.assertEqual(foreign.exception.status_code, 404)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with self.assertRaises(HTTPException) as expired:
                record_document_consent(db, TenantContext(self.org), record, "v1", uuid4())
            self.assertEqual(expired.exception.status_code, 409)
            # Restore the timestamp so the test fixture's expiry constraint still holds.
            record.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertEqual(self.count(Consent), 0)

    async def test_without_enforcement_uploads_need_no_consent(self):
        created = await self.create_session()
        code, body, _ = await self.upload(created["session_id"], self.headers)
        self.assertNotEqual(body.get("reason_code"), "DOCUMENT_CONSENT_REQUIRED", body)

    async def create_session(self):
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST",
                                   {"user_id": "consent-user", "country": "KH", "expected_document_type": "KH_PASSPORT",
                                    "verification_level": "DOCUMENT_ONLY"}, self.headers)
        self.assertEqual(code, 201, body)
        return body


class ErasureTests(WebhookCase):
    async def test_erasure_clears_review_notes_and_device_credentials_but_keeps_coded_history(self):
        session_id = await self.in_review()
        code, token, _ = await call(self.app, f"/v1/kyc/{session_id}/client-token", "POST", {}, self.headers)
        self.assertEqual(code, 201, token)
        code, decision, _ = await self.decide(session_id, action="REJECT", reason_code="IDENTITY_MISUSE_SUSPECTED",
                                             note="Customer SOK SOPHEA lives at a private home address.")
        self.assertEqual(code, 200, decision)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            record.idempotency_key = "customer-sok-sophea-sensitive-ref"
            record.request_fingerprint = "f" * 64
            for scope in ("DOCUMENT_PROCESSING", "BIOMETRIC_VERIFICATION"):
                db.add(Consent(organization_id=self.org, session_id=session_id, user_id=record.user_id,
                               scope=scope, policy_version="v1", granted=True))
        code, erased, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", {}, self.headers)
        self.assertEqual((code, erased["status"], erased["deleted"]["review_notes"]), (200, "REJECTED", 1))
        with Session(self.engine) as db:
            record = db.get(KYCSession, session_id)
            self.assertIsNone(record.idempotency_key)
            self.assertIsNone(record.request_fingerprint)
            self.assertIsNone(record.client_token_sha256)
            consents = list(db.scalars(sa.select(Consent).where(Consent.session_id == session_id)))
            self.assertEqual(len(consents), 2)
            self.assertTrue(all(row.user_id == "ERASED" and row.revoked_at is not None for row in consents))
            note = db.scalar(sa.select(ManualReview).where(ManualReview.session_id == session_id))
            self.assertEqual((note.reason_ciphertext, note.key_version), (None, None))
            self.assertEqual(note.reason_code, "IDENTITY_MISUSE_SUSPECTED")
        code, case, _ = await call(self.app, f"/v1/review/{session_id}", headers=self.reviewer)
        self.assertEqual(code, 200, case)
        self.assertEqual(case["history"][0]["reason_code"], "IDENTITY_MISUSE_SUSPECTED")
        self.assertIsNone(case["history"][0]["note"])
        self.assertEqual((case["fields"], case["images"], case["decision_options"]), ([], [], {}))
        device = {"Authorization": f"Bearer {token['client_token']}", "X-Organization-ID": str(self.org)}
        self.assertEqual((await call(self.app, f"/v1/kyc/{session_id}", headers=device))[0], 401)
        self.assertEqual((await call(self.app, f"/v1/kyc/{session_id}/client-token", "POST", {}, self.headers))[0], 409)

    async def test_commit_failure_leaves_database_and_objects_intact(self):
        session_id = await self.in_review()
        with Session(self.engine) as db:
            refs = list(db.scalars(sa.select(DocumentImage.encrypted_object_ref)
                                   .where(DocumentImage.session_id == session_id)))

        # Fail before the driver commit. An engine commit-event exception would leave
        # SQLite's shared test connection in a pending transaction after SQLAlchemy
        # has already marked that transaction closed, unlike this failed API commit.
        with patch.object(Session, "commit", side_effect=RuntimeError("simulated database commit failure")), \
                patch.object(self.app.state.capture_store, "delete") as delete, \
                self.assertLogs("kyc.api", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "simulated database commit failure"):
                await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", {}, self.headers)
            delete.assert_not_called()
        with Session(self.engine) as db:
            record = db.get(KYCSession, session_id)
            self.assertIsNone(record.erased_at)
            self.assertEqual(record.status.value, "MANUAL_REVIEW")
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(IdentityDocument)
                                       .where(IdentityDocument.session_id == session_id)), 1)
        self.assertTrue(all(self.app.state.capture_store._path(ref).exists() for ref in refs))

    async def test_storage_failure_is_observable_and_orphan_sweep_retries(self):
        session_id = await self.in_review()
        store = self.app.state.capture_store
        with Session(self.engine) as db:
            refs = list(db.scalars(sa.select(DocumentImage.encrypted_object_ref)
                                   .where(DocumentImage.session_id == session_id)))

        def fail_delete(ref):
            with Session(self.engine) as db:
                self.assertIsNotNone(db.get(KYCSession, session_id).erased_at, "Storage work must follow the commit")
            raise OSError("sensitive-storage-reference-must-not-be-logged")

        with patch.object(store, "delete", side_effect=fail_delete), self.assertLogs("kyc.privacy", level="WARNING") as logged:
            code, erased, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", {}, self.headers)
        self.assertEqual(code, 200, erased)
        self.assertTrue(all(store._path(ref).exists() for ref in refs))
        self.assertIn(f"failed_objects={len(refs)}", logged.output[0])
        self.assertNotIn("sensitive-storage-reference", logged.output[0])
        report = purge_organization(self.app.state.session_factory, store, self.org,
                                    now=datetime.now(timezone.utc) + timedelta(minutes=16))
        self.assertEqual(report.deleted_objects, len(refs))
        self.assertFalse(any(store._path(ref).exists() for ref in refs))

    async def test_erasure_service_rejects_foreign_terminal_session(self):
        session_id = await self.in_review()
        code, decision, _ = await self.decide(session_id, action="REJECT", reason_code="IDENTITY_MISUSE_SUSPECTED")
        self.assertEqual(code, 200, decision)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            for actor in (TenantContext(self.other_org), TenantContext(self.org, session_id=uuid4())):
                with self.assertRaises(HTTPException) as foreign:
                    erase_session(db, actor, record, uuid4())
                self.assertEqual(foreign.exception.status_code, 404)
            self.assertIsNone(record.erased_at)

    async def test_erasure_removes_personal_data_and_keeps_the_decision(self):
        await self.endpoint()
        session_id = await self.in_review()
        _, template_context = add_template(self.engine, self.app.state.biometric_cipher, self.org, session_id)
        with Session(self.engine) as db:
            refs = db.scalars(sa.select(DocumentImage.encrypted_object_ref).where(DocumentImage.session_id == session_id)).all()
        self.assertTrue(refs and all(self.app.state.capture_store._path(ref).exists() for ref in refs))
        code, before, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(before["identity"]["full_name"], "SOK SOPHEA")

        code, report, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", body={}, headers=self.headers)
        self.assertEqual(code, 200, report)
        self.assertEqual((report["status"], report["already_erased"]), ("EXPIRED", False), "An open case is closed first")
        self.assertGreaterEqual(report["deleted"]["identity_documents"], 1)
        self.assertEqual(report["deleted"]["biometric_templates"], 1)
        self.assertFalse(any(self.app.state.capture_store._path(ref).exists() for ref in refs), "Objects deleted")
        with Session(self.engine) as db:
            for model in (IdentityDocument, DocumentField, DocumentImage, BiometricTemplate):
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(model)
                                           .where(model.session_id == session_id)), 0, model.__tablename__)
            record = db.get(KYCSession, session_id)
            self.assertEqual(record.user_id, "ERASED")
            self.assertIsNotNone(record.erased_at)
            self.assertIsNone(record.client_token_sha256)
            for delivery in db.scalars(sa.select(WebhookDelivery).where(WebhookDelivery.session_id == session_id)):
                self.assertEqual(delivery.payload["data"]["user_id"], "ERASED")
            actions = db.scalars(sa.select(AuditLog.action).where(AuditLog.session_id == session_id)).all()
            self.assertIn("SESSION_DATA_ERASED", actions)
            self.assertIn("SESSION_EXPIRED", actions)
        code, after, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertIsNone(after["identity"])
        self.assertIsNotNone(after["erased_at"])
        self.assertEqual(after["decision"]["result"], "REVIEW", "The decision and its reason codes remain")
        code, again, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", body={}, headers=self.headers)
        self.assertEqual((code, again["already_erased"]), (200, True))

    async def test_erasure_needs_its_scope_and_stays_inside_the_organization(self):
        session_id = await self.in_review()
        with Session(self.engine) as db, db.begin():
            _, token = tenancy.create_key(db, self.org, "reader", ["sessions:read", "sessions:write"], "test", uuid4())
            _, foreign = tenancy.create_key(db, self.other_org, "eraser", ["data:erase"], "test", uuid4())
        code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", body={},
                                headers={"X-API-Key": token, "X-Organization-ID": str(self.org)})
        self.assertEqual(code, 403)
        code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/erase", "POST", body={},
                                headers={"X-API-Key": foreign, "X-Organization-ID": str(self.other_org)})
        self.assertEqual(code, 404)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(KYCSession, session_id).erased_at)


class KeyRotationTests(WebhookCase):
    async def test_every_data_class_moves_to_the_new_key_and_old_keys_can_be_removed(self):
        session_id = await self.in_review()
        code, decided, _ = await self.decide(session_id, action="REQUEST_RECAPTURE", reason_code="DOCUMENT_UNREADABLE")
        self.assertEqual(code, 200, decided)
        session_id = await self.in_review()
        template_id, template_context = add_template(self.engine, self.app.state.biometric_cipher, self.org, session_id)
        with Session(self.engine) as db, db.begin():
            document_id = db.scalar(sa.select(IdentityDocument.id).where(IdentityDocument.session_id == session_id))
            barcode_context = f"barcode/{self.org}/{session_id}/{document_id}/BACK/QR_CODE"
            payload, version = self.cipher.seal(base64.b64encode(b"SPECIMEN|PAYLOAD").decode(), barcode_context)
            db.add(BarcodeResult(organization_id=self.org, session_id=session_id, document_id=document_id,
                                 symbology="QR_CODE", decoded=True, data_consistency={"side": "BACK"},
                                 payload_ciphertext=payload, key_version=version))
        created = await self.endpoint()   # sealed with the PII keyring (no webhook keyring yet)
        factory = sessionmaker(self.engine, expire_on_commit=False)

        old_pii = self.cipher
        new_pii = FieldCipher(f"{keyring('pii-test-v2')},pii-test-v1:{base64.b64encode(old_pii.keys['pii-test-v1']).decode()}",
                              old_pii.hmac_key)
        old_bio = self.app.state.biometric_cipher
        new_bio = BiometricCipher(f"{keyring('biometric-test-v2')},biometric-test-v1:"
                                  f"{base64.b64encode(old_bio.keys['biometric-test-v1']).decode()}")
        old_store = self.app.state.capture_store
        active, keys = parse_keyring(f"{keyring('capture-test-v2')},capture-test-v1:"
                                     f"{base64.b64encode(old_store.keys['capture-test-v1']).decode()}")
        new_store = LocalEncryptedCaptureStore(old_store.root, active, keys)
        webhooks = WebhookSecretCipher(keyring("webhook-test-v1"), new_pii)
        rings = Keyrings(new_pii, new_bio, webhooks, new_store)

        before = rekey_organization(factory, self.org, rings, apply=False)
        self.assertTrue(all(retired_versions_in_use(before).values()), retired_versions_in_use(before))
        changed = rekey_organization(factory, self.org, rings, apply=True)
        self.assertTrue(all(report.resealed for report in changed.values()), {k: v.view() for k, v in changed.items()})
        after = rekey_organization(factory, self.org, rings, apply=False)
        # Recapture left the replaced image orphaned. Its key remains required until
        # the retention sweep removes it, even though all live captures were rotated.
        self.assertEqual(retired_versions_in_use(after),
                         {name: ["capture-test-v1"] if name == "capture_objects" else [] for name in after})
        self.assertEqual(after["capture_objects"].unreferenced, {"capture-test-v1": 1})
        purged = purge_organization(factory, new_store, self.org,
                                    now=datetime.now(timezone.utc) + timedelta(minutes=16))
        self.assertEqual(purged.deleted_objects, 1)
        after = rekey_organization(factory, self.org, rings, apply=False)
        self.assertEqual(retired_versions_in_use(after), {name: [] for name in after})

        # Old keys removed: everything still opens with the new keys alone.
        only_pii = FieldCipher(f"pii-test-v2:{base64.b64encode(new_pii.keys['pii-test-v2']).decode()}", old_pii.hmac_key)
        only_bio = BiometricCipher(f"biometric-test-v2:{base64.b64encode(new_bio.keys['biometric-test-v2']).decode()}")
        only_store = LocalEncryptedCaptureStore(old_store.root, "capture-test-v2", {"capture-test-v2": new_store.keys["capture-test-v2"]})
        with Session(self.engine) as db:
            for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == self.org)):
                value = only_pii.open(field.normalized_value_ciphertext, field.key_version,
                                      f"field/{self.org}/{field.session_id}/{field.document_id}/{field.field_name}/normalized")
                self.assertEqual(value, "SOK SOPHEA")
            note = db.scalar(sa.select(ManualReview).where(ManualReview.organization_id == self.org))
            self.assertIn("video call", only_pii.open(note.reason_ciphertext, note.key_version,
                                                      f"review/{self.org}/{note.session_id}/{note.id}/note"))
            barcode = db.scalar(sa.select(BarcodeResult).where(BarcodeResult.organization_id == self.org,
                                                               BarcodeResult.symbology == "QR_CODE"))
            self.assertEqual(base64.b64decode(only_pii.open(barcode.payload_ciphertext, barcode.key_version, barcode_context)),
                             b"SPECIMEN|PAYLOAD")
            template = db.get(BiometricTemplate, template_id)
            self.assertEqual(only_bio.open(template.template_ciphertext, template.key_version, template_context), b"\x01" * 512)
            for image in db.scalars(sa.select(DocumentImage).where(DocumentImage.organization_id == self.org)):
                self.assertEqual(image.key_version, "capture-test-v2")
                only_store.get(image.encrypted_object_ref, self.org, image.session_id, image.id)
            endpoint = db.get(WebhookEndpoint, UUID(created["id"]))
            self.assertTrue(endpoint.key_version.startswith("wh:"))
            only_webhooks = WebhookSecretCipher(None, None)
            only_webhooks.dedicated = webhooks.dedicated
            self.assertEqual(endpoint_secrets(only_webhooks, endpoint, datetime.now(timezone.utc)), [created["secret"]])

    async def test_inventory_does_not_change_anything(self):
        await self.in_review()
        with Session(self.engine) as db:
            before = [(row.id, row.normalized_value_ciphertext) for row in db.scalars(sa.select(DocumentField))]
        old_key = base64.b64encode(self.cipher.keys["pii-test-v1"]).decode()
        rotated = FieldCipher(f"{keyring('pii-new')},pii-test-v1:{old_key}", os.urandom(32))
        reports = rekey_organization(sessionmaker(self.engine), self.org, Keyrings(rotated), apply=False)
        self.assertEqual(reports["document_fields"].resealed, 0)
        self.assertEqual(reports["biometric_templates"].skipped, "BIOMETRIC_ENCRYPTION_KEYS not configured")
        with Session(self.engine) as db:
            self.assertEqual(before, [(row.id, row.normalized_value_ciphertext) for row in db.scalars(sa.select(DocumentField))])
