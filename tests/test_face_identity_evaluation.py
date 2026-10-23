"""Synthetic evaluation/approval contract tests, never a release accuracy report."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from kyc.face_identity.evaluation import evaluate, measurement, METRICS
from kyc.face_identity.policy import canonical, load_policy, RELEASE_EVIDENCE, sha
from tests.test_face_identity import test_policy
from tests.test_liveness import unit


class EvaluationTests(unittest.TestCase):
    def fixtures(self):
        policy = test_policy().model_copy(update={"max_population": 1, "populations": ["STAFF"]}).model_dump()
        protocol = {"policy": policy, "locked_at": "2026-01-01T00:00:00+00:00", "minimum_independent_trials": 1,
            "maximum_error_rates": {name: .999 for name in METRICS}, "maximum_end_to_end_p95_ms": 1000,
            "supported_conditions": {name: ["SYNTHETIC"] for name in ("camera", "lighting", "appearance", "user_group")}}
        model = test_policy().model_id
        known = {"subject": "evaluation-a", "embedding": unit(0).vector.tolist(), "model_id": model, "population": "STAFF"}
        other = {"subject": "evaluation-b", "embedding": unit(1).vector.tolist(), "model_id": model, "population": "STAFF"}
        extra = {name: "SYNTHETIC" for name in ("camera", "lighting", "appearance", "user_group")}
        data = {"synthetic": True, "protocol_sha256": sha(protocol), "evaluation_started_at": "2026-02-01T00:00:00+00:00",
            "development_subjects": ["development"], "evaluation_subjects": ["evaluation-a", "evaluation-b"],
            "gallery": [known], "probes": [known | extra | {"end_to_end_ms": 100}, other | extra | {"end_to_end_ms": 200}],
            "pad_trials": [{"subject": "evaluation-a", "attack": False, "accepted": True},
                           {"subject": "evaluation-b", "attack": True, "accepted": False}],
            "verification_trials": [{"reference": known, "probe": known}, {"reference": known, "probe": other}]}
        return protocol, data

    def test_synthetic_success_never_becomes_production_accuracy(self):
        protocol, data = self.fixtures()
        report = evaluate(protocol, data)
        self.assertFalse(report["ready"])
        self.assertEqual(report["metrics"]["fpir"]["rate"], 0)
        self.assertEqual(report["metrics"]["fnir"]["rate"], 0)
        self.assertNotIn('"embedding":', json.dumps(report))
        self.assertNotIn("evaluation-a", json.dumps(report))

    def test_zero_errors_with_tiny_sample_size_is_not_validation(self):
        metric = measurement(0, 2, .001, 100)
        self.assertFalse(metric["passed"])
        self.assertGreater(metric["upper_95"], .5)

    def test_identity_overlap_correlated_frames_and_changed_protocol_are_rejected(self):
        for problem in ("overlap", "correlation", "changed_protocol"):
            protocol, data = self.fixtures()
            if problem == "overlap": data["development_subjects"].append("evaluation-a")
            if problem == "correlation": data["probes"].append(data["probes"][0])
            if problem == "changed_protocol": protocol["maximum_end_to_end_p95_ms"] = 10
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                evaluate(protocol, data)

    def test_missing_pad_measurements_and_population_coverage_remain_blocked(self):
        protocol, data = self.fixtures()
        data["synthetic"] = False
        data["pad_trials"] = []
        report = evaluate(protocol, data)
        self.assertFalse(report["ready"])
        self.assertFalse(report["metrics"]["pad_attack_acceptance"]["passed"])
        protocol, data = self.fixtures()
        protocol["policy"]["populations"] = ["STAFF", "CUSTOMER"]
        data["protocol_sha256"] = sha(protocol)
        # This person is enrolled in CUSTOMER, but is a non-mate for a STAFF scan.
        data["gallery"].append({**data["probes"][1], "population": "CUSTOMER"})
        report = evaluate(protocol, data)
        self.assertEqual(report["population_results"]["STAFF"]["metrics"]["fpir"]["trials"], 1)
        self.assertEqual(report["population_results"]["STAFF"]["metrics"]["fpir"]["rate"], 0)
        self.assertFalse(report["population_results"]["CUSTOMER"]["metrics"]["fnir"]["passed"])
        self.assertFalse(report["ready"])

    def test_signed_approval_is_bound_to_policy_report_and_expiry(self):
        private = Ed25519PrivateKey.generate()
        protocol, data = self.fixtures()
        report = evaluate(protocol, data)
        # Test a signature envelope with deliberately fake measurements, using an
        # ephemeral key that is never an installed deployment approval key.
        report.update(ready=True, synthetic=False)
        payload = {"policy": protocol["policy"], "evaluation": report, "approval": {
            "scope": "face-identification", "approved_by": "SYNTHETIC-TEST-SIGNER",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "evaluation_sha256": sha(report), **{name: "a" * 64 for name in RELEASE_EVIDENCE}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "public.pem"
            key.write_bytes(private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            path = root / "approval.json"
            bundle = {"payload": payload, "signature": base64.b64encode(private.sign(canonical(payload))).decode()}
            path.write_text(json.dumps(bundle))
            self.assertEqual(load_policy(path, key)[0].version, protocol["policy"]["version"])
            payload["approval"]["device_validation"] = True
            bundle["signature"] = base64.b64encode(private.sign(canonical(payload))).decode()
            path.write_text(json.dumps(bundle))
            with self.assertRaises(ValueError): load_policy(path, key)
            payload["approval"]["device_validation"] = "a" * 64
            bundle["signature"] = base64.b64encode(private.sign(canonical(payload))).decode()
            payload["policy"]["identification_threshold"] = .1
            path.write_text(json.dumps(bundle))
            with self.assertRaises(Exception): load_policy(path, key)
            bundle["signature"] = base64.b64encode(private.sign(canonical(payload))).decode()
            path.write_text(json.dumps(bundle))
            with self.assertRaises(ValueError): load_policy(path, key)
