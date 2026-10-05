#!/usr/bin/env python3
"""Explicit provisioning only; the running application never fetches models.

Download the full precision OpenCV Zoo YuNet 2023mar and SFace 2021dec
weights with their published license notices. URLs, sizes, and SHA-256 digests
are pinned to a repository commit. No generic model URL is accepted.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

OPENCV_ZOO_COMMIT = "47534e27c9851bb1128ccc0102f1145e27f23f98"
MEDIA_BASE = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{OPENCV_ZOO_COMMIT}/models"
RAW_BASE = f"https://raw.githubusercontent.com/opencv/opencv_zoo/{OPENCV_ZOO_COMMIT}/models"
MODEL_BUNDLE = (
    {
        "name": "OpenCV YuNet", "version": "2023mar",
        "filename": "face_detection_yunet_2023mar.onnx", "size": 232589,
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "url": f"{MEDIA_BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "license": "MIT", "license_filename": "LICENSE-YuNet.txt",
        "license_sha256": "c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958",
        "license_size": 1085,
        "license_url": f"{RAW_BASE}/face_detection_yunet/LICENSE",
        "attribution": "Copyright (c) 2020 Shiqi Yu",
    },
    {
        "name": "OpenCV SFace", "version": "2021dec",
        "filename": "face_recognition_sface_2021dec.onnx", "size": 38696353,
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "url": f"{MEDIA_BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "license": "Apache-2.0", "license_filename": "LICENSE-SFace.txt",
        "license_sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        "license_size": 11358,
        "license_url": f"{RAW_BASE}/face_recognition_sface/LICENSE",
        "attribution": "Copyright (C) 2021, Shenzhen Institute of Artificial Intelligence and Robotics for Society",
    },
)


def verified_file(path: Path, sha256: str, size: int) -> bool:
    try:
        if path.stat().st_size != size:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == sha256
    except OSError:
        return False


def fetch_verified(url: str, destination: Path, sha256: str, size: int) -> None:
    if verified_file(destination, sha256, size):
        return
    request = Request(url, headers={"User-Agent": "universal-kyc-face-model-provisioner/1"})
    temporary_path = None
    try:
        digest, total = hashlib.sha256(), 0
        with urlopen(request, timeout=60) as response, tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".face-model-", delete=False) as stream:
            temporary_path = Path(stream.name)
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    raise ValueError(f"Unexpected download size for {destination.name}")
                stream.write(chunk)
                digest.update(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if total != size or digest.hexdigest() != sha256:
            raise ValueError(f"Model bundle integrity failure for {destination.name}")
        temporary_path.chmod(0o644)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def provision(destination: Path, *, verify_only: bool = False) -> dict:
    if not verify_only:
        destination.mkdir(parents=True, exist_ok=True)
    for model in MODEL_BUNDLE:
        artifacts = (
            (model["filename"], model["url"], model["sha256"], model["size"]),
            (model["license_filename"], model["license_url"], model["license_sha256"], model["license_size"]),
        )
        for filename, url, sha256, size in artifacts:
            path = destination / filename
            if verify_only:
                if not verified_file(path, sha256, size):
                    raise ValueError(f"Missing or corrupt pinned model artifact: {filename}")
            else:
                fetch_verified(url, path, sha256, size)
    manifest = {
        "format": "kyc.opencv-face-models.v1", "repository_commit": OPENCV_ZOO_COMMIT,
        "models": list(MODEL_BUNDLE), "backend": "OpenCV DNN CPU",
        "embedding_dimension": 128,
        "threshold_note": "OpenCV benchmark thresholds are not deployment calibration.",
    }
    if not verify_only:
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", "--output-dir", type=Path, default=Path("models/face"))
    parser.add_argument("--verify-only", action="store_true", help="Check the installed bundle without network access")
    arguments = parser.parse_args()
    manifest = provision(arguments.destination, verify_only=arguments.verify_only)
    print(json.dumps({"destination": str(arguments.destination.resolve()),
                      "verified": True, "repository_commit": manifest["repository_commit"],
                      "models": [model["filename"] for model in MODEL_BUNDLE]}, indent=2))


if __name__ == "__main__":
    main()
