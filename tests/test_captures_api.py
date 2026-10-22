from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID, uuid4

from PIL import Image
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import AuditLog, Base, DocumentCheck, DocumentImage, IdentityDocument, KYCSession, Organization
from kyc.db.session import build_engine
from kyc.domain.enums import SessionStatus
from kyc.main import create_app
from kyc.services.retention import purge_organization
from tests import images
from tests.helpers import call, multipart
from tests.test_api import TEST_KEY, configuration
from tests.test_capture_store import keyring

GOOD = images.encode(images.good())
GOOD_BACK = images.encode(images.scene(images.card(tint=(222, 230, 214))))
PASSPORT = images.encode(images.passport_page())
BLURRED = images.encode(images.blurred())


class CaptureAPICase(unittest.IsolatedAsyncioTestCase):
    max_attempts = 20
    extra_settings: dict = {}

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = Path(self.directory.name)
        self.org, self.other_org = uuid4(), uuid4()
        self.settings = configuration(self.org, capture_encryption_keys=keyring("test-v1"),
                                      capture_storage_dir=self.storage, max_capture_attempts=self.max_attempts,
                                      **self.extra_settings)
        self.engine = build_engine(self.settings)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([Organization(id=self.org, name="Tenant A"), Organization(id=self.other_org, name="Tenant B")])
        self.app = create_app(self.settings, self.engine)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.headers = {"X-API-Key": TEST_KEY, "X-Organization-ID": str(self.org)}

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.engine.dispose()
        self.directory.cleanup()

    async def create(self, document_type="KH_NATIONAL_ID", level="DOCUMENT_FACE_LIVENESS"):
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST",
                                   {"user_id": "capture-test", "country": "KH", "expected_document_type": document_type,
                                    "verification_level": level}, self.headers)
        self.assertEqual(code, 201, body)
        return body["session_id"]

    async def upload(self, session_id, data, path="front", side=None, filename="capture.jpg", content_type="image/jpeg"):
        fields = {"side": side} if side else None
        raw, form_type = multipart(fields, [("file", filename, content_type, data)])
        return await call(self.app, f"/v1/kyc/{session_id}/documents" + (f"/{path}" if path else ""), "POST",
                          raw=raw, content_type=form_type, headers=self.headers)

    def stored_files(self):
        return sorted(self.storage.rglob("*.bin"))


