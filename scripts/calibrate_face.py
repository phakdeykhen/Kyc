#!/usr/bin/env python3
"""Measure face-match thresholds from labelled pairs and write a calibration report.

Input is a CSV with a header. Either image pairs:

    reference,live,label          (label: genuine | impostor)
    ids/a_front_portrait.jpg,selfies/a.jpg,genuine
    ids/a_front_portrait.jpg,selfies/b.jpg,impostor

or precomputed production scores (--scores):

    score,label
    0.512,genuine

Image pairs are scored exactly as production does: the same YuNet detection and quality gate
(DOCUMENT_PORTRAIT for the reference, LIVE_SELFIE for the live face), the same SFace model and
cosine similarity. Pairs production would refuse for quality are counted and left out, because
production would ask for a recapture instead of comparing them.

Set FACE_MATCH_CALIBRATION_FILE to the output. The API refuses reports that are too small,
were measured with another model, or miss their false-acceptance target.

Example:
    python scripts/calibrate_face.py pairs.csv --version SFACE-KH-2026.11.1 --target-far 0.001 \
        --condition population="Cambodian adults, 18-75" --condition devices="Android+iPhone" -o face-calibration.json
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kyc.biometrics.calibration import CalibrationError, calibrate_face  # noqa: E402
from kyc.biometrics.types import SFACE_NAME, SFACE_SHA256, SFACE_VERSION  # noqa: E402

MODEL = {"name": SFACE_NAME, "version": SFACE_VERSION, "sha256": SFACE_SHA256}


def label_of(value: str) -> str:
    label = value.strip().lower()
    if label not in ("genuine", "impostor"):
        raise SystemExit(f"label must be genuine or impostor, not {value!r}")
    return label


def from_scores(rows) -> tuple[list[float], list[float], list[str], dict]:
    genuine, impostor, digest = [], [], []
    for row in rows:
        label, score = label_of(row["label"]), float(row["score"])
        (genuine if label == "genuine" else impostor).append(score)
        digest.append(f"{score!r}:{label}")
    return genuine, impostor, digest, {"source": "PRECOMPUTED_SCORES"}


def from_images(rows, base: Path, models: Path) -> tuple[list[float], list[float], list[str], dict]:
    from PIL import Image, ImageOps
    from kyc.biometrics import OpenCVFaceEngine, compare_embeddings
    from kyc.biometrics.types import FaceMatchPolicy

    engine = OpenCVFaceEngine(models / "face_detection_yunet_2023mar.onnx", models / "face_recognition_sface_2021dec.onnx")
    if engine.unavailable_reason():
        raise SystemExit(f"face models unavailable: {engine.unavailable_reason()}")
    cache: dict[tuple[str, str], tuple[object, str] | None] = {}

    def template(path: str, source: str):
        if (path, source) not in cache:
            data = (base / path).read_bytes()
            image = ImageOps.exif_transpose(Image.open(base / path)).convert("RGB")
            quality = engine.assess(image, source=source)
            ok = quality.accepted and quality.face_count == 1 and quality.detection is not None
            cache[(path, source)] = (engine.embed(image, quality.detection), hashlib.sha256(data).hexdigest()) if ok else None
        return cache[(path, source)]

    policy = FaceMatchPolicy()
    genuine, impostor, digest, refused = [], [], [], 0
    for row in rows:
        label = label_of(row["label"])
        reference, live = template(row["reference"], "DOCUMENT_PORTRAIT"), template(row["live"], "LIVE_SELFIE")
        if reference is None or live is None:
            refused += 1
            continue
        score = compare_embeddings(reference[0], live[0], policy).score
        (genuine if label == "genuine" else impostor).append(score)
        digest.append(f"{reference[1]}:{live[1]}:{label}")
    return genuine, impostor, digest, {"source": "IMAGE_PAIRS", "pairs_refused_by_quality_gate": refused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pairs", type=Path)
    parser.add_argument("--scores", action="store_true", help="the CSV holds score,label instead of image paths")
    parser.add_argument("--version", required=True, help="new face policy version, e.g. SFACE-KH-2026.11.1")
    parser.add_argument("--target-far", type=float, default=0.001)
    parser.add_argument("--target-frr-at-no-match", type=float, default=0.01,
                        help="share of genuine pairs allowed below the NO_MATCH threshold")
    parser.add_argument("--models", type=Path, default=ROOT / "models/face")
    parser.add_argument("--condition", action="append", default=[], metavar="KEY=VALUE",
                        help="test conditions recorded in the report (population, devices, lighting...)")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    with args.pairs.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    genuine, impostor, digest, source = (from_scores(rows) if args.scores
                                         else from_images(rows, args.pairs.parent, args.models))
    conditions = dict(item.split("=", 1) for item in args.condition) | source
    try:
        report = calibrate_face(genuine, impostor, target_far=args.target_far,
                                target_frr_at_no_match=args.target_frr_at_no_match, model=MODEL, version=args.version,
                                dataset_sha256=hashlib.sha256("\n".join(sorted(digest)).encode()).hexdigest(),
                                conditions=conditions)
    except CalibrationError as error:
        print(f"Calibration refused: {error}", file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    measured = report["measured"]
    print(json.dumps({"output": str(args.output), "thresholds": report["thresholds"],
                      "far_at_match": measured["far_at_match"], "frr_at_match": measured["frr_at_match"],
                      "roc_auc": measured["roc_auc"], "genuine_pairs": measured["genuine_pairs"],
                      "impostor_pairs": measured["impostor_pairs"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
