"""Key retirement must survive interrupted writes and count orphaned captures."""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.exceptions import InvalidTag
import sqlalchemy as sa
from sqlalchemy.orm import SessionTransaction, sessionmaker

from kyc.core.crypto import FieldCipher
from kyc.db.models import Base, KYCSession, ManualReview, Organization, SelfieCapture, WebhookEndpoint
from kyc.services.rekey import Keyrings, rekey_organization, retired_versions_in_use
from kyc.storage.captures import LocalEncryptedCaptureStore
from kyc.webhooks.dispatcher import secret_context
from kyc.webhooks.secrets import WebhookSecretCipher


def _ring(version, key):
    return f"{version}:{base64.b64encode(key).decode()}"


class RekeyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = sa.create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.org, self.other_org, self.session, self.other_session = (uuid4() for _ in range(4))
        self.old_key, self.new_key = b"o" * 32, b"n" * 32
        root = Path(self.directory.name)
        self.old_store = LocalEncryptedCaptureStore(root, "old", {"old": self.old_key})
        self.store = LocalEncryptedCaptureStore(root, "new", {"new": self.new_key, "old": self.old_key})
        now = datetime.now(timezone.utc)
        with self.factory() as db, db.begin():
            db.add_all([Organization(id=self.org, name="Rotation test"),
                        Organization(id=self.other_org, name="Foreign tenant")])
            db.flush()
            for org, identity in ((self.org, self.session), (self.other_org, self.other_session)):
                db.add(KYCSession(id=identity, organization_id=org, user_id="synthetic", country="KH",
                                  expected_document_type="KH_NATIONAL_ID", verification_level="DOCUMENT_ONLY",
                                  status="CREATED", created_at=now, expires_at=now + timedelta(minutes=10)))

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def capture(self, organization=None, session=None):
        organization, session = organization or self.org, session or self.session
        identity = uuid4()
        stored = self.old_store.put(organization, session, identity, b"synthetic capture")
        with self.factory() as db, db.begin():
            db.add(SelfieCapture(id=identity, organization_id=organization, session_id=session,
                                 encrypted_object_ref=stored.ref, key_version=stored.key_version,
                                 media_type="image/jpeg", sha256=stored.sha256, quality_scores={},
                                 quality_policy_version="test", delete_after=datetime.now(timezone.utc) + timedelta(hours=1)))
        return identity, stored.ref

    def captures(self, apply=False):
        return rekey_organization(self.factory, self.org, Keyrings(capture_store=self.store), apply=apply,
                                  classes=("capture_objects",))

    def test_inventory_uses_authenticated_envelope_instead_of_database_hint(self):
        identity, ref = self.capture()
        with self.factory() as db, db.begin():
            db.get(SelfieCapture, identity).key_version = "new"
        original = self.store._path(ref).read_bytes()
        inventory = self.captures()["capture_objects"]
        self.assertEqual(inventory.before, {"old": 1})
        self.assertEqual(self.store._path(ref).read_bytes(), original)
        report = self.captures(apply=True)["capture_objects"]
        self.assertEqual(report.resealed, 1)
        self.assertEqual(self.store.get_with_version(ref, self.org, self.session, identity),
                         (b"synthetic capture", "new"))

    def test_failed_database_commit_leaves_envelope_readable_and_retry_repairs_metadata(self):
        identity, ref = self.capture()
        with patch.object(SessionTransaction, "commit", side_effect=RuntimeError("synthetic transaction failure")):
            with self.assertRaises(RuntimeError):
                self.captures(apply=True)
        with self.factory() as db:
            self.assertEqual(db.get(SelfieCapture, identity).key_version, "old")
        self.assertEqual(self.store.get_with_version(ref, self.org, self.session, identity)[1], "new")
        self.assertEqual(retired_versions_in_use(self.captures()), {"capture_objects": []})
        repaired = self.captures(apply=True)["capture_objects"]
        self.assertEqual((repaired.resealed, repaired.metadata_repaired), (0, 1))
        with self.factory() as db:
            self.assertEqual(db.get(SelfieCapture, identity).key_version, "new")

    def test_orphaned_envelopes_block_key_retirement_until_removed(self):
        identity, ref = self.capture()
        orphan_id = uuid4()
        orphan = self.old_store.put(self.org, self.session, orphan_id, b"orphaned synthetic capture")
        foreign_id, foreign_ref = self.capture(self.other_org, self.other_session)
        inventory = self.captures()["capture_objects"]
        self.assertEqual(inventory.before, {"old": 2})
        self.assertEqual(inventory.unreferenced, {"old": 1})
        self.captures(apply=True)
        after = self.captures()
        self.assertEqual(after["capture_objects"].before, {"new": 1, "old": 1})
        self.assertEqual(retired_versions_in_use(after), {"capture_objects": ["old"]})
        self.assertEqual(self.store.get_with_version(orphan.ref, self.org, self.session, orphan_id)[1], "old")
        self.assertEqual(self.store.get_with_version(foreign_ref, self.other_org, self.other_session, foreign_id)[1], "old")
        self.store.delete(orphan.ref)
        self.assertEqual(retired_versions_in_use(self.captures()), {"capture_objects": []})
        retired = LocalEncryptedCaptureStore(self.store.root, "new", {"new": self.new_key})
        self.assertEqual(retired.get(ref, self.org, self.session, identity), b"synthetic capture")

    def test_forged_object_reference_cannot_supply_another_rows_aad(self):
        identity, original_ref = self.capture()
        other_id = uuid4()
        substituted = self.old_store.put(self.org, self.session, other_id, b"substituted synthetic capture")
        with self.factory() as db, db.begin():
            db.get(SelfieCapture, identity).encrypted_object_ref = substituted.ref
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(InvalidTag):
                self.captures(apply=apply)
        self.assertEqual(self.old_store.get(original_ref, self.org, self.session, identity), b"synthetic capture")

    def test_tampered_orphan_makes_inventory_fail_closed(self):
        self.capture()
        orphan = self.old_store.put(self.org, self.session, uuid4(), b"orphan")
        path = self.store._path(orphan.ref)
        sealed = path.read_bytes()
        path.write_bytes(sealed[:-1] + bytes([sealed[-1] ^ 1]))
        with self.assertRaises(InvalidTag):
            self.captures()

    def test_capture_namespace_discovery_includes_tenants_without_database_rows(self):
        orphan_org, orphan_session, orphan_id = uuid4(), uuid4(), uuid4()
        self.old_store.put(orphan_org, orphan_session, orphan_id, b"orphaned tenant capture")
        self.assertEqual(self.store.list_organization_ids(), [orphan_org])
        report = rekey_organization(self.factory, orphan_org, Keyrings(capture_store=self.store), apply=False,
                                    classes=("capture_objects",))["capture_objects"]
        self.assertEqual((report.before, report.unreferenced), ({"old": 1}, {"old": 1}))

    def test_erased_review_notes_are_skipped_and_other_tenant_notes_are_untouched(self):
        old = FieldCipher(_ring("old", self.old_key), b"lookup key")
        new = FieldCipher(_ring("new", self.new_key) + "," + _ring("old", self.old_key), b"lookup key")
        identities = [uuid4(), uuid4(), uuid4()]
        with self.factory() as db, db.begin():
            for identity, org, session, note in ((identities[0], self.org, self.session, None),
                                                (identities[1], self.org, self.session, "synthetic note"),
                                                (identities[2], self.other_org, self.other_session, "foreign note")):
                sealed, version = old.seal(note, f"review/{org}/{session}/{identity}/note") if note else (None, None)
                db.add(ManualReview(id=identity, organization_id=org, session_id=session, reviewer_id="synthetic",
                                    action="APPROVE", reason_code="TEST", reason_ciphertext=sealed, key_version=version))
        reports = rekey_organization(self.factory, self.org, Keyrings(field_cipher=new), classes=("review_notes",))
        self.assertEqual((reports["review_notes"].before, reports["review_notes"].resealed), ({"old": 1}, 1))
        with self.factory() as db:
            erased, rotated, foreign = (db.get(ManualReview, identity) for identity in identities)
            self.assertEqual((erased.reason_ciphertext, erased.key_version), (None, None))
            self.assertEqual(new.open(rotated.reason_ciphertext, rotated.key_version,
                                      f"review/{self.org}/{self.session}/{rotated.id}/note"), "synthetic note")
            self.assertEqual(foreign.key_version, "old")

    def test_webhook_current_and_previous_secrets_move_to_dedicated_keys(self):
        legacy = FieldCipher(_ring("v1", self.old_key), b"")
        dedicated_old = WebhookSecretCipher(_ring("v1", b"w" * 32), legacy)
        dedicated_new = WebhookSecretCipher(_ring("v2", b"x" * 32) + "," + _ring("v1", b"w" * 32), legacy)
        identity = uuid4()
        context = secret_context(self.org, identity)
        current, current_version = legacy.seal("synthetic current", context)
        previous, previous_version = dedicated_old.seal("synthetic previous", context)
        with self.factory() as db, db.begin():
            db.add(WebhookEndpoint(id=identity, organization_id=self.org, url="https://example.com/hooks",
                                   event_types=["session.completed"], secret_ciphertext=current, key_version=current_version,
                                   previous_secret_ciphertext=previous, previous_key_version=previous_version,
                                   created_by="test"))
        report = rekey_organization(self.factory, self.org, Keyrings(webhook_cipher=dedicated_new),
                                    classes=("webhook_secrets",))["webhook_secrets"]
        self.assertEqual((report.before, report.resealed), ({"v1": 1, "wh:v1": 1}, 2))
        only_new = WebhookSecretCipher(_ring("v2", b"x" * 32), None)
        with self.factory() as db:
            row = db.get(WebhookEndpoint, identity)
            self.assertEqual((row.key_version, row.previous_key_version), ("wh:v2", "wh:v2"))
            self.assertEqual(only_new.open(row.secret_ciphertext, row.key_version, context), "synthetic current")
            self.assertEqual(only_new.open(row.previous_secret_ciphertext, row.previous_key_version, context),
                             "synthetic previous")
