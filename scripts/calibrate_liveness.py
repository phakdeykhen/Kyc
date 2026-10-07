#!/usr/bin/env python3
"""Validate the liveness policy against labelled real sessions and write a validation report.

Input is a CSV with a header, one row per real liveness attempt run with the production
policy (for example during a supervised pilot):

    label,result
    GENUINE,PASS
    PRINTED_PHOTO,REVIEW
    PHOTO_ON_SCREEN,FAIL
    VIDEO_REPLAY,REVIEW

label: GENUINE or an attack type (PRINTED_PHOTO, PHOTO_ON_SCREEN, VIDEO_REPLAY, MASK_3D, ...).
result: the liveness check result the API recorded (PASS, REVIEW or FAIL).

Record results with the policy still uncalibrated and score what it would decide when
calibrated: run the attempts against a staging deployment with LIVENESS_CALIBRATED=true, which
is never production. The report is valid only for the policy version it names.

Set LIVENESS_VALIDATION_FILE to the output. The API refuses a report for another policy
version, one below the minimum sizes, or one that misses its targets.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kyc.biometrics.calibration import validate_liveness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", type=Path)
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--target-attack-detection", type=float, default=0.95)
    parser.add_argument("--target-genuine-pass", type=float, default=0.90)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.sessions.read_bytes()
    rows = [{"label": row["label"].strip().upper(), "result": row["result"].strip().upper()}
            for row in csv.DictReader(raw.decode("utf-8").splitlines())]
    if any(row["result"] not in ("PASS", "REVIEW", "FAIL") for row in rows):
        raise SystemExit("result must be PASS, REVIEW or FAIL")
    report = validate_liveness(rows, policy_version=args.policy_version, dataset_sha256=hashlib.sha256(raw).hexdigest(),
                               target_attack_detection=args.target_attack_detection,
                               target_genuine_pass=args.target_genuine_pass)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "validated": report["validated"], "problems": report["problems"],
                      "measured": report["measured"]}, indent=2))
    return 0 if report["validated"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
