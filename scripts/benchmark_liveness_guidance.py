"""Profile guidance CPU and upload size with the vendored public face fixture.

This measures local image processing only: no HTTP, database, real camera, identity
decision or device/demographic validation. It executes trusted geometry code from
the selected local Git revision to measure the previous two-image guide path.

  .venv/bin/python scripts/benchmark_liveness_guidance.py --repeat 100 \
      --output artifacts/liveness-guidance-server-profile.json
"""

import argparse
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from kyc.biometrics.opencv import OpenCVFaceEngine
from kyc.engines.capture_quality import decode_capture
from kyc.liveness.active import ActiveLivenessPolicy, guide, movement_visibility
from kyc.liveness.geometry import median_pose, pose
from kyc.services.liveness import LivenessLimits, _calibration_context, _open_calibration, _seal_calibration, _single_face
from kyc.storage.biometrics import BiometricCipher
from tests.helpers import multipart


def measure(function, repeat):
    for _ in range(3):
        function()
    elapsed = []
    for _ in range(repeat):
        started = time.perf_counter_ns()
        function()
        elapsed.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(elapsed)
    return {"samples": repeat, "p50_ms": round(statistics.median(elapsed), 4),
            "p95_ms": round(ordered[max(0, int(repeat * .95) - 1)], 4),
            "mean_ms": round(statistics.fmean(elapsed), 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=100)
    parser.add_argument("--baseline-revision", default="HEAD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 20:
        parser.error("Use at least 20 repetitions for percentiles.")

    revision = subprocess.run(["git", "rev-parse", args.baseline_revision], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    source = subprocess.run(["git", "show", f"{revision}:src/kyc/liveness/geometry.py"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout
    baseline_geometry = types.ModuleType("_liveness_baseline_geometry")
    sys.modules[baseline_geometry.__name__] = baseline_geometry
    exec(compile(source, f"{revision}:geometry.py", "exec"), baseline_geometry.__dict__)

    fixture = ROOT / "realtime-face-liveness-detector/misc/face.jpg"
    image = Image.open(fixture).convert("RGB")
    image = image.resize((640, round(image.height * 640 / image.width)), Image.Resampling.LANCZOS)
    encoded = BytesIO()
    image.save(encoded, "JPEG", quality=85)
    jpeg = encoded.getvalue()
    limits = LivenessLimits(challenge_ttl_seconds=120, max_attempts=5,
                            max_frame_bytes=5 * 1024 * 1024, max_frame_pixels=12_000_000)
    models = ROOT / "var/models"
    engine = OpenCVFaceEngine(models / "face_detection_yunet_2023mar.onnx",
                              models / "face_recognition_sface_2021dec.onnx")
    decoded = Image.fromarray(decode_capture(jpeg, max_bytes=limits.max_frame_bytes,
                                             max_pixels=limits.max_frame_pixels).pixels)
    faces = engine.detect(decoded)  # native load and validation are outside warm timings
    if len(faces) != 1 or not movement_visibility(decoded, faces[0]).accepted:
        raise RuntimeError("The public fixture must provide one usable visible face.")
    face = faces[0]
    policy = ActiveLivenessPolicy()
    cipher = BiometricCipher("bench.v1:" + base64.b64encode(os.urandom(32)).decode("ascii"))
    context = _calibration_context(types.SimpleNamespace(organization_id="bench-org", id="bench-session"),
        types.SimpleNamespace(id="bench-challenge", policy_version=policy.version), policy)
    token = _seal_calibration(pose(face.landmarks), cipher, context)

    def original_single():
        capture = decode_capture(jpeg, max_bytes=limits.max_frame_bytes, max_pixels=limits.max_frame_pixels)
        detected = engine.detect(Image.fromarray(capture.pixels))
        if len(detected) != 1:
            raise RuntimeError("Fixture detection was lost")
        return baseline_geometry.pose(detected[0].landmarks)

    def before_guide():
        current = original_single()
        neutral = original_single()
        return guide(neutral, current, "LOOK_DOWN", policy)

    def after_guide():
        samples = []
        for _ in range(3):
            status, sample, _ = _single_face(engine, jpeg, limits)
            if status != "OK":
                raise RuntimeError("Fixture visibility was lost")
            samples.append(sample)
        baseline = median_pose(samples[1:])
        _seal_calibration(baseline, cipher, context)
        return guide(baseline, samples[0], "LOOK_DOWN", policy)

    def warm_guide():
        baseline = _open_calibration(token, cipher, context, limits)
        status, current, _ = _single_face(engine, jpeg, limits)
        if status != "OK":
            raise RuntimeError("Fixture visibility was lost")
        return guide(baseline, current, "LOOK_DOWN", policy)

    metrics = {
        "decode_rgb": measure(lambda: decode_capture(jpeg, max_bytes=limits.max_frame_bytes,
                                                     max_pixels=limits.max_frame_pixels), args.repeat),
        "native_yunet_detection": measure(lambda: engine.detect(decoded), args.repeat),
        "original_pose": measure(lambda: baseline_geometry.pose(face.landmarks), args.repeat * 10),
        "updated_pose": measure(lambda: pose(face.landmarks), args.repeat * 10),
        "movement_visibility": measure(lambda: movement_visibility(decoded, face), args.repeat),
        "before_guide_2_images": measure(before_guide, args.repeat),
        "after_setup_3_images": measure(after_guide, args.repeat),
        "after_warm_token_1_image": measure(warm_guide, args.repeat),
    }
    fields = {"challenge_id": "00000000-0000-0000-0000-000000000000", "nonce": "n" * 43, "step": "1"}
    files = [("frame", "frame.jpg", "image/jpeg", jpeg), ("baseline", "baseline.jpg", "image/jpeg", jpeg)]
    before_body, _ = multipart(fields, files)
    after_body, _ = multipart({**fields, "holding": "false"}, files + [
        ("baseline_extra", "baseline-1.jpg", "image/jpeg", jpeg)])
    warm_body, _ = multipart({**fields, "holding": "false", "calibration": token}, files[:1])
    import cv2
    cpu_model = platform.processor()
    if sys.platform == "darwin":
        cpu_query = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
        cpu_model = cpu_query.stdout.strip() if cpu_query.returncode == 0 else cpu_model
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(), "baseline_revision": revision,
        "command": f".venv/bin/python scripts/benchmark_liveness_guidance.py --repeat {args.repeat} "
                   f"--baseline-revision {revision} --output artifacts/liveness-guidance-server-profile.json",
        "scope": "Warm local CPU processing; excludes HTTP, DB, camera rendering and network latency.",
        "limitations": ["One vendored public static face; not a live movement or presentation-attack test.",
                        "Desktop host; does not establish mobile, lighting, skin-tone or glasses performance.",
                        "The static fixture is never submitted for verification; no identity approval is attempted."],
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "cpu_model": cpu_model,
                        "python": platform.python_version(), "logical_cpus": os.cpu_count(),
                        "opencv": cv2.__version__, "opencv_threads": cv2.getNumThreads()},
        "input": {"path": str(fixture.relative_to(ROOT)), "resolution": image.size,
                  "jpeg_quality": 85, "frame_bytes": len(jpeg), "faces_detected": len(faces),
                  "native_detector": engine.detector_name, "detector_version": engine.detector_version,
                  "detection_max_dimension": engine.detection_max_dimension},
        "limits": {"max_frame_bytes": limits.max_frame_bytes, "max_frame_pixels": limits.max_frame_pixels,
                   "challenge_ttl_seconds": limits.challenge_ttl_seconds},
        "measurements": metrics,
        "multipart_upload": {"before_images": 2, "setup_images": 3, "warm_images": 1,
                             "before_bytes": len(before_body), "setup_bytes": len(after_body),
                             "warm_token_bytes": len(warm_body), "opaque_token_characters": len(token),
                             "setup_increase_percent": round((len(after_body) / len(before_body) - 1) * 100, 2),
                             "warm_decrease_percent": round((1 - len(warm_body) / len(before_body)) * 100, 2)},
    }
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