class CaptureAPITests(CaptureAPICase):
    async def test_front_and_back_complete_capture_and_hand_off(self):
        session_id = await self.create()
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["capture_status"], "ACCEPTED")
        self.assertEqual(body["status"], "DOCUMENT_REQUIRED")
        self.assertEqual(body["sides"], {"FRONT": "ACCEPTED", "BACK": "REQUIRED"})
        self.assertEqual(body["next_step"], "CAPTURE_BACK")
        self.assertEqual(body["quality"]["policy_version"], "DOC-CAPTURE-HEURISTIC-2026.10.3")
        self.assertTrue(body["geometry"]["document_detected"])
        code, body, _ = await self.upload(session_id, GOOD_BACK, "back")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "DOCUMENT_PROCESSING")
        self.assertEqual(body["next_step"], "AWAIT_DOCUMENT_PROCESSING")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"], {"document_quality": "PASS"})
        self.assertIsNone(result["decision"])  # no engine has decided anything
        with Session(self.engine) as db:
            record = db.get(KYCSession, UUID(session_id))
            self.assertEqual(record.status, SessionStatus.DOCUMENT_PROCESSING)
            stored = db.scalars(sa.select(DocumentImage)).all()
            self.assertEqual({image.side for image in stored}, {"FRONT", "BACK"})
            self.assertTrue(all(image.key_version == "test-v1" and image.media_type == "image/jpeg" for image in stored))
            document = db.scalar(sa.select(IdentityDocument))
            self.assertIsNone(document.classification_confidence)
            actions = db.scalars(sa.select(AuditLog.action).order_by(AuditLog.created_at)).all()
        self.assertIn("START", actions)
        self.assertIn("DOCUMENT_SUBMITTED", actions)
        self.assertEqual(actions.count("DOCUMENT_CAPTURE_ACCEPTED"), 2)
        self.assertEqual(len(self.stored_files()), 2)
        for path in self.stored_files():
            self.assertNotIn(GOOD[:64], path.read_bytes())
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "CAPTURE_NOT_OPEN")

    async def test_stored_capture_decrypts_to_the_original_upload(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        with Session(self.engine) as db:
            image = db.scalar(sa.select(DocumentImage))
        store = self.app.state.capture_store
        self.assertEqual(store.get(image.encrypted_object_ref, self.org, UUID(session_id), image.id), GOOD)

    async def test_poor_capture_requests_recapture_and_stores_no_image(self):
        session_id = await self.create()
        code, body, _ = await self.upload(session_id, BLURRED, "front")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["capture_status"], "RECAPTURE")
        self.assertIn("IMAGE_BLURRY", body["reason_codes"])
        self.assertIn("HOLD_STILL", body["instructions"])
        self.assertEqual(body["status"], "DOCUMENT_REQUIRED")
        self.assertEqual(body["next_step"], "CAPTURE_FRONT")
        self.assertNotIn("min_blur", json.dumps(body))
        self.assertEqual(self.stored_files(), [])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentImage)), 0)
            check = db.scalar(sa.select(DocumentCheck))
            self.assertEqual(check.result.value, "FAIL")
            self.assertEqual(check.evidence_metadata["outcome"], "RECAPTURE")
            self.assertEqual(len(check.evidence_metadata["sha256"]), 64)

    async def test_recapture_of_a_side_replaces_the_previous_image(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        replacement = images.encode(images.good(), quality=85)
        code, body, _ = await self.upload(session_id, replacement, "front")
        self.assertEqual(body["capture_status"], "ACCEPTED", body)
        with Session(self.engine) as db:
            rows = db.scalars(sa.select(DocumentImage)).all()
        self.assertEqual(len(rows), 1)

    async def test_same_image_cannot_satisfy_both_sides(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        code, body, _ = await self.upload(session_id, GOOD, "back")
        self.assertEqual(body["capture_status"], "RECAPTURE")
        self.assertEqual(body["reason_codes"], ["SAME_IMAGE_FOR_MULTIPLE_SIDES"])
        self.assertEqual(body["status"], "DOCUMENT_REQUIRED")

    async def test_passport_uses_a_single_data_page(self):
        session_id = await self.create("KH_PASSPORT")
        code, body, _ = await self.upload(session_id, PASSPORT, "front")
        self.assertEqual(code, 422)
        code, body, _ = await self.upload(session_id, PASSPORT, None, side="DATA_PAGE")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["sides"], {"DATA_PAGE": "ACCEPTED"})
        self.assertEqual(body["status"], "DOCUMENT_PROCESSING")

    async def test_document_only_level_still_waits_for_the_document_engine(self):
        session_id = await self.create("PASSPORT", "DOCUMENT_ONLY")
        code, body, _ = await self.upload(session_id, PASSPORT, None, side="DATA_PAGE")
        self.assertEqual(body["status"], "DOCUMENT_PROCESSING")

    async def test_invalid_side_and_invalid_files(self):
        session_id = await self.create()
        code, body, _ = await self.upload(session_id, GOOD, None, side="SELFIE")
        self.assertEqual(code, 422)
        gif = BytesIO()
        Image.new("RGB", (32, 32)).save(gif, "GIF")
        for data, reason in [(b"not an image", "UNREADABLE_IMAGE"), (gif.getvalue(), "UNSUPPORTED_FORMAT")]:
            code, body, _ = await self.upload(session_id, data, "front")
            self.assertEqual(code, 422, body)
            self.assertEqual(body["reason_code"], reason)
        with Session(self.engine) as db:
            outcomes = [check.evidence_metadata["outcome"] for check in db.scalars(sa.select(DocumentCheck))]
        self.assertEqual(outcomes, ["REJECTED_INPUT", "REJECTED_INPUT"])

    async def test_oversized_and_unbounded_uploads_are_refused_before_parsing(self):
        session_id = await self.create()
        raw, form_type = multipart(None, [("file", "big.jpg", "image/jpeg", b"0" * 64)])
        headers = self.headers | {"content-length": str(self.settings.max_capture_bytes + 10 * 1024 * 1024)}
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/documents/front", "POST", raw=raw,
                                   content_type=form_type, headers=headers)
        self.assertEqual(code, 413)
        code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", raw=b"{}" + b" " * 70_000, headers=self.headers)
        self.assertEqual(code, 413)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentCheck)), 0)

    async def test_cross_tenant_session_cannot_receive_captures(self):
        foreign = uuid4()
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            db.add(KYCSession(id=foreign, organization_id=self.other_org, user_id="foreign", country="KH",
                              expected_document_type="KH_NATIONAL_ID", verification_level="DOCUMENT_ONLY",
                              status="DOCUMENT_REQUIRED", created_at=now, updated_at=now, expires_at=now + timedelta(minutes=5)))
        code, body, _ = await self.upload(foreign, GOOD, "front")
        self.assertEqual(code, 404)
        self.assertEqual(self.stored_files(), [])

    async def test_unauthenticated_upload_is_rejected(self):
        session_id = await self.create()
        raw, form_type = multipart(None, [("file", "c.jpg", "image/jpeg", GOOD)])
        code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/documents/front", "POST", raw=raw, content_type=form_type,
                                headers={"X-API-Key": "wrong", "X-Organization-ID": str(self.org)})
        self.assertEqual(code, 401)
        self.assertEqual(self.stored_files(), [])

    async def test_expired_session_is_persisted_as_expired(self):
        session_id = await self.create()
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, UUID(session_id))
            record.created_at, record.expires_at = now - timedelta(hours=1), now - timedelta(minutes=1)
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(code, 409)
        self.assertEqual(body["reason_code"], "SESSION_EXPIRED")
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, UUID(session_id)).status, SessionStatus.EXPIRED)
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(body["reason_code"], "SESSION_CLOSED")
        self.assertEqual(self.stored_files(), [])

    async def test_document_types_list_required_sides(self):
        code, body, _ = await call(self.app, "/v1/document-types", headers=self.headers)
        sides = {row["type"]: row["required_sides"] for row in body["document_types"]}
        self.assertEqual(sides["KH_NATIONAL_ID"], ["FRONT", "BACK"])
        self.assertEqual(sides["KH_PASSPORT"], ["DATA_PAGE"])

    async def test_capture_page_is_served_with_a_strict_policy(self):
        code, body, headers = await call(self.app, "/capture/")
        self.assertEqual(code, 200)
        self.assertIn(b"Identity capture", body)
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertIn("camera=(self)", headers["permissions-policy"])


