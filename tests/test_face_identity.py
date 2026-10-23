"""Security/lifecycle tests with scripted faces. These are NOT biometric accuracy measurements."""

import base64
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.biometrics.embeddings import serialize_embedding
from kyc.biometrics.quality import assess_face_quality
from kyc.db.models import (Base, BiometricTemplate, FaceActor, FaceCaptureSession, FaceConsent, FaceDeletion,
    FaceEnrollment, FaceRecognitionEvent, FaceSubject, FaceTemplate, KYCSession, Organization)
from kyc.db.session import build_engine
from kyc.face_identity.ledger import DeletionLedger
from kyc.face_identity.lifecycle import maintain
from kyc.face_identity.policy import IdentityPolicy, decide
from kyc.face_identity.service import context, digest, now, profile_context
from kyc.liveness.active import movement_visibility
from kyc.main import create_app
from kyc.services.biometrics import _template_context
from kyc.services.erasure import erase_session
from tests.helpers import call, multipart
from tests.test_api import configuration, TEST_KEY
from tests.test_capture_store import keyring
from tests.test_liveness import FrameEngine, real_head, unit


def test_policy():
    # Deliberately synthetic decision parameters. No runtime policy reads these.
    return IdentityPolicy(version="SYNTHETIC-IDENTITY-TEST", populations=["STAFF", "CUSTOMER"], identification_threshold=.8,
        ambiguity_margin=.1, duplicate_threshold=.95, verification_threshold=.8, movement=.08,
        max_population=10, retention_days=30, notice_version="TEST-NOTICE", visibility_provider="TEST-VISIBILITY")


class VisibilityEngine(FrameEngine):
    visibility_provider = "TEST-VISIBILITY"
    occlusion_supported = True
    occluded = False

    def visibility(self, image, face, frontal=False):
        result = assess_face_quality(image, [face]) if frontal else movement_visibility(image, face)
        return replace(result, unverified_checks=() if not self.occluded else ("SEVERE_OCCLUSION",))


class FaceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.org, self.other_org = uuid4(), uuid4()
        self.settings = configuration(self.org, biometric_encryption_keys=keyring("kyc-bio"),
            pii_encryption_keys=keyring("pii"), pii_hmac_key=base64.b64encode(os.urandom(32)).decode(),
            face_recognition_enabled=True, face_recognition_keys=keyring("face-only"),
            face_recognition_ledger_path=self.root / "ledger.jsonl",
            face_recognition_ledger_key=base64.b64encode(os.urandom(32)).decode())
        self.engine = build_engine(self.settings)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([Organization(id=self.org, name="Synthetic A"), Organization(id=self.other_org, name="Synthetic B")])
        self.app = create_app(self.settings, self.engine)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.service = self.app.state.face_identity
        self.service.policy, self.service.approval_expiry = test_policy(), now() + timedelta(hours=1)
        self.service.ledger.initialize()
        self.face = VisibilityEngine()
        self.app.state.face_engine = self.face
        self.clock = now()
        self.time_patch = patch("kyc.face_identity.service.now", lambda: self.clock)
        self.time_patch.start()
        self.lifecycle_time_patch = patch("kyc.face_identity.lifecycle.now", lambda: self.clock)
        self.lifecycle_time_patch.start()
        self.subject, self.kyc, self.person, self.headers = self.make_actor("PERSON")
        _, _, self.kiosk, self.kiosk_headers = self.make_actor("KIOSK")
        self.key = {"Idempotency-Key": "synthetic-completion-key"}

    async def asyncTearDown(self):
        self.time_patch.stop()
        self.lifecycle_time_patch.stop()
        await self.lifespan.__aexit__(None, None, None)
        self.engine.dispose()
        self.directory.cleanup()

    def make_actor(self, kind, organization=None, subject=None, population="STAFF"):
        tenant = organization or self.org
        token = "fri_" + uuid4().hex + uuid4().hex
        actor_id, kyc_id = uuid4(), None
        with Session(self.engine) as db, db.begin():
            if kind == "PERSON" and subject is None:
                kyc_id, subject = uuid4(), uuid4()
                db.add(KYCSession(id=kyc_id, organization_id=tenant, user_id="synthetic-user", country="KH",
                    expected_document_type="KH_PASSPORT", verification_level="DOCUMENT_FACE_LIVENESS",
                    status="VERIFIED", expires_at=self.clock + timedelta(hours=1)))
                db.flush()
                row = FaceSubject(id=subject, organization_id=tenant, kyc_session_id=kyc_id, population=population)
                row.profile_ciphertext, row.profile_key_version = self.app.state.field_cipher.seal(json.dumps({
                    "display_name": "Synthetic Person", "internal_reference": "TEST-01", "department": "Test",
                    "date_of_birth": "SHOULD_NEVER_ESCAPE"}), profile_context(row))
                row.profile_expires_at = self.clock + timedelta(days=30)
                db.add(row)
                db.flush()
                reference_id = uuid4()
                reference = unit(0)
                kyc = db.get(KYCSession, kyc_id)
                sealed, version = self.app.state.biometric_cipher.seal(serialize_embedding(reference),
                    _template_context(kyc, reference_id, "DOCUMENT_PORTRAIT", reference))
                db.add(BiometricTemplate(id=reference_id, organization_id=tenant, session_id=kyc_id,
                    source="DOCUMENT_PORTRAIT", model_name=reference.model_name, model_version=reference.model_version,
                    model_sha256=reference.model_sha256, embedding_dimension=128, template_ciphertext=sealed,
                    key_version=version, delete_after=self.clock + timedelta(hours=1)))
            db.add(FaceActor(id=actor_id, organization_id=tenant, subject_id=subject if kind == "PERSON" else None,
                kind=kind, token_hash=digest(token), purposes=["self_profile"] if kind == "PERSON" else ["staff_lookup"] if kind == "KIOSK" else ["customer_service", "duplicate_resolution"],
                populations=[population] if kind == "PERSON" else ["STAFF"] if kind == "KIOSK" else ["STAFF", "CUSTOMER"],
                profile_fields=["display_name", "internal_reference", "department", "kyc_status", "date_of_birth"],
                expires_at=self.clock + timedelta(hours=1), branch="TEST"))
        return subject, kyc_id, actor_id, {"X-Organization-ID": str(tenant), "Authorization": "Bearer " + token}

    async def create(self, enroll=True, headers=None, **extra):
        path = "/v1/face-enrollments/sessions" if enroll else "/v1/recognition/sessions"
        body = {"consent": True, "notice_version": "TEST-NOTICE"} if enroll else {"purpose": "staff_lookup"}
        code, body, _ = await call(self.app, path, "POST", body | extra, headers or (self.headers if enroll else self.kiosk_headers))
        self.assertEqual(code, 201, body)
        return body

    async def frame(self, capture, enroll=True, headers=None, landmarks=None, data=None, identity=0):
        self.clock += timedelta(milliseconds=650)
        data = data or self.face.frame(landmarks or real_head(capture["step"]), identity=identity)
        raw, content_type = multipart({"ticket": capture["ticket"]}, [("frame", "live.png", "image/png", data)])
        prefix = "face-enrollments" if enroll else "recognition"
        return await call(self.app, f'/v1/{prefix}/sessions/{capture["session_id"]}/frames', "POST",
            headers=headers or (self.headers if enroll else self.kiosk_headers), raw=raw, content_type=content_type)

    async def capture(self, enroll=True, headers=None, **extra):
        capture = await self.create(enroll, headers, **extra)
        for _ in range(8):
            code, capture, _ = await self.frame(capture, enroll, headers)
            self.assertEqual(code, 200, capture)
        self.assertEqual(capture["state"], "READY_TO_COMPLETE", capture)
        return capture

    async def complete(self, capture, enroll=True, headers=None, key=None):
        prefix = "face-enrollments" if enroll else "recognition"
        return await call(self.app, f'/v1/{prefix}/sessions/{capture["session_id"]}/complete', "POST", {},
            (headers or (self.headers if enroll else self.kiosk_headers)) | (key or self.key))

    async def enroll(self):
        capture = await self.capture()
        code, body, _ = await self.complete(capture)
        self.assertEqual((code, body["status"]), (200, "ACTIVE"), body)
        return capture

    async def match(self):
        capture = await self.capture(False)
        code, result, _ = await self.complete(capture, False)
        self.assertEqual((code, result["outcome"]), (200, "MATCHED"), result)
        code, result, headers = await call(self.app, f'/v1/recognition/sessions/{capture["session_id"]}/result', headers=self.kiosk_headers)
        self.assertEqual(code, 200, result)
        self.assertEqual(headers["cache-control"], "no-store")
        return capture, result

    async def profile(self, capture, result, headers=None, key=None):
        return await call(self.app, f'/v1/recognition/sessions/{capture["session_id"]}/profile', "POST",
            {"recognition_reference": result["recognition_reference"]}, (headers or self.kiosk_headers) | (key or self.key))

    async def test_fresh_enrollment_and_disclosure_do_not_return_vectors_or_sensitive_fields(self):
        await self.enroll()
        capture, result = await self.match()
        self.assertEqual(set(result), {"outcome", "message", "recognition_reference", "expires_at"})
        code, body, headers = await self.profile(capture, result)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["clear_after_seconds"], 15)
        self.assertEqual(set(body["profile"]), {"display_name", "internal_reference", "department", "kyc_status"})
        self.assertNotIn("SHOULD_NEVER_ESCAPE", json.dumps(body))
        self.assertEqual(headers["cache-control"], "no-store")
        with Session(self.engine) as db:
            template = db.scalar(sa.select(FaceTemplate))
            self.assertNotIn(b'Synthetic Person', template.ciphertext)
            self.assertEqual(db.get(KYCSession, self.kyc).status.value, "VERIFIED")
            self.assertTrue(all(row.sample_ciphertext is None for row in db.scalars(sa.select(FaceCaptureSession))))

    async def test_kyc_api_keys_and_anonymous_users_cannot_enroll(self):
        for headers in [{}, {"X-Organization-ID": str(self.org), "X-API-Key": TEST_KEY}]:
            code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, headers)
            self.assertIn(code, (401, 422))

    async def test_declining_and_disabled_feature_preserve_kyc(self):
        code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": False, "notice_version": "TEST-NOTICE"}, self.headers)
        self.assertEqual(code, 422)
        self.settings.face_recognition_enabled = False
        code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, self.headers)
        self.assertEqual(code, 503)
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, self.kyc).status.value, "VERIFIED")
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceEnrollment)), 0)

    async def test_nonverified_kyc_and_client_subject_selection_are_refused(self):
        code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": True, "notice_version": "TEST-NOTICE", "subject_id": str(uuid4())}, self.headers)
        self.assertEqual(code, 422)
        with Session(self.engine) as db, db.begin():
            db.get(KYCSession, self.kyc).status = "MANUAL_REVIEW"
        code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, self.headers)
        self.assertEqual(code, 403)

    async def test_old_authentication_and_kiosk_enrollment_are_refused(self):
        with Session(self.engine) as db, db.begin():
            db.get(FaceActor, self.person).created_at = self.clock - timedelta(minutes=11)
        for headers in (self.headers, self.kiosk_headers):
            code, _, _ = await call(self.app, "/v1/face-enrollments/sessions", "POST", {"consent": True, "notice_version": "TEST-NOTICE"}, headers)
            self.assertEqual(code, 403)

    async def test_ticket_is_one_use_and_sessions_are_actor_and_tenant_bound(self):
        capture = await self.create()
        code, updated, _ = await self.frame(capture)
        self.assertEqual(code, 200)
        code, _, _ = await self.frame(capture)
        self.assertEqual(code, 409)
        _, _, _, other_headers = self.make_actor("PERSON")
        code, _, _ = await self.frame(updated, headers=other_headers)
        self.assertEqual(code, 404)
        wrong_tenant = self.headers | {"X-Organization-ID": str(self.other_org)}
        code, _, _ = await self.frame(updated, headers=wrong_tenant)
        self.assertEqual(code, 401)

    async def test_reused_frame_across_sessions_is_liveness_failure(self):
        capture = await self.create()
        frame = self.face.frame(real_head("LOOK_STRAIGHT"))
        self.assertEqual((await self.frame(capture, data=frame))[0], 200)
        second = await self.create()
        code, body, _ = await self.frame(second, data=frame)
        self.assertEqual((code, body["outcome"]), (200, "LIVENESS_FAILED"))

    async def test_wrong_direction_occlusion_and_one_face_checks_do_not_advance(self):
        capture = await self.create()
        self.face.occluded = True
        code, capture, _ = await self.frame(capture)
        self.assertEqual((code, capture["held"], capture["step_index"]), (200, 0, 0))
        self.face.occluded = False
        missing = self.face.frame(real_head("LOOK_STRAIGHT"), faces=2)
        _, capture, _ = await self.frame(capture, data=missing)
        self.assertEqual(capture["instructions"], ["ONE_FACE_ONLY"])
        for _ in range(2):
            _, capture, _ = await self.frame(capture)
        _, wrong, _ = await self.frame(capture, landmarks=real_head("LOOK_STRAIGHT"))
        self.assertEqual((wrong["held"], wrong["step_index"]), (0, 1))

    async def test_visibility_capability_is_an_independent_readiness_gate(self):
        self.face.occlusion_supported = False
        code, body, _ = await call(self.app, "/health/face-recognition")
        self.assertEqual(code, 503)
        self.assertIn("VALIDATED_VISIBILITY_PROVIDER_REQUIRED", body["reason_codes"])

    async def test_expiry_and_stale_completion_never_enroll(self):
        capture = await self.capture()
        self.clock += timedelta(seconds=11)
        self.assertEqual((await self.complete(capture))[0], 409)
        self.clock += timedelta(minutes=3)
        code, body, _ = await self.complete(capture)
        self.assertEqual((code, body["outcome"]), (200, "EXPIRED"))

    async def test_completion_and_disclosure_idempotency(self):
        capture = await self.enroll()
        self.assertEqual((await self.complete(capture))[1]["status"], "ACTIVE")
        self.assertEqual((await self.complete(capture, key={"Idempotency-Key": "different-operation-key"}))[0], 409)
        recognition, result = await self.match()
        first = await self.profile(recognition, result)
        self.assertEqual((await self.profile(recognition, result))[1], first[1])
        self.assertEqual((await self.profile(recognition, result, key={"Idempotency-Key": "different-operation-key"}))[0], 409)

    async def test_no_match_and_ambiguous_never_authorize_profiles(self):
        capture = await self.capture(False)
        code, result, _ = await self.complete(capture, False)
        self.assertEqual((code, result["outcome"]), (200, "NO_MATCH"))
        code, _, _ = await call(self.app, f'/v1/recognition/sessions/{capture["session_id"]}/profile', "POST",
            {"recognition_reference": "x" * 43}, self.kiosk_headers | self.key)
        self.assertEqual(code, 403)
        outcome, identifier = decide([(uuid4(), .95), (uuid4(), .94)], self.service.policy)
        self.assertEqual((outcome, identifier), ("AMBIGUOUS", None))

    async def test_withdrawal_immediately_deletes_templates_and_invalidates_reference(self):
        await self.enroll()
        capture, result = await self.match()
        code, body, _ = await call(self.app, "/v1/me/face-enrollment", "DELETE", headers=self.headers)
        self.assertEqual((code, body["status"]), (200, "DISABLED"))
        self.assertEqual((await self.profile(capture, result))[0], 403)
        self.assertEqual((await call(self.app, "/v1/me/face-enrollment", "DELETE", headers=self.headers))[0], 200)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 0)
            self.assertIsNotNone(db.scalar(sa.select(FaceConsent)).withdrawn_at)
            self.assertEqual(db.get(KYCSession, self.kyc).status.value, "VERIFIED")

    async def test_consent_withdrawal_invalidates_pending_capture(self):
        capture = await self.capture()
        await call(self.app, "/v1/me/face-enrollment", "DELETE", headers=self.headers)
        self.assertEqual((await self.complete(capture))[0], 409)

    async def test_duplicate_enrollment_is_private_and_requires_authorized_resolution(self):
        await self.enroll()
        _, _, _, other_headers = self.make_actor("PERSON")
        capture = await self.capture(headers=other_headers)
        code, body, _ = await self.complete(capture, headers=other_headers)
        self.assertEqual((code, body["status"]), (200, "DUPLICATE_REVIEW"))
        self.assertNotIn("Synthetic", json.dumps(body))
        self.assertEqual((await call(self.app, "/v1/face-enrollments/duplicates", headers=self.kiosk_headers))[0], 403)
        _, _, _, operator = self.make_actor("OPERATOR")
        code, cases, _ = await call(self.app, "/v1/face-enrollments/duplicates", headers=operator)
        self.assertEqual(code, 200)
        enrollment = cases["cases"][0]["evidence"]["enrollment"]
        path = f'/v1/face-enrollments/duplicates/{enrollment}/resolve'
        body = {"action": "REJECT", "reason_code": "CONFIRMED_DUPLICATE"}
        code, result, _ = await call(self.app, path, "POST", body, operator | self.key)
        self.assertEqual((code, result["status"]), (200, "REJECTED"))
        self.assertEqual((await call(self.app, path, "POST", body, operator | self.key))[0], 200)

    async def test_suspended_expired_and_revoked_population_is_excluded(self):
        await self.enroll()
        with Session(self.engine) as db, db.begin():
            db.get(FaceSubject, self.subject).active = False
        capture = await self.capture(False)
        self.assertEqual((await self.complete(capture, False))[1]["outcome"], "NO_MATCH")

    async def test_revoked_device_and_cross_actor_reference_are_refused(self):
        await self.enroll()
        capture, result = await self.match()
        _, _, _, another_device = self.make_actor("KIOSK")
        self.assertEqual((await self.profile(capture, result, headers=another_device))[0], 404)
        with Session(self.engine) as db, db.begin():
            db.get(FaceActor, self.kiosk).revoked_at = self.clock
        self.assertEqual((await self.profile(capture, result))[0], 401)

    async def test_corrupt_templates_are_technical_errors_not_identity_rejections(self):
        await self.enroll()
        with Session(self.engine) as db, db.begin():
            row = db.scalar(sa.select(FaceTemplate))
            row.ciphertext = row.ciphertext[:-1] + bytes([row.ciphertext[-1] ^ 1])
        capture = await self.capture(False)
        self.assertEqual((await self.complete(capture, False))[1]["outcome"], "TECHNICAL_ERROR")

    async def test_deleted_enrollment_cannot_return_after_database_snapshot_restore(self):
        await self.enroll()
        with Session(self.engine) as db:
            enrollment = db.scalar(sa.select(FaceEnrollment))
            saved_id, consent_id = enrollment.id, enrollment.consent_id
            template = db.scalar(sa.select(FaceTemplate))
            saved = {column.name: getattr(template, column.name) for column in FaceTemplate.__table__.columns}
        await call(self.app, "/v1/me/face-enrollment", "DELETE", headers=self.headers)
        with Session(self.engine) as db, db.begin():
            db.execute(sa.delete(FaceDeletion))  # Mimic an older database, not the independent journal.
            db.get(FaceEnrollment, saved_id).status = "ACTIVE"
            db.get(FaceConsent, consent_id).withdrawn_at = None
            db.add(FaceTemplate(**saved))
        capture = await self.capture(False)
        self.assertEqual((await self.complete(capture, False))[1]["outcome"], "NO_MATCH")
        with Session(self.engine) as db, db.begin():
            report = maintain(db, self.service, self.org)
            self.assertEqual(report["restored_enrollments_removed"], 1)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 0)

    async def test_kyc_erasure_also_revokes_face_identity(self):
        await self.enroll()
        with Session(self.engine) as db, db.begin():
            erase_session(db, TenantContext(self.org), db.get(KYCSession, self.kyc), uuid4(), face_ledger=self.service.ledger)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 0)
            self.assertFalse(db.get(FaceSubject, self.subject).active)
            self.assertIsNone(db.get(FaceSubject, self.subject).profile_ciphertext)
        self.assertEqual(len(self.service.ledger.denied(self.org)), 1)

    async def test_ciphertext_is_tenant_and_purpose_bound(self):
        await self.enroll()
        with Session(self.engine) as db:
            enrollment = db.scalar(sa.select(FaceEnrollment))
            template = db.scalar(sa.select(FaceTemplate))
            correct = context(enrollment, "enrollment", enrollment.model_version)
            with self.assertRaises(InvalidTag):
                self.service.cipher.open(template.ciphertext, template.key_version, correct.replace(str(self.org), str(self.other_org)))
            with self.assertRaises(InvalidTag):
                self.service.cipher.open(template.ciphertext, template.key_version, context(enrollment, "capture", enrollment.model_version))

    async def test_person_scan_cannot_disclose_another_enrolled_person(self):
        await self.enroll()
        _, _, _, other_person = self.make_actor("PERSON")
        capture = await self.capture(False, other_person, purpose="self_profile")
        code, result, _ = await self.complete(capture, False, other_person)
        self.assertEqual((code, result["outcome"]), (200, "NO_MATCH"))

    async def test_ambiguity_is_enforced_end_to_end_after_distinct_person_review(self):
        await self.enroll()
        _, _, _, other_person = self.make_actor("PERSON")
        pending = await self.capture(headers=other_person)
        self.assertEqual((await self.complete(pending, headers=other_person))[1]["status"], "DUPLICATE_REVIEW")
        _, _, _, operator = self.make_actor("OPERATOR")
        _, cases, _ = await call(self.app, "/v1/face-enrollments/duplicates", headers=operator)
        identifier = cases["cases"][0]["evidence"]["enrollment"]
        code, _, _ = await call(self.app, f"/v1/face-enrollments/duplicates/{identifier}/resolve", "POST",
            {"action": "ALLOW_DISTINCT", "reason_code": "REVIEWED_DISTINCT_PERSONS"}, operator | self.key)
        self.assertEqual(code, 200)
        capture = await self.capture(False)
        code, result, _ = await self.complete(capture, False)
        self.assertEqual((code, result["outcome"]), (200, "AMBIGUOUS"))
        _, result, _ = await call(self.app, f'/v1/recognition/sessions/{capture["session_id"]}/result', headers=self.kiosk_headers)
        self.assertNotIn("recognition_reference", result)

    async def test_expired_consent_reference_and_profile_never_disclose(self):
        await self.enroll()
        capture, result = await self.match()
        self.clock += timedelta(seconds=31)
        self.assertEqual((await self.profile(capture, result))[0], 403)
        capture, result = await self.match()
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(FaceConsent)).expires_at = self.clock - timedelta(seconds=1)
        self.assertEqual((await self.profile(capture, result))[0], 403)

    async def test_expired_profile_is_not_returned_even_after_match(self):
        await self.enroll()
        capture, result = await self.match()
        with Session(self.engine) as db, db.begin():
            db.get(FaceSubject, self.subject).profile_expires_at = self.clock - timedelta(seconds=1)
        self.assertEqual((await self.profile(capture, result))[0], 503)

    async def test_changed_thresholds_under_same_label_invalidate_old_capture(self):
        capture = await self.capture()
        self.service.policy = self.service.policy.model_copy(update={"identification_threshold": .9})
        self.assertEqual((await self.complete(capture))[0], 409)

    async def test_incompatible_model_is_excluded(self):
        await self.enroll()
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(FaceEnrollment)).model_version = "INCOMPATIBLE-MODEL"
        capture = await self.capture(False)
        self.assertEqual((await self.complete(capture, False))[1]["outcome"], "NO_MATCH")

    async def test_withdrawal_remains_available_when_feature_is_disabled(self):
        await self.enroll()
        self.settings.face_recognition_enabled = False
        self.assertEqual((await call(self.app, "/v1/me/face-enrollment", "DELETE", headers=self.headers))[0], 200)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 0)

    async def test_reenrollment_replaces_templates_without_changing_subject_or_kyc(self):
        await self.enroll()
        with Session(self.engine) as db:
            old = db.scalar(sa.select(FaceEnrollment.id))
        await self.enroll()
        with Session(self.engine) as db:
            self.assertEqual(db.get(FaceEnrollment, old).status, "SUPERSEDED")
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 1)
            self.assertEqual(db.get(KYCSession, self.kyc).status.value, "VERIFIED")
        self.assertIn(str(old), self.service.ledger.denied(self.org))

    async def test_ticket_expiry_recovers_and_exposes_no_old_hold(self):
        capture = await self.create()
        self.clock += timedelta(seconds=16)
        code, body, _ = await self.frame(capture)
        self.assertEqual((code, body["state"], body["held"]), (200, "RETRY_REQUIRED", 0))
        self.assertIn("FRESH_CAPTURE_REQUIRED", body["instructions"])

    async def test_retention_physically_removes_expired_templates(self):
        await self.enroll()
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(FaceEnrollment)).expires_at = self.clock - timedelta(seconds=1)
        with Session(self.engine) as db, db.begin():
            result = maintain(db, self.service, self.org)
            self.assertEqual(result["expired_enrollments"], 1)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(FaceTemplate)), 0)

    async def test_session_rate_limit_is_durable_across_limiter_resets(self):
        for _ in range(5):
            await self.create(False)
        from kyc.tenancy.ratelimit import RateLimiter
        self.app.state.rate_limiter = RateLimiter()
        code, _, _ = await call(self.app, "/v1/recognition/sessions", "POST", {"purpose": "staff_lookup"}, self.kiosk_headers)
        self.assertEqual(code, 429)

    async def test_invalid_disabled_optional_keys_do_not_break_ordinary_kyc(self):
        settings = configuration(self.org, face_recognition_keys="invalid", face_recognition_ledger_key="invalid",
            face_recognition_ledger_path=self.root / "invalid", face_recognition_database_url="invalid")
        app = create_app(settings, self.engine)
        async with app.router.lifespan_context(app):
            self.assertEqual((await call(app, "/health/live"))[0], 200)
            self.assertEqual((await call(app, "/health/face-recognition"))[0], 503)

    async def test_staff_kiosk_excludes_customer_population_before_matching(self):
        _, _, _, customer = self.make_actor("PERSON", population="CUSTOMER")
        capture = await self.capture(headers=customer)
        code, result, _ = await self.complete(capture, headers=customer)
        self.assertEqual((code, result["status"]), (200, "ACTIVE"))
        capture = await self.capture(False)
        self.assertEqual((await self.complete(capture, False))[1]["outcome"], "NO_MATCH")
        capture = await self.capture(False, customer, purpose="self_profile")
        self.assertEqual((await self.complete(capture, False, customer))[1]["outcome"], "MATCHED")

    async def test_actor_cannot_claim_an_unauthorized_population(self):
        code, _, _ = await call(self.app, "/v1/recognition/sessions", "POST",
            {"purpose": "staff_lookup", "population": "CUSTOMER"}, self.kiosk_headers)
        self.assertEqual(code, 422)
        self.service.policy = self.service.policy.model_copy(update={"populations": ["CUSTOMER"]})
        code, body, _ = await call(self.app, "/v1/face-recognition/capabilities", headers=self.kiosk_headers)
        self.assertEqual((code, body["ready"]), (200, False))
        code, _, _ = await call(self.app, "/v1/recognition/sessions", "POST", {"purpose": "staff_lookup"}, self.kiosk_headers)
        self.assertEqual(code, 503)
        with Session(self.engine) as db, db.begin():
            db.get(FaceActor, self.kiosk).populations = ["CUSTOMER"]
        code, _, _ = await call(self.app, "/v1/recognition/sessions", "POST", {"purpose": "staff_lookup"}, self.kiosk_headers)
        self.assertEqual(code, 403)


class LedgerTests(unittest.TestCase):
    def test_truncation_and_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = DeletionLedger(Path(directory) / "ledger", os.urandom(32))
            ledger.initialize()
            ledger.append(uuid4(), uuid4(), "WITHDRAWAL")
            original = ledger.path.read_bytes()
            ledger.path.write_bytes(b"")
            with self.assertRaises(ValueError):
                ledger.entries()
            ledger.path.write_bytes(original.replace(b"WITHDRAWAL", b"RESTORE_BAD"))
            with self.assertRaises(ValueError):
                ledger.entries()
