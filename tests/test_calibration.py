"""Measured face calibration and liveness validation: thresholds come from data, never from a flag."""

from pathlib import Path
import unittest

from pydantic import ValidationError

from kyc.biometrics.calibration import (CalibrationError, MIN_IMPOSTOR_PAIRS, calibrate_face, load_face_calibration,
                                        load_liveness_validation, roc_auc, validate_liveness)
from kyc.core.config import Settings
from tests import calibration_fixtures as fixtures


def settings(**values) -> Settings:
    return Settings(_env_file=None, environment="test", database_url="sqlite://", **values)


class FaceCalibrationTests(unittest.TestCase):
    def test_thresholds_meet_the_measured_targets(self):
        genuine, impostor = fixtures.scores()
        report = fixtures.face_report()
        match, no_match = report["thresholds"]["match"], report["thresholds"]["no_match"]
        self.assertLess(no_match, match)
        self.assertLessEqual(sum(s >= match for s in impostor) / len(impostor), 0.001)
        self.assertLessEqual(sum(s < no_match for s in genuine) / len(genuine), 0.01)
        measured = report["measured"]
        self.assertEqual((measured["genuine_pairs"], measured["impostor_pairs"]), (300, 3000))
        self.assertAlmostEqual(measured["tar_at_match"], 1 - measured["frr_at_match"])
        self.assertEqual(sum(b["count"] for b in measured["impostor_distribution"]), 3000)

    def test_auc_matches_its_definition(self):
        self.assertEqual(roc_auc([0.9, 0.8], [0.1, 0.2]), 1.0)
        self.assertEqual(roc_auc([0.1], [0.9]), 0.0)
        self.assertEqual(roc_auc([0.5], [0.5]), 0.5)

    def test_too_little_data_or_a_loose_target_is_refused(self):
        genuine, impostor = fixtures.scores()
        common = dict(target_frr_at_no_match=0.01, model=fixtures.MODEL, version="V", dataset_sha256="0")
        with self.assertRaises(CalibrationError):
            calibrate_face(genuine, impostor[:MIN_IMPOSTOR_PAIRS - 1], target_far=0.001, **common)
        with self.assertRaises(CalibrationError):
            calibrate_face(genuine, impostor, target_far=0.05, **common)

    def test_the_loader_refuses_reports_that_do_not_hold(self):
        cases = {
            "other model": fixtures.face_report(model={**fixtures.MODEL, "version": "other"}),
            "missed target": fixtures.face_report(measured={**fixtures.face_report()["measured"], "far_at_match": 0.5}),
            "uncalibrated name": fixtures.face_report(version="SFACE-UNCALIBRATED"),
            "too small": fixtures.face_report(measured={**fixtures.face_report()["measured"], "genuine_pairs": 10}),
            "bad thresholds": fixtures.face_report(thresholds={"match": 0.1, "no_match": 0.4}),
            "wrong kind": fixtures.liveness_report(),
        }
        for name, report in cases.items():
            with self.subTest(name), self.assertRaises(CalibrationError):
                load_face_calibration(fixtures.write(f"bad-{name}.json", report), fixtures.MODEL)

    def test_settings_take_thresholds_and_version_from_the_report(self):
        report = fixtures.face_report()
        configured = settings(face_match_calibration_file=fixtures.write("face-ok.json", report),
                              face_match_pass_threshold=0.9, face_match_fail_threshold=0.8)
        self.assertTrue(configured.face_match_calibrated)
        self.assertEqual(configured.face_match_policy_version, "SFACE-TEST-CALIBRATED-1")
        self.assertEqual(configured.face_match_pass_threshold, report["thresholds"]["match"])
        self.assertTrue(configured.face_match_calibration_reference.startswith("face-ok.json#sha256:"))
        with self.assertRaises(ValidationError) as raised:
            settings(face_match_calibration_file=Path("/nonexistent/face.json"))
        self.assertIn("Calibration report refused", str(raised.exception))


class LivenessValidationTests(unittest.TestCase):
    def test_every_required_attack_type_must_be_tested_and_detected(self):
        self.assertTrue(fixtures.liveness_report()["validated"])
        rows = [{"label": "GENUINE", "result": "PASS"}] * 60 + [{"label": "PRINTED_PHOTO", "result": "FAIL"}] * 25
        report = validate_liveness(rows, policy_version="V", dataset_sha256="0")
        self.assertFalse(report["validated"])
        self.assertEqual(report["measured"]["untested_attack_types"], ["PHOTO_ON_SCREEN", "VIDEO_REPLAY"])
        leaky = [{"label": "GENUINE", "result": "PASS"}] * 60
        for attack in ("PRINTED_PHOTO", "PHOTO_ON_SCREEN", "VIDEO_REPLAY"):
            leaky += [{"label": attack, "result": "PASS"}] * 5 + [{"label": attack, "result": "REVIEW"}] * 20
        self.assertFalse(validate_liveness(leaky, policy_version="V", dataset_sha256="0")["validated"])

    def test_a_report_only_applies_to_its_own_policy_version(self):
        path = fixtures.write("liveness-other.json", fixtures.liveness_report("ACTIVE-GEOMETRY-OTHER"))
        with self.assertRaises(CalibrationError):
            load_liveness_validation(path, fixtures.LIVENESS_VERSION)
        self.assertTrue(settings(liveness_validation_file=fixtures.liveness_file()).liveness_calibrated)
        with self.assertRaises(ValidationError):
            settings(liveness_validation_file=path)


if __name__ == "__main__":
    unittest.main()
