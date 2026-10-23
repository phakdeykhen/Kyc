"""Evaluate a private, frozen 1:N protocol and identity-disjoint held-out dataset.

No acceptance rates/thresholds are supplied by this script. No release approval is
created. Report contains aggregates and hashes, never embeddings or subject names.
"""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kyc.face_identity.evaluation import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate(json.loads(args.protocol.read_text()), json.loads(args.dataset.read_text()))
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("Evaluation inputs are invalid or incomplete. See the private protocol; no biometric values were logged.") from None
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"ready_for_governance_review": report["ready"], "synthetic": report["synthetic"],
                      "gallery_population": report["max_population_tested"]}))


if __name__ == "__main__":
    main()
