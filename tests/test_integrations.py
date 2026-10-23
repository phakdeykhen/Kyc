"""Synthetic security/contract tests; these do not measure KYC or biometric accuracy."""
import base64
from datetime import timedelta
from pathlib import Path
import unittest
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.core.crypto import FieldCipher
from kyc.db.models import (Application, DocumentCheck, DocumentField, ExternalSubject, IdentityDocument, IntegrationFaceSession,
    IntegrationUsage, KYCSession, Organization, RegisteredDevice, VerificationBinding, VerificationDecision, WebhookDelivery)
from kyc.domain.state_machine import Event
from kyc.integrations import service
from kyc.services.sessions import apply_event
from kyc.services.tenancy import create_key
from kyc.webhooks.secrets import WebhookSecretCipher
from tests.helpers import call
from tests import test_api
from tests.test_capture_store import keyring


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_api.SessionAPITests.asyncSetUp(self)
        self.app.state.field_cipher = FieldCipher(keyring("integration-test"), b"x" * 32)
        self.app.state.webhook_cipher = WebhookSecretCipher(None, self.app.state.field_cipher)
        self.config = {"name": "Synthetic HR", "mode": "sandbox", "branding": {"display_name": "Synthetic School"},
            "workflows": {"staff-onboarding": {"country": "KH", "expected_document_type": "KH_NATIONAL_ID"}},
            "return_urls": ["https://1.1.1.1/return"], "identity_fields": ["full_name"], "check_fields": ["expiry"],
            "populations": ["STAFF"], "purposes": ["self_profile", "staff_lookup"], "profile_fields": ["kyc_status"]}
        self.body = {"external_subject_id": "EMP-00123", "workflow_id": "staff-onboarding", "reference": "onboarding-456",
                     "return_url": "https://1.1.1.1/return", "locale": "km"}
        self.application_id, self.client = await self.make_app()

    asyncTearDown = test_api.SessionAPITests.asyncTearDown

    async def make_app(self, organization=None, **changes):
        headers = self.headers
        if organization and organization != self.org:
            with Session(self.engine) as db, db.begin():
                _, token = create_key(db, organization, "admin", list(service.APPLICATION_SCOPES | {"applications:manage"}), "test", uuid4())
            headers = {"X-API-Key": token, "X-Organization-ID": str(organization)}
        code, app, _ = await call(self.app, "/v1/applications", "POST", self.config | changes, headers)
        self.assertEqual(code, 201, app)
        code, credential, _ = await call(self.app, f"/v1/applications/{app['id']}/credentials", "POST",
            {"name": "synthetic backend", "scopes": sorted(service.APPLICATION_SCOPES)}, headers)
        self.assertEqual(code, 201, credential)
        return UUID(app["id"]), {"X-API-Key": credential["api_key"], "X-Organization-ID": headers["X-Organization-ID"]}

    async def create(self, headers=None, extra=None, body=None):
        code, row, _ = await call(self.app, "/v1/verification-sessions", "POST", body or self.body,
                                 (headers or self.client) | (extra or {}))
        self.assertIn(code, (200, 201), row)
        return row

    async def test_creation_capture_context_and_branded_host(self):
        row = await self.create()
        self.assertTrue(row["sandbox"])
        self.assertTrue(row["capture_token"].startswith("kst_"))
        self.assertIn("#token=", row["hosted_verification_url"])
        self.assertNotIn(self.client["X-API-Key"], str(row))
        capture = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + row["capture_token"]}
        code, context, _ = await call(self.app, f"/v1/verification-sessions/{row['session_id']}/capture-context", headers=capture)
        self.assertEqual(code, 200, context)
        self.assertEqual(context["locale"], "km")
        self.assertEqual(context["required_sides"], ["FRONT", "BACK"])
        code, html, response = await call(self.app, f"/verify/{row['session_id']}")
        self.assertEqual(code, 200)
        self.assertNotIn(b'API key', html)
        self.assertIn("camera=(self)", response["permissions-policy"])
        self.assertIn("script-src 'self'", response["content-security-policy"])
        self.assertEqual((await call(self.app, "/verify-assets/index.html"))[0], 404)

    async def test_same_external_id_in_different_tenants_and_application_isolation(self):
        _, other = await self.make_app(self.other_org)
        _, sibling = await self.make_app()
        first, second = await self.create(), await self.create(other)
        with Session(self.engine) as db:
            rows = list(db.scalars(sa.select(ExternalSubject).where(ExternalSubject.external_id == "EMP-00123")))
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0].id, rows[1].id)
        for denied in (other, sibling):
            for suffix in ("", "/result"):
                self.assertEqual((await call(self.app, f"/v1/verification-sessions/{first['session_id']}{suffix}", headers=denied))[0], 404)
        self.assertNotEqual(first["session_id"], second["session_id"])

    async def test_application_keys_cannot_use_legacy_or_admin_routes(self):
        row = await self.create()
        for path in ("/v1/api-keys", "/v1/applications", "/v1/webhooks", f"/v1/kyc/{row['session_id']}/result"):
            self.assertEqual((await call(self.app, path, headers=self.client))[0], 403)

    async def test_workflow_callback_and_arbitrary_webhook_rejected(self):
        for changes in ({"workflow_id": "other"}, {"return_url": "https://1.1.1.1/return?next=evil"},
                        {"return_url": "https://169.254.169.254/"}, {"webhook_url": "https://1.1.1.1/hook"}):
            code, _, _ = await call(self.app, "/v1/verification-sessions", "POST", self.body | changes, self.client)
            self.assertEqual(code, 422)
        for url in ("https://127.0.0.1/", "https://169.254.169.254/latest/meta-data", "http://1.1.1.1/", "https://user@1.1.1.1/", "https://1.1.1.1/\r\nX:x"):
            code, _, _ = await call(self.app, "/v1/applications", "POST", self.config | {"return_urls": [url]}, self.headers)
            self.assertEqual(code, 422, url)

    async def test_idempotency_is_application_scoped_and_rotates_capture_token(self):
        idem = {"Idempotency-Key": "create-attempt-456"}
        first, again = await self.create(extra=idem), await self.create(extra=idem)
        self.assertEqual(first["session_id"], again["session_id"])
        self.assertNotEqual(first["capture_token"], again["capture_token"])
        stale = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + first["capture_token"]}
        self.assertEqual((await call(self.app, f"/v1/kyc/{first['session_id']}", headers=stale))[0], 401)
        code, _, _ = await call(self.app, "/v1/verification-sessions", "POST", self.body | {"reference": "changed"}, self.client | idem)
        self.assertEqual(code, 409)
        _, sibling = await self.make_app()
        self.assertNotEqual((await self.create(sibling, idem))["session_id"], first["session_id"])

    async def test_capture_cannot_read_authoritative_result_or_other_sessions(self):
        first, second = await self.create(), await self.create()
        capture = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + first["capture_token"]}
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{first['session_id']}/result", headers=capture))[0], 403)
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{second['session_id']}/capture-context", headers=capture))[0], 401)
        with Session(self.engine) as db, db.begin():
            link = db.scalar(sa.select(VerificationBinding).where(VerificationBinding.session_id == UUID(first["session_id"])))
            link.capture_expires_at = service.utcnow() - timedelta(seconds=1)
            db.get(KYCSession, UUID(first["session_id"])).status = "MANUAL_REVIEW"
        self.assertEqual((await call(self.app, f"/v1/kyc/{first['session_id']}", headers=capture))[0], 401)

    async def test_expiry_persists_final_result_and_outbox(self):
        row = await self.create()
        endpoint = await self.endpoint()
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, UUID(row["session_id"]))
            record.created_at = service.utcnow() - timedelta(hours=1)
            record.expires_at = service.utcnow() - timedelta(seconds=1)
        code, result, _ = await call(self.app, f"/v1/verification-sessions/{row['session_id']}/result", headers=self.client)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["outcome"], "EXPIRED")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(VerificationDecision)), 1)
            deliveries = list(db.scalars(sa.select(WebhookDelivery)))
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0].payload["data"]["result_version"], result["result_version"])

    async def endpoint(self, app=None):
        code, row, _ = await call(self.app, f"/v1/applications/{app or self.application_id}/webhooks", "POST",
            {"url": "https://1.1.1.1/webhook"}, self.headers)
        self.assertEqual(code, 201, row)
        return row

    async def test_decision_and_application_webhook_atomicity_and_isolation(self):
        await self.endpoint()
        sibling, _ = await self.make_app()
        await self.endpoint(sibling)
        row = await self.create()
        try:
            with Session(self.engine) as db, db.begin():
                record = db.get(KYCSession, UUID(row["session_id"]))
                apply_event(db, record, TenantContext(self.org), Event.EXPIRE, uuid4())
                db.commit()
        except Exception:
            raise
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(VerificationDecision)), 1)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(WebhookDelivery)), 1)
        second = await self.create()
        with Session(self.engine) as db:
            record = db.get(KYCSession, UUID(second["session_id"]))
            apply_event(db, record, TenantContext(self.org), Event.EXPIRE, uuid4())
            db.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(VerificationDecision)), 1)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(WebhookDelivery)), 1)

    async def test_scopes_and_disclosure_policy_both_limit_preserved_results(self):
        row = await self.create()
        session_id = UUID(row["session_id"])
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            doc = IdentityDocument(organization_id=self.org, session_id=session_id, document_type="KH_NATIONAL_ID",
                                   processed_at=service.utcnow(), delete_after=service.utcnow() + timedelta(days=1))
            db.add(doc); db.flush()
            for name, value in (("full_name", "Synthetic Person"), ("date_of_birth", "2000-01-01")):
                context = f"field/{self.org}/{session_id}/{doc.id}/{name}/normalized"
                sealed, key = self.app.state.field_cipher.seal(value, context)
                db.add(DocumentField(organization_id=self.org, session_id=session_id, document_id=doc.id,
                    field_name=name, normalized_value_ciphertext=sealed, key_version=key, confidence=1))
            db.add(DocumentCheck(organization_id=self.org, session_id=session_id, document_id=doc.id,
                                  check_type="EXPIRY", result="PASS", evidence_metadata={}))
            db.flush()
            from kyc.domain.enums import SessionStatus
            record.status = SessionStatus.VERIFIED
            record.version += 1
            service.snapshot(db, record)
            _, token = create_key(db, self.org, "limited", ["sessions:read"], "test", uuid4(), application_id=self.application_id)
        path = f"/v1/verification-sessions/{session_id}/result"
        full = (await call(self.app, path, headers=self.client))[1]
        self.assertEqual(full["identity"], {"full_name": "Synthetic Person"})
        self.assertEqual(full["checks"], {"expiry": "PASS"})
        limited = (await call(self.app, path, headers={**self.client, "X-API-Key": token}))[1]
        self.assertEqual((limited["identity"], limited["checks"]), ({}, {}))
        with Session(self.engine) as db, db.begin():
            db.execute(sa.delete(IdentityDocument).where(IdentityDocument.session_id == session_id))
        self.assertEqual((await call(self.app, path, headers=self.client))[1]["identity"], full["identity"])
        with Session(self.engine) as db, db.begin():
            db.get(Application, self.application_id).identity_fields = []
        self.assertEqual((await call(self.app, path, headers=self.client))[1]["identity"], {})

    async def test_all_final_outcomes_are_explicit_and_production_cannot_simulate(self):
        for outcome in sorted(service.FINAL):
            row = await self.create()
            code, result, _ = await call(self.app, f"/v1/verification-sessions/{row['session_id']}/sandbox-result", "POST",
                {"outcome": outcome}, self.client)
            self.assertEqual(code, 200, result)
            self.assertEqual(result["outcome"], outcome)
            self.assertTrue(result["sandbox"])
        production, client = await self.make_app(mode="production")
        self.assertEqual((await call(self.app, "/v1/verification-sessions", "POST", self.body, client))[0], 503)
        self.app.state.settings.integration_kyc_production_enabled = True
        row = await self.create(client)
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{row['session_id']}/sandbox-result", "POST", {"outcome": "VERIFIED"}, client))[0], 403)

    async def test_quotas_rate_limits_revocation_and_usage_are_scoped(self):
        row = await self.create()
        with Session(self.engine) as db, db.begin():
            db.get(Application, self.application_id).daily_session_quota = 1
        self.assertEqual((await call(self.app, "/v1/verification-sessions", "POST", self.body, self.client))[0], 429)
        _, sibling = await self.make_app()
        await self.create(sibling)
        dashboard = (await call(self.app, "/v1/integration/usage", headers=self.client))[1]
        self.assertEqual(dashboard["sessions"], 1)
        with Session(self.engine) as db, db.begin():
            db.get(Application, self.application_id).active = False
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{row['session_id']}", headers=self.client))[0], 401)
        capture = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + row["capture_token"]}
        self.assertEqual((await call(self.app, f"/v1/kyc/{row['session_id']}", headers=capture))[0], 401)

    async def test_session_deletion_preserves_outcome_and_removes_identity(self):
        row = await self.create()
        await call(self.app, f"/v1/verification-sessions/{row['session_id']}/sandbox-result", "POST", {"outcome": "VERIFIED"}, self.client)
        code, _, _ = await call(self.app, f"/v1/verification-sessions/{row['session_id']}", "DELETE", headers=self.client)
        self.assertEqual(code, 200)
        result = (await call(self.app, f"/v1/verification-sessions/{row['session_id']}/result", headers=self.client))[1]
        self.assertEqual(result["outcome"], "VERIFIED")
        self.assertEqual(result["identity"], {})
        status = (await call(self.app, f"/v1/verification-sessions/{row['session_id']}", headers=self.client))[1]
        self.assertIsNone(status["reference"])
        self.assertIsNone(status["external_subject_id"])

    async def test_face_gate_is_independent_and_sandbox_cannot_enroll(self):
        for path in ("/v1/face-enrollment-sessions", "/v1/recognition-sessions"):
            code, _, _ = await call(self.app, path, "POST", {"purpose": "self_profile", "population": "STAFF"}, self.client)
            self.assertEqual(code, 403)
        _, client = await self.make_app(mode="production")
        self.app.state.settings.integration_kyc_production_enabled = True
        code, _, _ = await call(self.app, "/v1/recognition-sessions", "POST", {"purpose": "staff_lookup", "population": "STAFF", "device_id": str(uuid4())}, client)
        self.assertEqual(code, 503)

    async def test_device_revocation_and_capture_token_binding(self):
        code, device, _ = await call(self.app, f"/v1/applications/{self.application_id}/devices", "POST",
            {"name": "Authorized synthetic kiosk", "populations": ["STAFF"], "purposes": ["staff_lookup"]}, self.headers)
        self.assertEqual(code, 201, device)
        token = "fct_" + "a" * 43
        with Session(self.engine) as db, db.begin():
            face = IntegrationFaceSession(organization_id=self.org, application_id=self.application_id,
                device_id=UUID(device["device_id"]), kind="RECOGNITION", purpose="staff_lookup", population="STAFF",
                capture_token_hash=__import__("hashlib").sha256(token.encode()).hexdigest(), expires_at=service.utcnow() + timedelta(minutes=1))
            db.add(face); db.flush(); identifier = face.id
        capture = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + token, "X-Device-Token": device["device_token"]}
        path = f"/v1/recognition-sessions/{identifier}/capture"
        self.assertEqual((await call(self.app, path, "POST", {}, {**capture, "X-Device-Token": "invalid"}))[0], 403)
        self.assertEqual((await call(self.app, path, "POST", {}, {**capture, "X-Organization-ID": str(self.other_org)}))[0], 401)
        await call(self.app, f"/v1/applications/{self.application_id}/devices/{device['device_id']}", "DELETE", headers=self.headers)
        self.assertEqual((await call(self.app, path, "POST", {}, capture))[0], 403)

    async def test_application_scopes_and_credential_revocation(self):
        with Session(self.engine) as db, db.begin():
            row, secret = create_key(db, self.org, "reader", ["sessions:read"], "test", uuid4(), application_id=self.application_id)
            key_id = row.id
        limited = self.client | {"X-API-Key": secret}
        self.assertEqual((await call(self.app, "/v1/verification-sessions", "POST", self.body, limited))[0], 403)
        for path in ("/v1/face-enrollment-sessions", "/v1/recognition-sessions"):
            self.assertEqual((await call(self.app, path, "POST", {"purpose": "self_profile", "population": "STAFF"}, limited))[0], 403)
        row = await self.create()
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{row['session_id']}", "DELETE", headers=limited))[0], 403)
        await call(self.app, f"/v1/applications/{self.application_id}/credentials/{key_id}", "DELETE", headers=self.headers)
        self.assertEqual((await call(self.app, f"/v1/verification-sessions/{row['session_id']}", headers=limited))[0], 401)

    async def test_subject_deletion_respects_sibling_application_and_removes_last_reference(self):
        _, sibling = await self.make_app()
        first, second = await self.create(), await self.create(sibling)
        code, report, _ = await call(self.app, "/v1/integration/subjects/EMP-00123", "DELETE", headers=self.client)
        self.assertEqual(code, 200, report)
        self.assertFalse(report["subject_erased"])
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(KYCSession, UUID(first["session_id"])).erased_at)
            self.assertIsNone(db.get(KYCSession, UUID(second["session_id"])).erased_at)
        code, report, _ = await call(self.app, "/v1/integration/subjects/EMP-00123", "DELETE", headers=sibling)
        self.assertEqual(code, 200, report)
        self.assertTrue(report["subject_erased"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(ExternalSubject).where(ExternalSubject.external_id == "EMP-00123")), 0)

    async def test_technical_failure_has_a_durable_outcome_and_clear_capture_status(self):
        row = await self.create()
        with Session(self.engine) as db, db.begin():
            service.technical_failure(db, db.get(KYCSession, UUID(row["session_id"])))
        result = (await call(self.app, f"/v1/verification-sessions/{row['session_id']}/result", headers=self.client))[1]
        self.assertEqual(result["outcome"], "TECHNICAL_ERROR")
        capture = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + row["capture_token"]}
        code, status, _ = await call(self.app, f"/v1/verification-sessions/{row['session_id']}/capture-status", headers=capture)
        self.assertEqual((code, status["status"]), (200, "TECHNICAL_ERROR"))
        self.assertEqual((await call(self.app, f"/v1/kyc/{row['session_id']}/consent", "POST", {"scope": "DOCUMENT_PROCESSING", "granted": True}, capture))[0], 401)

    async def test_retry_redelivery_retains_event_id_and_attempt_history(self):
        from tests.test_webhooks import RecordingSender
        from kyc.webhooks.delivery import SendResult
        from kyc.webhooks.signing import verify
        endpoint = await self.endpoint()
        row = await self.create()
        await call(self.app, f"/v1/verification-sessions/{row['session_id']}/sandbox-result", "POST", {"outcome": "VERIFIED"}, self.client)
        dispatcher = self.app.state.webhook_dispatcher
        dispatcher.cipher = self.app.state.webhook_cipher
        dispatcher.sender = RecordingSender(SendResult(500, "HTTP_500"), SendResult(200, None), SendResult(200, None))
        self.assertEqual(dispatcher.deliver_due(self.org), 1)
        with Session(self.engine) as db:
            delivery = db.scalar(sa.select(WebhookDelivery))
            delivery_id, event_id = delivery.id, delivery.event_id
        self.assertEqual(dispatcher.deliver_due(self.org), 0, "Backoff must delay a failed delivery")
        self.assertEqual(dispatcher.deliver_due(self.org, service.utcnow() + timedelta(minutes=1)), 1)
        code, again, _ = await call(self.app, f"/v1/webhooks/{endpoint['id']}/deliveries/{delivery_id}/redeliver", "POST", {}, self.headers)
        self.assertEqual(code, 202, again)
        self.assertEqual(again["event_id"], str(event_id))
        self.assertEqual(dispatcher.deliver_due(self.org), 1)
        code, history, _ = await call(self.app, f"/v1/webhooks/{endpoint['id']}/deliveries/{delivery_id}/attempts", headers=self.headers)
        self.assertEqual(code, 200, history)
        self.assertEqual(len(history), 3)
        self.assertEqual(sorted(item["status_code"] for item in history), [200, 200, 500])
        for _, body, headers in dispatcher.sender.sent:
            verify(endpoint["secret"], body, headers["KYC-Signature"])
