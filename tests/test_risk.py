"""Phase 13: deterministic risk policy, evaluation, state change and POST /verify."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import SecretStr, ValidationError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import AuditLog, BarcodeResult, DocumentCheck, IdentityDocument, KYCSession, RiskAssessmentRecord
from kyc.domain.enums import CheckResult, VerificationLevel as L
from kyc.risk.engine import RiskInputs, RiskOutcome, evaluate
from kyc.risk.policy import DEFAULT_VERSION, PolicyFile, RiskPolicy
from kyc.domain.state_machine import VerificationEvidence
from tests.helpers import call
from tests.test_biometrics_api import BiometricAPICase

ALL_PASS = {"document_quality": "PASS", "document_classification": "PASS", "document_data": "PASS", "expiry": "PASS",
            "cross_check": "PASS", "fraud": "PASS", "portrait_quality": "PASS", "face_quality": "PASS",
            "face_match": "PASS", "liveness": "PASS", "nfc": "PASS", "nfc_status": "NFC_VERIFIED", "mrz": "PASS"}
GENUINE = ("NFC_CHIP_VERIFIED",)


def run(level=L.DOCUMENT_FACE_LIVENESS_NFC, policy=None, signals=(), calibrated=True, sources=GENUINE, **changes):
    checks = {**ALL_PASS, **changes}
    for name in [name for name, value in checks.items() if value is None]:
        del checks[name]
    resolved = (policy or RiskPolicy()).resolve(level, "KH", "KH_PASSPORT")
    return evaluate(RiskInputs(level, checks, tuple(signals), calibrated, sources), resolved)


class RiskEngineTests(unittest.TestCase):
    def test_spec_pass_with_reason_codes(self):
        outcome = run()
        self.assertEqual((outcome.decision, outcome.reason_codes),
                         ("PASS", ["DOCUMENT_VALID", "FACE_MATCH", "LIVENESS_PASS", "NFC_VERIFIED", "NO_FRAUD_SIGNALS"]))
        self.assertTrue(outcome.evidence.complete_for(L.DOCUMENT_FACE_LIVENESS_NFC))
        self.assertEqual((outcome.policy_version, outcome.trace), (DEFAULT_VERSION, []))

    def test_spec_fail_reasons_and_fail_beats_review(self):
        cases = {"liveness": "LIVENESS_FAILED", "face_match": "FACE_MISMATCH", "expiry": "EXPIRED_DOCUMENT",
                 "fraud": "HIGH_RISK_TAMPER_SIGNAL"}
        for check, reason in cases.items():
            with self.subTest(check=check):
                outcome = run(**{check: "FAIL"}, document_data="REVIEW")
                self.assertEqual((outcome.decision, outcome.reason_codes), ("FAIL", [reason]))
                self.assertIn("LOW_OCR_CONFIDENCE", [item["reason"] for item in outcome.trace])  # still traced
                self.assertFalse(outcome.evidence.policy_pass)

    def test_spec_review_reasons(self):
        self.assertEqual(run(document_data="REVIEW").reason_codes, ["LOW_OCR_CONFIDENCE"])
        self.assertEqual(run(cross_check="REVIEW").reason_codes, ["FIELD_MISMATCH"])
        self.assertEqual(run(face_match="REVIEW", calibrated=False).reason_codes,
                         ["FACE_SCORE_BORDERLINE", "FACE_MATCH_UNCALIBRATED"])
        self.assertEqual(run(some_future_check="FAIL").reason_codes, ["CHECK_SOME_FUTURE_CHECK_FAIL"])  # unknown → REVIEW, not FAIL

    def test_required_evidence_for_the_level(self):
        self.assertEqual(run(liveness=None).reason_codes, ["EVIDENCE_MISSING_LIVENESS"])
        self.assertEqual(run(nfc="UNAVAILABLE").reason_codes, ["NFC_UNAVAILABLE"])
        self.assertEqual(run(face_match="NOT_APPLICABLE").reason_codes, ["FACE_MATCH_NOT_APPLICABLE"])
        self.assertEqual(run(expiry="NOT_APPLICABLE").decision, "PASS")          # e.g. NSSF prints no expiry
        self.assertEqual(run(document_portrait="UNAVAILABLE").decision, "PASS")  # not required, nothing to say
        self.assertEqual(run(level=L.DOCUMENT_ONLY, liveness=None, face_match=None, nfc=None).reason_codes,
                         ["DOCUMENT_VALID", "NO_FRAUD_SIGNALS"])

    def test_uncalibrated_face_pass_is_never_a_match(self):
        outcome = run(calibrated=False)
        self.assertEqual((outcome.decision, outcome.reason_codes), ("REVIEW", ["FACE_MATCH_UNCALIBRATED"]))
        self.assertFalse(outcome.evidence.policy_pass)

    def test_same_evidence_and_policy_always_give_the_same_decision(self):
        variants = [{}, {"document_data": "REVIEW"}, {"liveness": "FAIL"}, {"nfc": None},
                    {"face_match": "REVIEW", "calibrated": False}]
        for changes in variants:
            with self.subTest(changes=changes):
                outcomes = {(o.decision, tuple(o.reason_codes), repr(o.trace)) for o in (run(**changes) for _ in range(25))}
                self.assertEqual(len(outcomes), 1)

    def test_reading_is_not_authenticating(self):
        outcome = run(sources=())
        self.assertEqual((outcome.decision, outcome.reason_codes), ("REVIEW", ["DOCUMENT_AUTHENTICITY_UNVERIFIED"]))

    def test_signals(self):
        self.assertEqual(run(signals=[("NFC_ISSUER_NOT_TRUSTED", "CONTEXT", "LOW")]).reason_codes[-1], "ONLY_LOW_FRAUD_SIGNALS")
        self.assertEqual(run(signals=[("DOCUMENT_VELOCITY", "DUPLICATE", "MEDIUM")]).reason_codes, ["FRAUD_DOCUMENT_VELOCITY"])
        tamper = run(signals=[("NFC_CHIP_CLONE_SUSPECTED", "TAMPER", "HIGH"), ("DOCUMENT_VELOCITY", "DUPLICATE", "MEDIUM")])
        self.assertEqual((tamper.decision, tamper.reason_codes), ("FAIL", ["FRAUD_NFC_CHIP_CLONE_SUSPECTED"]))

    def test_deterministic(self):
        inputs = dict(signals=[("DOCUMENT_VELOCITY", "DUPLICATE", "MEDIUM")], cross_check="REVIEW")
        first, second = run(**inputs), run(**inputs)
        self.assertEqual((first.decision, first.reason_codes, first.trace), (second.decision, second.reason_codes, second.trace))


class RiskPolicyTests(unittest.TestCase):
    def policy(self, overrides):
        return RiskPolicy(PolicyFile.model_validate({"version": "TENANT-STRICT-1", "overrides": overrides}))

    def test_overrides_tighten_per_country_and_document(self):
        policy = self.policy([{"country": "KH", "document_type": "KH_PASSPORT", "require": ["barcode"],
                               "signal_rules": {"DOCUMENT_USED_BY_ANOTHER_USER": "FAIL", "*:LOW": "REVIEW"},
                               "check_rules": {"issuing_country:REVIEW": "FAIL"}}])
        self.assertEqual(run(policy=policy).reason_codes, ["EVIDENCE_MISSING_BARCODE"])
        duplicate = run(policy=policy, barcode="PASS", signals=[("DOCUMENT_USED_BY_ANOTHER_USER", "DUPLICATE", "HIGH")])
        self.assertEqual((duplicate.decision, duplicate.policy_version), ("FAIL", "TENANT-STRICT-1"))
        self.assertEqual(run(policy=policy, barcode="PASS", issuing_country="REVIEW").decision, "FAIL")
        self.assertEqual(run(policy=policy, barcode="PASS", signals=[("X", "CONTEXT", "LOW")]).decision, "REVIEW")
        other = policy.resolve(L.DOCUMENT_ONLY, "TH", "PASSPORT")
        self.assertEqual((other.overrides, "barcode" in other.required), ((), False))

    def test_overrides_can_never_loosen(self):
        for bad in ({"check_rules": {"expiry:FAIL": "REVIEW"}}, {"check_rules": {"liveness:FAIL": "IGNORE"}},
                    {"signal_rules": {"*:HIGH": "IGNORE"}}, {"unknown_key": True}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.policy([bad])
        # A REVIEW rule written against a tamper signal is valid syntax, but the floor still fails it.
        policy = self.policy([{"signal_rules": {"NFC_CHIP_CLONE_SUSPECTED": "REVIEW", "TAMPER:HIGH": "REVIEW"}}])
        self.assertEqual(run(policy=policy, signals=[("NFC_CHIP_CLONE_SUSPECTED", "TAMPER", "HIGH")]).decision, "FAIL")

    def test_policy_file_is_versioned_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps({"version": "KH-2026.10", "overrides": [{"country": "KH", "require": ["mrz"]}]}))
            policy = RiskPolicy.load(path)
        self.assertEqual((policy.version, len(policy.digest)), ("KH-2026.10", 16))
        self.assertEqual(RiskPolicy.load(None).version, DEFAULT_VERSION)


class RiskAPITests(BiometricAPICase):
    def processing(self, level="DOCUMENT_ONLY", signed_barcode=True, organization=None):
        """A PROCESSING session whose stored document evidence is all PASS."""
        session_id = self.ready(status="PROCESSING", level=level, organization=organization)
        org = organization or self.org
        with Session(self.engine) as db, db.begin():
            document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.session_id == session_id))
            for check_type in ("CAPTURE_QUALITY", "CLASSIFICATION", "EXPIRY", "REQUIRED_FIELDS"):
                db.add(DocumentCheck(organization_id=org, session_id=session_id, document_id=document.id,
                                     check_type=check_type, result=CheckResult.PASS, evidence_metadata={"reason_codes": []}))
            db.add(BarcodeResult(organization_id=org, session_id=session_id, document_id=document.id, symbology="QRCODE",
                                 decoded=True, format_valid=True, signature_present=signed_barcode,
                                 signature_valid=True if signed_barcode else None,
                                 data_consistency={"side": "DATA_PAGE", "fields": {"document_number": "MATCH"}}))
        return session_id

    async def verify(self, session_id, headers=None):
        return await call(self.app, f"/v1/kyc/{session_id}/verify", "POST", body={}, headers=headers or self.headers)

    async def test_authenticated_document_passes_and_is_verified(self):
        session_id = self.processing()
        code, body, _ = await self.verify(session_id)
        self.assertEqual(code, 200, body)
        self.assertEqual((body["status"], body["decision"]["result"]), ("VERIFIED", "PASS"))
        self.assertEqual(body["decision"]["reason_codes"], ["DOCUMENT_VALID", "NO_FRAUD_SIGNALS"])
        self.assertEqual(body["decision"]["policy_version"], DEFAULT_VERSION)
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["decision"], body["decision"])
        with Session(self.engine) as db:
            row = db.scalar(sa.select(RiskAssessmentRecord))
            self.assertEqual(row.check_summary["authenticity_sources"], ["SIGNED_BARCODE_VERIFIED"])
            self.assertEqual(row.check_summary["trace"], [])
            audit = db.scalar(sa.select(AuditLog).where(AuditLog.action == "RISK_ASSESSED"))
            self.assertEqual((audit.from_status, audit.to_status), ("PROCESSING", "VERIFIED"))

    async def test_result_views_report_every_check_and_never_show_not_run_as_pass(self):
        session_id = self.processing(level="DOCUMENT_FACE_LIVENESS", signed_barcode=False)
        await self.verify(session_id)
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200, result)
        self.assertEqual((result["final_result"], result["outcome"], result["end_user_message_code"]),
                         ("MANUAL_REVIEW", "REVIEW", "REVIEW"))
        self.assertEqual(result["end_user_message"], "Your verification has been submitted for additional review.")
        statuses = {item["check_name"]: item["status"] for item in result["check_results"]}
        self.assertEqual(statuses["DOCUMENT_QUALITY"], "PASS")
        self.assertEqual((statuses["FACE_MATCH"], statuses["LIVENESS"]), ("NOT_RUN", "NOT_RUN"))  # never PASS
        self.assertEqual((statuses["NFC"], statuses["NFC_ACTIVE_AUTHENTICATION"]), ("NOT_APPLICABLE", "NOT_APPLICABLE"))
        self.assertEqual(statuses["DOCUMENT_AUTHENTICITY"], "REVIEW")
        self.assertEqual(statuses["FORENSICS_FONT_INCONSISTENCY"], "NOT_SUPPORTED")
        self.assertTrue(set(statuses.values()) <= {"PASS", "FAIL", "REVIEW", "NOT_SUPPORTED", "NOT_APPLICABLE", "NOT_RUN", "ERROR"})

    async def test_a_crashed_assessment_is_a_technical_error_not_a_rejection(self):
        session_id = self.processing()
        with patch("kyc.services.risk.evaluate", side_effect=RuntimeError("engine crashed")):
            self.assertEqual(self.app.state.assessor.assess(self.org, session_id, uuid4()), "ERROR")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual((result["status"], result["final_result"], result["outcome"], result["retry_allowed"]),
                         ("PROCESSING", "TECHNICAL_ERROR", None, True))
        self.assertEqual(result["end_user_message_code"], "TECHNICAL")
        code, body, _ = await self.verify(session_id)  # the retry still decides on the evidence
        self.assertEqual((code, body["status"]), (200, "VERIFIED"))
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual((result["final_result"], result["outcome"]), ("VERIFIED", "PASS"))

    async def test_metrics_count_committed_decisions_without_identity_data(self):
        from kyc.core.metrics import REGISTRY
        before_review, before_tech = REGISTRY.value("kyc_review_total"), REGISTRY.value("kyc_technical_error_total", stage="risk")
        session_id = self.processing(signed_barcode=False)
        with patch("kyc.services.risk.evaluate", side_effect=RuntimeError("engine crashed")):
            self.app.state.assessor.assess(self.org, session_id, uuid4())
        self.assertEqual(REGISTRY.value("kyc_technical_error_total", stage="risk"), before_tech + 1)
        self.assertEqual(REGISTRY.value("kyc_review_total"), before_review)  # the rolled-back attempt is not counted
        await self.verify(session_id)
        self.assertEqual(REGISTRY.value("kyc_review_total"), before_review + 1)
        code, text, _ = await call(self.app, "/metrics")
        self.assertEqual(code, 200)
        text = text.decode()
        self.assertIn('kyc_reason_codes_total{decision="REVIEW",reason="DOCUMENT_AUTHENTICITY_UNVERIFIED"}', text)
        self.assertIn('kyc_http_request_seconds_bucket{route="/v1/kyc/{session_id}/verify",le="+Inf"}', text)
        self.assertNotIn(str(session_id), text)
        self.app.state.settings.metrics_token = SecretStr("m" * 32)
        try:
            self.assertEqual((await call(self.app, "/metrics"))[0], 401)
            code, _, _ = await call(self.app, "/metrics", headers={"Authorization": "Bearer " + "m" * 32})
            self.assertEqual(code, 200)
        finally:
            self.app.state.settings.metrics_token = None
        self.app.state.settings.environment = "production"
        try:
            self.assertEqual((await call(self.app, "/metrics"))[0], 404)  # off in production until a token is set
        finally:
            self.app.state.settings.environment = "test"

    async def test_verify_is_idempotent_and_never_reassesses(self):
        session_id = self.processing(signed_barcode=False)
        code, first, _ = await self.verify(session_id)
        self.assertEqual((first["status"], first["decision"]["reason_codes"]),
                         ("MANUAL_REVIEW", ["DOCUMENT_AUTHENTICITY_UNVERIFIED"]))
        code, again, _ = await self.verify(session_id)
        self.assertEqual((code, again), (200, first))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(RiskAssessmentRecord)), 1)
        self.assertEqual(self.app.state.assessor.assess(self.org, session_id, uuid4()), "NOT_READY")

    async def test_verify_refuses_early_expired_and_foreign_sessions(self):
        code, body, _ = await self.verify(self.ready(status="SELFIE_REQUIRED"))
        self.assertEqual((code, body["reason_code"]), (409, "VERIFICATION_NOT_READY"))
        expired = self.processing()
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, expired)
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.verify(expired)
        self.assertEqual((code, body["reason_code"]), (409, "SESSION_EXPIRED"))
        code, body, _ = await self.verify(self.processing(organization=self.other_org))
        self.assertEqual(code, 404)

    async def test_state_machine_guard_turns_an_unsupported_pass_into_review(self):
        session_id = self.processing(level="DOCUMENT_FACE")  # face level, but no face evidence at all
        forced = RiskOutcome("PASS", ["DOCUMENT_VALID"], DEFAULT_VERSION, [], VerificationEvidence(document_valid=True, policy_pass=True))
        with patch("kyc.services.risk.evaluate", return_value=forced):
            code, body, _ = await self.verify(session_id)
        self.assertEqual((body["status"], body["decision"]["reason_codes"]), ("MANUAL_REVIEW", ["VERIFICATION_EVIDENCE_GUARD"]))

    async def test_tamper_rejects_the_session(self):
        session_id = self.processing()
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(BarcodeResult)).signature_valid = False  # signature present but does not verify
        code, body, _ = await self.verify(session_id)
        self.assertEqual((body["status"], body["decision"]["result"]), ("REJECTED", "FAIL"))
        self.assertEqual(body["decision"]["reason_codes"], ["HIGH_RISK_TAMPER_SIGNAL", "FRAUD_BARCODE_SIGNATURE_INVALID"])
