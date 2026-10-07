"""Synthetic calibration reports for configuration tests. Not measurements of anything real."""

import json
from pathlib import Path
import random
import tempfile

from kyc.biometrics.calibration import calibrate_face, validate_liveness
from kyc.biometrics.types import SFACE_NAME, SFACE_SHA256, SFACE_VERSION

MODEL = {"name": SFACE_NAME, "version": SFACE_VERSION, "sha256": SFACE_SHA256}
LIVENESS_VERSION = "ACTIVE-GEOMETRY-2026.10.2"
_DIRECTORY = Path(tempfile.mkdtemp(prefix="kyc-calibration-"))


def scores(seed: int = 7, genuine: int = 300, impostor: int = 3000) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    return ([min(1, max(-1, rng.gauss(0.62, 0.1))) for _ in range(genuine)],
            [min(1, max(-1, rng.gauss(0.05, 0.08))) for _ in range(impostor)])


def face_report(**changes) -> dict:
    genuine, impostor = scores()
    report = calibrate_face(genuine, impostor, target_far=0.001, target_frr_at_no_match=0.01, model=MODEL,
                            version="SFACE-TEST-CALIBRATED-1", dataset_sha256="0" * 64)
    return report | changes


def liveness_report(version: str = LIVENESS_VERSION) -> dict:
    rows = [{"label": "GENUINE", "result": "PASS"}] * 60
    for attack in ("PRINTED_PHOTO", "PHOTO_ON_SCREEN", "VIDEO_REPLAY"):
        rows += [{"label": attack, "result": "FAIL"}] * 25
    return validate_liveness(rows, policy_version=version, dataset_sha256="0" * 64)


def write(name: str, report: dict) -> Path:
    path = _DIRECTORY / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def face_file() -> Path:
    return write("face.json", face_report())


def liveness_file() -> Path:
    return write("liveness.json", liveness_report())
