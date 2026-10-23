"""Public recognition boundary with scripted faces only; no identification-accuracy claim."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.biometrics.embeddings import serialize_embedding
from kyc.db.models import BiometricTemplate, FaceCaptureSession, FaceConsent, KYCSession
from kyc.domain.enums import SessionStatus
from kyc.face_identity.ledger import DeletionLedger
from kyc.face_identity.service import now
from kyc.services.biometrics import _template_context
from kyc.storage.biometrics import BiometricCipher
from tests import test_integrations, test_face_identity
from tests.helpers import call, multipart
from tests.test_capture_store import keyring
from tests.test_liveness import real_head, unit


class PublicFaceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    make_app = test_integrations.IntegrationTests.make_app
    create = test_integrations.IntegrationTests.create

    async def asyncSetUp(self):
        await test_integrations.IntegrationTests.asyncSetUp(self)
        self.settings.face_recognition_enabled = True
        self.app.state.biometric_cipher = BiometricCipher(keyring("test-kyc-biometric"))
        self.service = self.app.state.face_identity
        self.service.cipher = BiometricCipher(keyring("test-independent-face"))
        self.directory = tempfile.TemporaryDirectory()
        self.service.ledger = DeletionLedger(Path(self.directory.name) / "ledger.jsonl", b"d" * 32)
        self.service.ledger.initialize()
        self.service.policy = test_face_identity.test_policy()
        self.clock = now()
        self.service.approval_expiry = self.clock + timedelta(hours=1)
        self.time_patch = patch("kyc.face_identity.service.now", lambda: self.clock)
        self.time_patch.start()
        self.face = test_face_identity.VisibilityEngine()
        self.app.state.face_engine = self.face
        self.app.state.settings.integration_kyc_production_enabled = True
        self.production, self.client = await self.make_app(mode="production")
        self.kyc = UUID((await self.create())["session_id"])
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, self.kyc)
            record.status = SessionStatus.VERIFIED  # synthetic fixture; never a production verification
            identifier, reference = uuid4(), unit(0)
            sealed, key = self.app.state.biometric_cipher.seal(serialize_embedding(reference),
                _template_context(record, identifier, "DOCUMENT_PORTRAIT", reference))
            db.add(BiometricTemplate(id=identifier, organization_id=self.org, session_id=self.kyc,
                source="DOCUMENT_PORTRAIT", model_name=reference.model_name, model_version=reference.model_version,
                model_sha256=reference.model_sha256, embedding_dimension=128, template_ciphertext=sealed,
                key_version=key, delete_after=self.clock + timedelta(hours=1)))

    async def asyncTearDown(self):
        self.time_patch.stop()
        await test_integrations.IntegrationTests.asyncTearDown(self)
        self.directory.cleanup()

    async def start(self, enrollment=True, client=None):
        prefix = "face-enrollment-sessions" if enrollment else "recognition-sessions"
        code, row, _ = await call(self.app, f"/v1/{prefix}", "POST", {"purpose": "self_profile", "population": "STAFF",
            "external_subject_id": "EMP-00123", "verification_session_id": str(self.kyc)}, client or self.client)
        self.assertEqual(code, 201, row)
        headers = {"X-Organization-ID": str(self.org), "Authorization": "Bearer " + row["capture_token"]}
        return prefix, row, headers

    async def capture(self, enrollment=True):
        prefix, envelope, headers = await self.start(enrollment)
        path = f"/v1/{prefix}/{envelope['session_id']}"
        code, capture, _ = await call(self.app, path + "/capture", "POST",
            {"consent": True, "notice_version": "TEST-NOTICE"} if enrollment else None, headers)
        self.assertEqual(code, 200, capture)
        for _ in range(8):
            self.clock += timedelta(milliseconds=650)
            data = self.face.frame(real_head(capture["step"]))
            payload, content_type = multipart({"ticket": capture["ticket"]}, [("frame", "synthetic.png", "image/png", data)])
            code, capture, _ = await call(self.app, path + "/frames", "POST", headers=headers, raw=payload, content_type=content_type)
            self.assertEqual(code, 200, capture)
        self.assertEqual(capture["state"], "READY_TO_COMPLETE")
        code, reply, _ = await call(self.app, path + "/complete", "POST", {}, headers | {"Idempotency-Key": "synthetic-completion-key"})
        self.assertEqual(code, 200, reply)
        return envelope, reply

    async def test_enrollment_requires_device_consent_and_fresh_complete_capture(self):
        prefix, row, headers = await self.start()
        path = f"/v1/{prefix}/{row['session_id']}"
        code, _, _ = await call(self.app, path + "/capture", "POST", {"consent": False, "notice_version": "TEST-NOTICE"}, headers)
        self.assertEqual(code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceConsent)), 0)
        code, capture, _ = await call(self.app, path + "/capture", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, headers)
        self.assertEqual(code, 200, capture)
        self.assertEqual((await call(self.app, path + "/complete", "POST", {}, headers | {"Idempotency-Key": "not-yet-captured-key"}))[0], 409)
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, self.kyc).status.value, "VERIFIED")

    async def test_tenant_isolated_match_returns_only_external_id_and_permitted_profile(self):
        _, enrollment = await self.capture()
        self.assertEqual(enrollment["status"], "ACTIVE", enrollment)
        session, result = await self.capture(False)
        self.assertEqual(result["outcome"], "MATCHED", result)
        path = f"/v1/recognition-sessions/{session['session_id']}/result"
        code, result, _ = await call(self.app, path, headers=self.client)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["external_subject_id"], "EMP-00123")
        self.assertEqual(result["profile"], {"kyc_status": "VERIFIED"})
        self.assertEqual(set(result), {"outcome", "message", "recognition_reference", "expires_at", "external_subject_id", "profile"})
        _, sibling = await self.make_app(mode="production")
        self.assertEqual((await call(self.app, path, headers=sibling))[0], 404)
        _, foreign = await self.make_app(self.other_org, mode="production")
        self.assertEqual((await call(self.app, path, headers=foreign))[0], 404)
        # The other tenant has no enrollment population, even with the same synthetic face vector.
        with Session(self.engine) as db:
            self.assertEqual(self.service._eligible(db, self.other_org), [])

    async def test_withdrawn_kyc_and_replayed_frame_do_not_match(self):
        prefix, row, headers = await self.start()
        path = f"/v1/{prefix}/{row['session_id']}"
        _, capture, _ = await call(self.app, path + "/capture", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, headers)
        self.clock += timedelta(seconds=1)
        data = self.face.frame(real_head(capture["step"]))
        payload, content_type = multipart({"ticket": capture["ticket"]}, [("frame", "test.png", "image/png", data)])
        code, next_, _ = await call(self.app, path + "/frames", "POST", headers=headers, raw=payload, content_type=content_type)
        self.assertEqual(code, 200, next_)
        self.assertEqual((await call(self.app, path + "/frames", "POST", headers=headers, raw=payload, content_type=content_type))[0], 409)
        with Session(self.engine) as db, db.begin():
            db.get(KYCSession, self.kyc).erased_at = now()
        self.assertEqual((await call(self.app, path + "/complete", "POST", {}, headers | {"Idempotency-Key": "erased-capture-key"}))[0], 403)