class RetentionTests(CaptureAPICase):
    async def test_expired_captures_and_orphans_are_purged_without_touching_live_ones(self):
        expired_session, live_session = await self.create(), await self.create()
        await self.upload(expired_session, GOOD, "front")
        await self.upload(live_session, GOOD, "front")
        store = self.app.state.capture_store
        orphan = store.put(self.org, UUID(live_session), uuid4(), b"ciphertext without a row")
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            image = db.scalar(sa.select(DocumentImage).where(DocumentImage.session_id == UUID(expired_session)))
            image.delete_after = now - timedelta(minutes=1)
            expired_ref = image.encrypted_object_ref
        factory = self.app.state.session_factory
        fresh = purge_organization(factory, store, self.org)
        self.assertEqual((fresh.expired_images, fresh.deleted_objects), (1, 0))  # orphan still inside grace period
        later = purge_organization(factory, store, self.org, now=now + timedelta(hours=1))
        self.assertEqual(later.deleted_objects, 2)  # the expired image's ciphertext and the orphan
        remaining = {f"local://{p.relative_to(self.storage).as_posix()}" for p in self.stored_files()}
        self.assertNotIn(expired_ref, remaining)
        self.assertNotIn(orphan.ref, remaining)
        self.assertEqual(len(remaining), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentImage)), 1)

    async def test_expired_document_cascades_to_its_images_and_checks(self):
        session_id = await self.create()
        await self.upload(session_id, GOOD, "front")
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(IdentityDocument)).delete_after = datetime.now(timezone.utc) - timedelta(minutes=1)
        report = purge_organization(self.app.state.session_factory, self.app.state.capture_store, self.org)
        self.assertEqual(report.expired_documents, 1)
        with Session(self.engine) as db:
            for model in (IdentityDocument, DocumentImage, DocumentCheck):
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(model)), 0)


class CaptureLimitTests(CaptureAPICase):
    max_attempts = 2

    async def test_attempt_limit_is_enforced_and_audited(self):
        session_id = await self.create()
        for expected in [1, 0]:
            code, body, _ = await self.upload(session_id, BLURRED, "front")
            self.assertEqual(body["attempts_remaining"], expected)
        code, body, _ = await self.upload(session_id, GOOD, "front")
        self.assertEqual(code, 429)
        self.assertEqual(body["reason_code"], "CAPTURE_ATTEMPTS_EXCEEDED")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(AuditLog)
                                       .where(AuditLog.action == "CAPTURE_ATTEMPTS_EXCEEDED")), 1)


class UnconfiguredStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_is_unavailable_without_capture_keys(self):
        org = uuid4()
        settings = configuration(org)
        engine = build_engine(settings)
        Base.metadata.create_all(engine)
        with Session(engine) as db, db.begin():
            db.add(Organization(id=org, name="Tenant"))
        app = create_app(settings, engine)
        async with app.router.lifespan_context(app):
            headers = {"X-API-Key": TEST_KEY, "X-Organization-ID": str(org)}
            code, body, _ = await call(app, "/v1/kyc/sessions", "POST", {"user_id": "u", "country": "KH",
                                       "expected_document_type": "KH_NATIONAL_ID"}, headers)
            raw, form_type = multipart(None, [("file", "c.jpg", "image/jpeg", GOOD)])
            code, _, _ = await call(app, f"/v1/kyc/{body['session_id']}/documents/front", "POST", raw=raw,
                                    content_type=form_type, headers=headers)
            self.assertEqual(code, 503)
        engine.dispose()
