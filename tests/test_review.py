"""Phase 14: reviewer access, queue, case view, images, decisions and the dashboard shell."""

from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.crypto import FieldCipher
from kyc.db.models import AuditLog, DocumentField, DocumentImage, IdentityDocument, KYCSession, ManualReview, Reviewer
from kyc.review.access import new_token
from tests.helpers import call
from tests.test_biometrics_api import DOCUMENT, BiometricAPICase
from tests.test_capture_store import keyring
from tests import test_risk


class ReviewCase(BiometricAPICase):
    processing = test_risk.RiskAPITests.processing

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cipher = FieldCipher(keyring("pii-test-v1"), os.urandom(32))
        self.app.state.field_cipher = self.cipher
        self.reviewer, self.reviewer_id = self.make_reviewer("Sok Dara", "REVIEWER")
        self.auditor, _ = self.make_reviewer("Audit Team", "AUDITOR")

    def make_reviewer(self, name, role, organization=None, active=True):
        token, digest = new_token()
        reviewer_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(Reviewer(id=reviewer_id, organization_id=organization or self.org, display_name=name, role=role,
                            token_sha256=digest, active=active))
        return {"Authorization": f"Bearer {token}", "X-Organization-ID": str(organization or self.org)}, reviewer_id

    async def in_review(self, level="DOCUMENT_ONLY", **options):
        """A case the risk engine sent to MANUAL_REVIEW (document-only without authenticity evidence)."""
        session_id = self.processing(level=level, signed_barcode=False, **options)
        with Session(self.engine) as db, db.begin():
            document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.session_id == session_id))
            context = f"field/{self.org}/{session_id}/{document.id}/full_name/normalized"
            sealed, version = self.cipher.seal("SOK SOPHEA", context)
            db.add(DocumentField(organization_id=self.org, session_id=session_id, document_id=document.id,
                                 field_name="full_name", normalized_value_ciphertext=sealed, key_version=version,
                                 confidence=.93, source="OCR"))
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/verify", "POST", body={}, headers=self.headers)
        self.assertEqual(body["status"], "MANUAL_REVIEW", body)
        return session_id

    async def get(self, path, headers):
        return await call(self.app, path, headers=headers)

    async def decide(self, session_id, headers=None, version=None, **body):
        if version is None:
            code, case, _ = await self.get(f"/v1/review/{session_id}", headers or self.reviewer)
            version = case["session"]["version"]
        payload = {"action": "APPROVE", "reason_code": "DOCUMENT_CONFIRMED_GENUINE",
                   "note": "Checked the card security features by video call.", "expected_version": version, **body}
        return await call(self.app, f"/v1/review/{session_id}/decision", "POST", body=payload, headers=headers or self.reviewer)


class ReviewAccessTests(ReviewCase):
    async def test_review_and_client_credentials_never_cross(self):
        self.assertEqual((await self.get("/v1/review/queue", {}))[0], 401)
        self.assertEqual((await self.get("/v1/review/queue", self.headers))[0], 401)  # client API key
        session_id = self.ready()
        self.assertEqual((await self.get(f"/v1/kyc/{session_id}", self.reviewer))[0], 401)  # reviewer token on client API
        code, me, _ = await self.get("/v1/review/me", self.reviewer)
        self.assertEqual((code, me["role"], me["display_name"]), (200, "REVIEWER", "Sok Dara"))

    async def test_tokens_are_scoped_to_one_organization_and_can_be_revoked(self):
        wrong_org = {**self.reviewer, "X-Organization-ID": str(self.other_org)}
        self.assertEqual((await self.get("/v1/review/me", wrong_org))[0], 401)
        inactive, _ = self.make_reviewer("Former", "REVIEWER", active=False)
        self.assertEqual((await self.get("/v1/review/me", inactive))[0], 401)
        foreign_reviewer, _ = self.make_reviewer("Tenant B reviewer", "REVIEWER", organization=self.other_org)
        session_id = await self.in_review()
        self.assertEqual((await self.get(f"/v1/review/{session_id}", foreign_reviewer))[0], 404)
        code, queue, _ = await self.get("/v1/review/queue", foreign_reviewer)
        self.assertEqual(queue["total"], 0)
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalar(sa.select(Reviewer.token_sha256).limit(1))), 64)  # hash only


