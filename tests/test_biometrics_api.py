"""HTTP orchestration checks with explicit injected face evidence (no fake model fallback)."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import numpy as np
from PIL import Image
import sqlalchemy as sa
from sqlalchemy.orm import Session
import starlette.formparsers

from kyc.biometrics import FaceAssessment, FaceDetection, FaceEmbedding, FaceEngineUnavailable, FaceMatchPolicy
from kyc.biometrics import deserialize_embedding
from kyc.api.dependencies import TenantContext
from kyc.db.models import (AuditLog, Base, BiometricTemplate, Consent, DocumentCheck, DocumentImage, FaceComparison,
                           FaceQualityCheck, IdentityDocument, KYCSession, LivenessCheck, Organization, SelfieCapture)
from kyc.db.session import build_engine
from kyc.domain.enums import CheckResult, SessionStatus
from kyc.main import create_app
from kyc.services.retention import purge_organization
from kyc.services.biometrics import SelfieLimits, submit_selfie
from tests.helpers import call, multipart
from tests.test_api import TEST_KEY, configuration
from tests.test_capture_store import keyring


def encoded(color=(170, 145, 125), size=(512, 512)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


SELFIE = encoded()
DOCUMENT = encoded((80, 150, 210), (1600, 1126))
UNKNOWN = ("FACE_EYE_VISIBILITY_UNVERIFIED", "FACE_OCCLUSION_UNVERIFIED")


class InjectedFaceEngine:
    """A test-only provider; the production engine remains unavailable without models."""

    def __init__(self):
        self.live_reason = None
        self.reference_faces = 1
        self.same_identity = True
        self.unavailable = False
        self.embed_unavailable = False
        self.calls = []
        self.reference_image = None

    def unavailable_reason(self):
        return "FACE_MODELS_UNAVAILABLE" if self.unavailable else None

    def assess(self, image, source="LIVE_SELFIE"):
        self.calls.append(("assess", source))
        if source == "DOCUMENT_PORTRAIT":
            self.reference_image = image
        count = self.reference_faces if source == "DOCUMENT_PORTRAIT" else 1
        reason = self.live_reason if source == "LIVE_SELFIE" else ("MULTIPLE_FACES" if count > 1 else "NO_FACE" if count == 0 else None)
        detection = FaceDetection((30, 30, 200, 240), ((70, 100), (180, 100), (130, 150), (85, 210), (170, 210)), .99)
        return FaceAssessment(accepted=reason is None, scores={"overall_quality": .9, "blur_score": .9,
                              "brightness_score": .8, "face_resolution_score": 1.0, "pose_score": .9},
                              reason_codes=(reason,) if reason else UNKNOWN,
                              instructions=("KEEP_FACE_STILL",) if reason else (),
                              detection=detection if count == 1 else None, face_count=count,
                              policy_version="TEST-FACE-QUALITY-v1")

    def embed(self, image, detection):
        self.calls.append(("embed", image is self.reference_image))
        if self.embed_unavailable:
            raise FaceEngineUnavailable("Unavailable")
        vector = np.zeros(128, dtype=np.float32)
        vector[0 if image is self.reference_image or self.same_identity else 1] = 1
        return FaceEmbedding(vector)


class BiometricAPICase(unittest.IsolatedAsyncioTestCase):
    max_attempts = 3

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = Path(self.directory.name)
        self.org, self.other_org = uuid4(), uuid4()
        self.settings = configuration(self.org, capture_encryption_keys=keyring("capture-test-v1"),
            biometric_encryption_keys=keyring("biometric-test-v1"), capture_storage_dir=self.storage,
            face_models_dir=self.storage / "absent-models", max_selfie_attempts=self.max_attempts,
            face_match_calibrated=False, face_match_calibration_reference=None,
            face_match_policy_version="TEST-FACE-UNCALIBRATED-v1")
        self.engine = build_engine(self.settings)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([Organization(id=self.org, name="Tenant A", capture_retention_hours=2, template_retention_hours=1),
                        Organization(id=self.other_org, name="Tenant B")])
        self.app = create_app(self.settings, self.engine)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.face = InjectedFaceEngine()
        self.app.state.face_engine = self.face
        self.headers = {"X-API-Key": TEST_KEY, "X-Organization-ID": str(self.org)}

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.engine.dispose()
        self.directory.cleanup()

    def ready(self, *, status="SELFIE_REQUIRED", level="DOCUMENT_FACE_LIVENESS", organization=None,
              document_type="KH_PASSPORT", capture_side="DATA_PAGE", logical_side="DATA_PAGE"):
        org = organization or self.org
        now, session_id, document_id, image_id = datetime.now(timezone.utc), uuid4(), uuid4(), uuid4()
        stored = self.app.state.capture_store.put(org, session_id, image_id, DOCUMENT)
        with Session(self.engine) as db, db.begin():
            db.add(KYCSession(id=session_id, organization_id=org, user_id="synthetic-customer", country="KH",
                expected_document_type=document_type, verification_level=level, status=status,
                created_at=now, updated_at=now, expires_at=now + timedelta(minutes=15)))
            db.flush()
            db.add(IdentityDocument(id=document_id, organization_id=org, session_id=session_id,
                document_type=document_type, issuing_country="KH", classification_confidence=.99,
                side_classification={logical_side: {"side": logical_side, "capture_side": capture_side}},
                processed_at=now, delete_after=now + timedelta(days=7)))
            db.flush()
            db.add(DocumentImage(id=image_id, organization_id=org, session_id=session_id, document_id=document_id,
                side=capture_side, encrypted_object_ref=stored.ref, key_version=stored.key_version,
                media_type="image/jpeg", sha256=stored.sha256, quality_scores={}, quality_policy_version="DOC-TEST",
                delete_after=now + timedelta(hours=2)))
            db.add(DocumentCheck(organization_id=org, session_id=session_id, document_id=document_id,
                check_type="PORTRAIT", result=CheckResult.UNAVAILABLE,
                evidence_metadata={"reason_codes": ["PORTRAIT_ENGINE_PHASE_8"]}))
        return session_id

    async def upload(self, session_id, data=SELFIE, consent="true", headers=None):
        fields = {"biometric_consent": consent} if consent is not None else {}
        raw, content_type = multipart(fields, [("file", "selfie.jpg", "image/jpeg", data)])
        return await call(self.app, f"/v1/kyc/{session_id}/selfie", "POST", raw=raw, content_type=content_type,
                          headers=headers or self.headers)

    def count(self, model):
        with Session(self.engine) as db:
            return db.scalar(sa.select(sa.func.count()).select_from(model))


class BiometricAPITests(BiometricAPICase):
    async def test_accepted_selfie_records_review_evidence_and_waits_for_liveness(self):
        session_id = self.ready()
        before = datetime.now(timezone.utc)
        code, body, headers = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual((body["capture_status"], body["status"], body["next_step"]),
                         ("ACCEPTED", "LIVENESS_REQUIRED", "CAPTURE_LIVENESS"))
        self.assertEqual(body["comparison"]["result"], "REVIEW")
        self.assertEqual(body["comparison"]["metric"], "COSINE_SIMILARITY")
        self.assertAlmostEqual(body["comparison"]["score"], 1)
        self.assertIn("UNCALIBRATED_FACE_POLICY", body["reason_codes"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(self.count(LivenessCheck), 0)
        self.assertNotIn("vector", json.dumps(body))
        self.assertNotIn("threshold", json.dumps(body))
        with Session(self.engine) as db:
            templates = db.scalars(sa.select(BiometricTemplate)).all()
            self.assertEqual({row.source for row in templates}, {"DOCUMENT_PORTRAIT", "LIVE_SELFIE"})
            self.assertEqual(len(templates), 2)
            for row in templates:
                self.assertEqual(row.key_version, "biometric-test-v1")
                self.assertEqual(row.embedding_dimension, 128)
                self.assertNotIn(b'"vector"', row.template_ciphertext)
                context = (f"biometric/{row.organization_id}/{row.session_id}/{row.id}/{row.source}/"
                           f"{row.model_name}/{row.model_version}/{row.model_sha256}")
                payload = self.app.state.biometric_cipher.open(row.template_ciphertext, row.key_version, context)
                self.assertEqual(deserialize_embedding(payload).dimension, 128)
                self.assertLessEqual(row.delete_after.replace(tzinfo=timezone.utc), before + timedelta(hours=1, seconds=10))
            comparison = db.scalar(sa.select(FaceComparison))
            self.assertEqual(comparison.result, CheckResult.REVIEW)
            self.assertEqual(comparison.model_sha256, templates[0].model_sha256)
            self.assertEqual(comparison.evidence_metadata["metric"], "COSINE_SIMILARITY")
            quality = db.scalars(sa.select(FaceQualityCheck)).all()
            self.assertTrue(all(check.result == CheckResult.REVIEW for check in quality))
            self.assertTrue(all("landmarks" not in json.dumps(check.evidence_metadata) for check in quality))
            consent = db.scalar(sa.select(Consent))
            self.assertTrue(consent.granted)
            self.assertEqual(consent.scope, "BIOMETRIC_VERIFICATION")
            portrait = db.scalar(sa.select(DocumentCheck).where(DocumentCheck.check_type == "PORTRAIT"))
            self.assertEqual(portrait.result, CheckResult.REVIEW)
            self.assertNotIn("PORTRAIT_ENGINE_PHASE_8", portrait.evidence_metadata["reason_codes"])
        capture = self.face.reference_image
        self.assertEqual(capture.mode, "RGB")
        pixels = np.asarray(capture)
        self.assertTrue(np.any(pixels[:, :, 0] != pixels[:, :, 2]))

    async def test_document_face_level_advances_only_to_processing(self):
        session_id = self.ready(level="DOCUMENT_FACE")
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual((body["status"], body["next_step"]), ("PROCESSING", "AWAIT_ASSESSMENT"))
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200)
        # Phase 13 decides right after PROCESSING; an uncalibrated face match can never auto-verify.
        self.assertEqual((result["status"], result["decision"]["result"]), ("MANUAL_REVIEW", "REVIEW"))
        self.assertIn("FACE_MATCH_UNCALIBRATED", result["decision"]["reason_codes"])

    async def test_calibrated_failed_match_remains_evidence_without_final_rejection(self):
        self.face.same_identity = False
        self.app.state.face_match_policy = FaceMatchPolicy(version="CALIBRATED-TEST-v1", calibrated=True,
                                                         calibration_reference="test-fixture")
        session_id = self.ready(level="DOCUMENT_FACE")
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["comparison"]["result"], "FAIL")
        self.assertEqual(body["status"], "PROCESSING")
        self.assertEqual(self.count(FaceComparison), 1)

    async def test_explicit_consent_is_required_before_face_processing(self):
        session_id = self.ready()
        for consent in (None, "false"):
            code, body, _ = await self.upload(session_id, consent=consent)
            self.assertEqual(code, 422, body)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(Consent), 0)
        self.assertEqual(self.count(FaceQualityCheck), 0)
        self.assertEqual(self.count(SelfieCapture), 0)

    async def test_quality_recapture_consumes_attempt_without_storing_capture_or_vectors(self):
        self.face.live_reason = "FACE_BLURRED"
        session_id = self.ready()
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["capture_status"], "RECAPTURE")
        self.assertEqual(body["status"], "SELFIE_REQUIRED")
        self.assertEqual(body["attempts_remaining"], self.max_attempts - 1)
        self.assertEqual(body["instructions"], ["KEEP_FACE_STILL"])
        self.assertEqual(self.count(FaceQualityCheck), 1)
        self.assertEqual(self.count(SelfieCapture), 0)
        self.assertEqual(self.count(BiometricTemplate), 0)
        self.assertEqual(self.face.calls, [("assess", "LIVE_SELFIE")])

    async def test_unreadable_input_and_pixel_bombs_are_bounded_before_detection(self):
        session_id = self.ready()
        for data, reason in ((b"bad image", "UNREADABLE_IMAGE"),
                             (encoded(size=(4000, 4000)), "IMAGE_DIMENSIONS_TOO_LARGE")):
            code, body, _ = await self.upload(session_id, data=data)
            self.assertEqual(code, 422, body)
            self.assertEqual(body["reason_code"], reason)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(FaceQualityCheck), 2)

    async def test_reference_detection_failure_reopens_document_capture(self):
        session_id = self.ready()
        self.face.reference_faces = 2
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["capture_status"], "RECAPTURE")
        self.assertEqual(body["status"], "DOCUMENT_REQUIRED")
        self.assertEqual(body["next_step"], "CAPTURE_DATA_PAGE")
        self.assertEqual(body["reason_codes"], ["REFERENCE_FACE_UNAVAILABLE"])
        self.assertEqual(self.count(BiometricTemplate), 0)
        self.assertEqual(self.count(DocumentImage), 0)
        with Session(self.engine) as db:
            document = db.scalar(sa.select(IdentityDocument))
            self.assertIsNone(document.processed_at)
            self.assertIsNone(document.classification_confidence)
            self.assertEqual(db.scalar(sa.select(DocumentCheck.result).where(DocumentCheck.check_type == "PORTRAIT")),
                             CheckResult.FAIL)

    async def test_original_side_mapping_is_honored_for_swapped_card_captures(self):
        session_id = self.ready(document_type="KH_NATIONAL_ID", capture_side="BACK", logical_side="FRONT")
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["capture_status"], "ACCEPTED")
        self.assertEqual(self.count(FaceComparison), 1)

    async def test_model_unavailability_does_not_create_or_advance_evidence(self):
        session_id = self.ready()
        self.face.unavailable = True
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        self.assertEqual(body["reason_code"], "FACE_MODELS_UNAVAILABLE")
        self.assertEqual(self.count(FaceQualityCheck), 0)
        self.assertEqual(self.count(BiometricTemplate), 0)
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, session_id).status, SessionStatus.SELFIE_REQUIRED)

    async def test_embedding_failure_does_not_persist_partial_templates(self):
        session_id = self.ready()
        self.face.embed_unavailable = True
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        for model in (FaceComparison, BiometricTemplate, SelfieCapture, FaceQualityCheck):
            self.assertEqual(self.count(model), 0)

    async def test_biometric_encryption_is_required_before_engine_processing(self):
        session_id = self.ready()
        self.app.state.biometric_cipher = None
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(BiometricTemplate), 0)
        self.assertEqual(self.count(SelfieCapture), 0)

    async def test_large_multipart_body_is_rejected_before_upload_parsing(self):
        session_id = self.ready()
        raw, content_type = multipart({"biometric_consent": "true"}, [("file", "selfie.jpg", "image/jpeg", SELFIE)])
        headers = self.headers | {"content-length": str(self.settings.max_selfie_bytes + 10 * 1024 * 1024)}
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/selfie", "POST", raw=raw,
                                  content_type=content_type, headers=headers)
        self.assertEqual(code, 413, body)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(FaceQualityCheck), 0)

    async def test_valid_large_selfies_never_spool_plaintext_to_disk(self):
        session_id = self.ready()
        pixels = np.random.default_rng(42).integers(0, 256, size=(1600, 1600, 3), dtype=np.uint8)
        image = BytesIO()
        Image.fromarray(pixels).save(image, "JPEG", quality=95)
        data = image.getvalue()
        self.assertGreater(len(data), 1024 * 1024)
        files = []
        original = starlette.formparsers.SpooledTemporaryFile

        def observed(*args, **kwargs):
            file = original(*args, **kwargs)
            files.append(file)
            return file

        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=observed):
            code, body, _ = await self.upload(session_id, data=data)
        self.assertEqual(code, 200, body)
        self.assertTrue(files)
        self.assertTrue(all(not file._rolled for file in files), "A selfie was written to an unencrypted temporary file.")

    async def test_declared_length_cannot_bypass_actual_multipart_body_limit(self):
        session_id = self.ready()
        raw, content_type = multipart({"biometric_consent": "true"},
            [("file", "oversized.jpg", "image/jpeg", b"x" * (self.settings.max_selfie_bytes + 300_000))])
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/selfie", "POST", raw=raw,
            content_type=content_type, headers=self.headers | {"content-length": "100"})
        self.assertEqual(code, 413, body)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(FaceQualityCheck), 0)

    async def test_tampered_reference_ciphertext_returns_retryable_failure(self):
        session_id = self.ready()
        with Session(self.engine) as db:
            image = db.scalar(sa.select(DocumentImage))
            path = self.storage / image.encrypted_object_ref.removeprefix("local://")
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        self.assertEqual(body["reason_code"], "REFERENCE_FACE_UNAVAILABLE")
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, session_id).status, SessionStatus.SELFIE_REQUIRED)
        self.assertEqual(self.count(BiometricTemplate), 0)
        self.assertEqual(self.count(SelfieCapture), 0)

    async def test_expired_reference_original_is_not_used_for_templates(self):
        session_id = self.ready()
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(DocumentImage)).delete_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        self.assertEqual(self.face.calls, [("assess", "LIVE_SELFIE")])
        self.assertEqual(self.count(BiometricTemplate), 0)

    async def test_missing_original_reference_does_not_advance(self):
        session_id = self.ready()
        with Session(self.engine) as db, db.begin():
            db.execute(sa.delete(DocumentImage))
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 503, body)
        self.assertEqual(body["reason_code"], "REFERENCE_FACE_UNAVAILABLE")
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, session_id).status, SessionStatus.SELFIE_REQUIRED)
        self.assertEqual(self.count(BiometricTemplate), 0)

    async def test_cross_tenant_sessions_are_inaccessible(self):
        session_id = self.ready(organization=self.other_org)
        code, _, _ = await self.upload(session_id)
        self.assertEqual(code, 404)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(Consent), 0)

    async def test_authentication_and_stage_are_checked_before_face_processing(self):
        session_id = self.ready(status="DOCUMENT_PROCESSING")
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 409, body)
        code, _, _ = await self.upload(session_id, headers=self.headers | {"X-API-Key": "invalid"})
        self.assertEqual(code, 401)
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(Consent), 0)

    async def test_expired_session_persists_expiry_without_face_processing(self):
        session_id = self.ready()
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            record.created_at, record.expires_at = now - timedelta(hours=1), now - timedelta(seconds=1)
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 409, body)
        self.assertEqual(body["reason_code"], "SESSION_EXPIRED")
        self.assertEqual(self.face.calls, [])
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, session_id).status, SessionStatus.EXPIRED)

    async def test_duplicate_success_is_closed_to_additional_selfies(self):
        session_id = self.ready()
        self.assertEqual((await self.upload(session_id))[0], 200)
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 409, body)
        self.assertEqual(self.count(SelfieCapture), 1)
        self.assertEqual(self.count(BiometricTemplate), 2)
        self.assertEqual(self.count(FaceComparison), 1)
        self.assertEqual(self.count(Consent), 1)

    async def test_retention_removes_biometric_copies_and_cascades_comparison(self):
        session_id = self.ready()
        self.assertEqual((await self.upload(session_id))[0], 200)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            db.execute(sa.update(BiometricTemplate).values(delete_after=now - timedelta(minutes=1)))
            db.execute(sa.update(SelfieCapture).values(delete_after=now - timedelta(minutes=1)))
            db.execute(sa.update(FaceQualityCheck).values(delete_after=now - timedelta(minutes=1)))
        purge_organization(self.app.state.session_factory, self.app.state.capture_store, self.org,
                           now=now + timedelta(hours=1))
        for model in (BiometricTemplate, SelfieCapture, FaceQualityCheck, FaceComparison):
            self.assertEqual(self.count(model), 0)
        self.assertEqual(self.count(DocumentImage), 1)

    async def test_result_hides_expired_templates_before_a_retention_sweep(self):
        session_id = self.ready()
        self.assertEqual((await self.upload(session_id))[0], 200)
        with Session(self.engine) as db, db.begin():
            db.execute(sa.update(BiometricTemplate).values(delete_after=datetime.now(timezone.utc) - timedelta(seconds=1)))
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, body)
        self.assertIsNone(body["face_comparison"])
        self.assertNotIn("face_match", body["checks"])
        self.assertIsNone(body["decision"])
        self.assertEqual(self.count(FaceComparison), 1)  # The read path itself enforces expiry.

    async def test_unavailable_document_check_cannot_be_aggregated_as_pass(self):
        session_id = self.ready()
        with Session(self.engine) as db, db.begin():
            document = db.scalar(sa.select(IdentityDocument))
            db.add_all([
                DocumentCheck(organization_id=self.org, session_id=session_id, document_id=document.id,
                    check_type="OCR_CONFIDENCE", result=CheckResult.UNAVAILABLE,
                    evidence_metadata={"reason_codes": ["OCR_ENGINE_UNAVAILABLE"]}),
                DocumentCheck(organization_id=self.org, session_id=session_id, document_id=document.id,
                    check_type="REQUIRED_FIELDS", result=CheckResult.PASS, evidence_metadata={}),
            ])
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["checks"]["document_data"], "UNAVAILABLE")
        self.assertIn("OCR_ENGINE_UNAVAILABLE", body["review_flags"])

    async def test_result_excludes_quality_from_an_earlier_document_attempt(self):
        session_id = self.ready()
        self.face.reference_faces = 2
        code, recapture, _ = await self.upload(session_id)
        self.assertEqual((code, recapture["status"]), (200, "DOCUMENT_REQUIRED"))
        # Simulate the document processor accepting the customer's replacement.
        image_id = uuid4()
        stored = self.app.state.capture_store.put(self.org, session_id, image_id, DOCUMENT)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            document = db.scalar(sa.select(IdentityDocument))
            document.processed_at, document.classification_confidence = now, .99
            document.side_classification = {"DATA_PAGE": {"side": "DATA_PAGE", "capture_side": "DATA_PAGE"}}
            db.get(KYCSession, session_id).status = SessionStatus.SELFIE_REQUIRED
            db.add(DocumentImage(id=image_id, organization_id=self.org, session_id=session_id, document_id=document.id,
                side="DATA_PAGE", encrypted_object_ref=stored.ref, key_version=stored.key_version,
                media_type="image/jpeg", sha256=stored.sha256, quality_scores={}, quality_policy_version="DOC-TEST",
                delete_after=now + timedelta(hours=2)))
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, body)
        self.assertNotIn("portrait_quality", body["checks"])
        self.assertNotIn("face_quality", body["checks"])
        self.assertNotIn("MULTIPLE_FACES", body["review_flags"])
        self.assertNotIn("REFERENCE_FACE_UNAVAILABLE", body["review_flags"])
        self.assertEqual(self.count(FaceQualityCheck), 1)  # Earlier evidence remains for the audit trail.

    async def test_retention_sweep_keeps_live_selfie_ciphertext(self):
        session_id = self.ready()
        self.assertEqual((await self.upload(session_id))[0], 200)
        with Session(self.engine) as db:
            reference = db.scalar(sa.select(SelfieCapture.encrypted_object_ref))
        purge_organization(self.app.state.session_factory, self.app.state.capture_store, self.org,
                           now=datetime.now(timezone.utc) + timedelta(minutes=20))
        self.assertEqual(self.count(SelfieCapture), 1)
        self.assertEqual(self.count(BiometricTemplate), 2)
        self.assertIn(reference, self.app.state.capture_store.list_refs(self.org))

    async def test_retention_is_scoped_to_the_requested_organization(self):
        own_session, foreign_session = self.ready(), self.ready(organization=self.other_org)
        self.assertEqual((await self.upload(own_session))[0], 200)
        with Session(self.engine) as db, db.begin():
            outcome = submit_selfie(db, TenantContext(self.other_org), foreign_session, SELFIE, self.face,
                self.app.state.capture_store, self.app.state.biometric_cipher, self.app.state.face_match_policy,
                SelfieLimits(self.settings.max_selfie_bytes, self.settings.max_selfie_pixels, self.max_attempts),
                uuid4(), biometric_consent=True)
            self.assertEqual(outcome["capture_status"], "ACCEPTED")
        before = self.app.state.capture_store.list_refs(self.other_org)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            for model in (BiometricTemplate, FaceQualityCheck, SelfieCapture):
                db.execute(sa.update(model).values(delete_after=now - timedelta(seconds=1)))
        purge_organization(self.app.state.session_factory, self.app.state.capture_store, self.org,
                           now=now + timedelta(minutes=20))
        with Session(self.engine) as db:
            for model, expected in ((BiometricTemplate, 2), (FaceQualityCheck, 2), (SelfieCapture, 1), (FaceComparison, 1)):
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(model)
                    .where(model.organization_id == self.other_org)), expected)
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(model)
                    .where(model.organization_id == self.org)), 0)
        self.assertEqual(self.app.state.capture_store.list_refs(self.other_org), before)


class BiometricAttemptLimitTests(BiometricAPICase):
    max_attempts = 2

    async def test_recapture_attempts_are_limited_without_engine_calls_after_limit(self):
        session_id = self.ready()
        self.face.live_reason = "FACE_BLURRED"
        for remaining in (1, 0):
            code, body, _ = await self.upload(session_id)
            self.assertEqual(code, 200, body)
            self.assertEqual(body["attempts_remaining"], remaining)
        self.face.calls.clear()
        code, body, _ = await self.upload(session_id)
        self.assertEqual(code, 429, body)
        self.assertEqual(body["reason_code"], "SELFIE_ATTEMPTS_EXCEEDED")
        self.assertEqual(self.face.calls, [])
        self.assertEqual(self.count(FaceQualityCheck), 2)