class ReviewQueueAndCaseTests(ReviewCase):
    async def test_queue_lists_only_cases_waiting_for_review(self):
        first = await self.in_review()
        second = await self.in_review()
        self.ready(status="SELFIE_REQUIRED")
        code, queue, _ = await self.get("/v1/review/queue", self.auditor)
        self.assertEqual(code, 200)
        self.assertEqual([item["session_id"] for item in queue["items"]], [str(first), str(second)])  # oldest first
        self.assertEqual(queue["items"][0]["reason_codes"], ["DOCUMENT_AUTHENTICITY_UNVERIFIED"])

    async def test_reviewer_sees_identity_and_photos_and_every_view_is_audited(self):
        session_id = await self.in_review()
        code, case, _ = await self.get(f"/v1/review/{session_id}", self.reviewer)
        self.assertEqual(code, 200, case)
        self.assertEqual(next(item["value"] for item in case["fields"] if item["name"] == "full_name"), "SOK SOPHEA")
        self.assertEqual(case["risk"]["reason_codes"], ["DOCUMENT_AUTHENTICITY_UNVERIFIED"])
        self.assertIn("APPROVE", case["decision_options"])
        image = case["images"][0]
        code, data, headers = await call(self.app, f"/v1/review/{session_id}/images/{image['image_id']}",
                                         headers=self.reviewer)
        self.assertEqual((code, data), (200, DOCUMENT))
        self.assertEqual(headers["cache-control"], "no-store")
        with Session(self.engine) as db:
            actions = list(db.scalars(sa.select(AuditLog.action).where(AuditLog.actor_id == f"reviewer:{self.reviewer_id}")))
        self.assertEqual(actions, ["REVIEW_CASE_VIEWED", "REVIEW_IMAGE_VIEWED"])

    async def test_auditor_sees_evidence_but_no_identity_photos_or_decisions(self):
        session_id = await self.in_review()
        code, case, _ = await self.get(f"/v1/review/{session_id}", self.auditor)
        self.assertEqual(code, 200)
        self.assertEqual({item["value"] for item in case["fields"]}, {None})
        self.assertEqual((case["images"], case["decision_options"]), ([], {}))
        self.assertNotIn("SOK SOPHEA", json.dumps(case))
        self.assertIn("checks", case)
        with Session(self.engine) as db:
            image_id = db.scalar(sa.select(DocumentImage.id).where(DocumentImage.session_id == session_id))
        self.assertEqual((await self.get(f"/v1/review/{session_id}/images/{image_id}", self.auditor))[0], 403)
        code, body, _ = await self.decide(session_id, headers=self.auditor, version=1)
        self.assertEqual(code, 403)

    async def test_photos_removed_by_retention_are_not_shown(self):
        session_id = await self.in_review()
        with Session(self.engine) as db, db.begin():
            image = db.scalar(sa.select(DocumentImage).where(DocumentImage.session_id == session_id))
            image.delete_after = datetime.now(timezone.utc) - timedelta(minutes=1)
            image_id = image.id
        code, case, _ = await self.get(f"/v1/review/{session_id}", self.reviewer)
        self.assertEqual(case["images"], [])
        self.assertEqual((await self.get(f"/v1/review/{session_id}/images/{image_id}", self.reviewer))[0], 404)


class ReviewDecisionTests(ReviewCase):
    async def test_approve_verifies_and_the_client_sees_the_outcome_not_the_note(self):
        session_id = await self.in_review()
        code, body, _ = await self.decide(session_id)
        self.assertEqual((code, body["status"]), (200, "VERIFIED"), body)
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual((result["status"], result["review"]["action"], result["review"]["reason_code"]),
                         ("VERIFIED", "APPROVE", "DOCUMENT_CONFIRMED_GENUINE"))
        self.assertEqual(result["decision"]["result"], "REVIEW")  # the engine's decision is kept as it was
        self.assertNotIn("video call", json.dumps(result))
        with Session(self.engine) as db:
            row = db.scalar(sa.select(ManualReview))
            self.assertNotIn(b"video call", row.reason_ciphertext)
            self.assertEqual((row.reviewer_id, row.session_version), (str(self.reviewer_id), 2))  # the version opened
            audit = db.scalar(sa.select(AuditLog).where(AuditLog.action == "REVIEW_DECISION"))
            self.assertEqual((audit.from_status, audit.to_status, audit.reason_codes),
                             ("MANUAL_REVIEW", "VERIFIED", ["DOCUMENT_CONFIRMED_GENUINE"]))
        code, case, _ = await self.get(f"/v1/review/{session_id}", self.reviewer)
        self.assertEqual(case["history"][0]["note"], "Checked the card security features by video call.")
        code, case, _ = await self.get(f"/v1/review/{session_id}", self.auditor)
        self.assertIsNone(case["history"][0]["note"])  # notes are identity-level information
        code, body, _ = await self.decide(session_id, version=case["session"]["version"], action="REJECT",
                                          reason_code="POLICY_NOT_MET")
        self.assertEqual((code, body["reason_code"]), (409, "CASE_NOT_IN_REVIEW"))

    async def test_stale_versions_reasons_and_notes_are_enforced(self):
        session_id = await self.in_review()
        code, body, _ = await self.decide(session_id, version=1)
        self.assertEqual((code, body["reason_code"]), (409, "CASE_CHANGED"))
        code, body, _ = await self.decide(session_id, reason_code="FACE_DIFFERENT_PERSON")  # a REJECT reason
        self.assertEqual((code, body["reason_code"]), (422, "REASON_CODE_NOT_ALLOWED"))
        code, body, _ = await self.decide(session_id, note="ok")
        self.assertEqual(code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.get(KYCSession, session_id).status.value, "MANUAL_REVIEW")

    async def test_missing_evidence_cannot_be_approved_only_rejected_or_recaptured(self):
        session_id = await self.in_review(level="DOCUMENT_FACE")  # a face level with no face evidence at all
        code, body, _ = await self.decide(session_id)
        self.assertEqual((code, body["reason_code"]), (409, "APPROVAL_BLOCKED"))
        self.assertIn("EVIDENCE_MISSING_FACE_MATCH", body["blockers"])
        code, body, _ = await self.decide(session_id, action="REJECT", reason_code="POLICY_NOT_MET")
        self.assertEqual((code, body["status"]), (200, "REJECTED"))

    async def test_recapture_reopens_the_session_with_clean_evidence_and_kept_history(self):
        session_id = await self.in_review()
        code, body, _ = await self.decide(session_id, action="REQUEST_RECAPTURE", reason_code="GLARE_OR_BLUR",
                                          note="Glare covers the date of birth.")
        self.assertEqual((code, body["status"]), (200, "DOCUMENT_REQUIRED"))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentImage)), 0)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(DocumentField)), 0)
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(ManualReview)), 1)
        code, queue, _ = await self.get("/v1/review/queue", self.reviewer)
        self.assertEqual(queue["total"], 0)


class ReviewDashboardTests(ReviewCase):
    async def test_dashboard_is_served_with_a_strict_policy(self):
        code, page, headers = await call(self.app, "/review/")
        self.assertEqual(code, 200)
        csp = headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-inline", csp)
        page = page.decode()
        self.assertIn('<script src="review.js"></script>', page)
        self.assertNotIn("<script>", page)
        script = (await call(self.app, "/review/review.js"))[1].decode()
        self.assertNotIn("localStorage", script)
        self.assertNotIn("innerHTML", script)  # all API values are rendered as text
