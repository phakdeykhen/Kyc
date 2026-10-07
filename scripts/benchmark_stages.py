"""Per-stage cost of the verification pipeline (Phase 18). Synthetic SPECIMEN inputs only.

  python scripts/benchmark_stages.py cpu [--repeat 20]
      In-process CPU time of each engine on this machine: capture decode + quality gate,
      Tesseract OCR of a passport data page, MRZ parsing, barcode decoding, face detection
      and embedding (when the pinned models are installed), AES-GCM sealing, encrypted
      capture storage, webhook signing.

  python scripts/benchmark_stages.py pipeline --base-url URL --organization ORG [--sessions 6] [--parallel 1,3]
      End-to-end document processing through the live API: upload a rendered SPECIMEN
      Cambodian passport, then time how long the session stays in DOCUMENT_PROCESSING
      (OCR, MRZ, classification, fraud analysis and the risk decision), with N uploads
      in flight at once. The API key comes from KYC_LOAD_API_KEY.

Writes JSON with --output (runs are appended).
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def timed(function, repeat: int) -> dict:
    function()  # warm caches, model loading and lazy imports
    values = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        values.append((time.perf_counter() - started) * 1000)
    ordered = sorted(values)
    return {"runs": repeat, "mean_ms": round(statistics.fmean(values), 2), "p50_ms": round(statistics.median(values), 2),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(.95 * len(ordered)))], 2), "max_ms": round(ordered[-1], 2)}


def cpu(args) -> dict:
    from io import BytesIO

    from PIL import Image

    from kyc.barcode import engine as barcode
    from kyc.core.crypto import FieldCipher
    from kyc.engines.capture_quality import HeuristicDocumentQualityEngine, decode_capture
    from kyc.mrz.parser import read as read_mrz
    from kyc.ocr.tesseract import TesseractOCREngine
    from kyc.storage.biometrics import BiometricCipher
    from kyc.storage.captures import LocalEncryptedCaptureStore
    from kyc.webhooks.signing import sign
    from tests import images
    from tests.mrz_build import td3

    def ring(version):
        return f"{version}:{base64.b64encode(os.urandom(32)).decode()}"

    stages = {}
    document = images.encode(images.good())
    passport = images.encode(images.photographed(images.kh_passport())) if images.fonts_available() else None
    gate = HeuristicDocumentQualityEngine()
    stages["capture_decode_and_quality_gate"] = timed(
        lambda: gate.assess(decode_capture(document, max_bytes=25 << 20, max_pixels=40_000_000), 1.586), args.repeat)
    stages["capture_decode_and_quality_gate"]["input"] = f"synthetic 1600x1200 JPEG, {len(document) // 1024} KiB"
    ocr = TesseractOCREngine()
    if passport is not None and ocr.is_available(("khm", "eng")):
        page = images.kh_passport()
        stages["ocr_full_page_khm_eng"] = timed(lambda: ocr.read_lines(page, ("khm", "eng")), max(3, args.repeat // 4))
        stages["ocr_full_page_eng"] = timed(lambda: ocr.read_lines(page, ("eng",)), max(3, args.repeat // 4))
        stages["ocr_full_page_khm_eng"]["input"] = f"rendered SPECIMEN passport page {page.width}x{page.height}"
    else:
        stages["ocr_full_page_khm_eng"] = {"skipped": "Tesseract khm/eng or fonts not installed"}
    lines = td3()
    stages["mrz_parse_td3"] = timed(lambda: read_mrz(list(lines)), args.repeat * 50)
    qr = images.qr_image("SPECIMEN|KH|N01234567|SOK SOPHEA", 600)
    import numpy as np
    qr_pixels = np.asarray(qr.convert("RGB") if hasattr(qr, "convert") else Image.open(BytesIO(qr)).convert("RGB"))
    if barcode.available():
        stages["barcode_decode_qr"] = timed(lambda: barcode.decode(qr_pixels), args.repeat)
    models = ROOT / "var" / "models"
    fixture = Path(os.environ.get("KYC_FACE_NATIVE_FIXTURE", "/private/tmp/kyc-opencv-face-smoke.jpg"))
    if (models / "face_recognition_sface_2021dec.onnx").exists() and fixture.exists():
        from kyc.biometrics.opencv import OpenCVFaceEngine
        face_engine = OpenCVFaceEngine(models / "face_detection_yunet_2023mar.onnx", models / "face_recognition_sface_2021dec.onnx")
        face = Image.open(fixture).convert("RGB")
        stages["face_detect"] = timed(lambda: face_engine.detect(face), args.repeat)
        detection = face_engine.detect(face)[0]
        stages["face_embed"] = timed(lambda: face_engine.embed(face, detection), args.repeat)
    else:
        stages["face_detect"] = {"skipped": "pinned face models or the public sample face are not installed"}
    cipher = FieldCipher(ring("bench"), os.urandom(32))
    stages["pii_field_seal_and_open"] = timed(
        lambda: cipher.open(*cipher.seal("SOK SOPHEA", "field/a/b/c/full_name/normalized"), "field/a/b/c/full_name/normalized"),
        args.repeat * 100)
    template = BiometricCipher(ring("bench-bio"))
    context = "biometric/a/b/c/LIVE_SELFIE/sface/2021dec/" + "0" * 64
    stages["template_seal_and_open"] = timed(lambda: template.open(*template.seal(b"\x01" * 512, context), context),
                                             args.repeat * 100)
    with tempfile.TemporaryDirectory() as directory:
        store = LocalEncryptedCaptureStore(Path(directory), "bench", {"bench": os.urandom(32)})
        org, session = uuid4(), uuid4()
        stages["capture_store_put_fsync"] = timed(lambda: store.put(org, session, uuid4(), document), args.repeat)
    body = json.dumps({"type": "kyc.verified", "data": {"session_id": str(uuid4())}}).encode()
    stages["webhook_sign"] = timed(lambda: sign(["whsec_" + "x" * 43], body), args.repeat * 100)
    return stages


def pipeline(args) -> dict:
    from load_test import Client, multipart
    from tests import images

    api_key = os.environ.get("KYC_LOAD_API_KEY")
    if not api_key:
        raise SystemExit("Set KYC_LOAD_API_KEY to a provisioned API key.")
    if not images.fonts_available():
        raise SystemExit("Khmer/Latin fonts for the SPECIMEN passport are not installed.")
    photo = images.encode(images.photographed(images.kh_passport()))
    headers = {"X-API-Key": api_key, "X-Organization-ID": args.organization}

    def one(_):
        client = Client(args.base_url, headers, 120)
        status, created, _ = client.request("POST", "/v1/kyc/sessions", json.dumps(
            {"user_id": f"bench-{uuid4().hex[:10]}", "country": "KH", "expected_document_type": "KH_PASSPORT",
             "verification_level": "DOCUMENT_ONLY"}).encode(), {"Content-Type": "application/json"})
        session = created["session_id"]
        client.request("POST", f"/v1/kyc/{session}/consent", b'{"scope":"DOCUMENT_PROCESSING","granted":true}',
                       {"Content-Type": "application/json"})
        raw, content_type = multipart({"side": "DATA_PAGE"}, "file", "passport.jpg", photo)
        status, uploaded, upload_seconds = client.request("POST", f"/v1/kyc/{session}/documents", raw,
                                                          {"Content-Type": content_type})
        accepted = time.perf_counter()
        final = uploaded.get("status") if isinstance(uploaded, dict) else None
        while final == "DOCUMENT_PROCESSING" and time.perf_counter() - accepted < 180:
            time.sleep(.05)
            _, state, _ = client.request("GET", f"/v1/kyc/{session}")
            final = state["status"]
        return {"upload_ms": round(upload_seconds * 1000, 1), "processing_ms": round((time.perf_counter() - accepted) * 1000, 1),
                "final_status": final, "capture_status": uploaded.get("capture_status") if isinstance(uploaded, dict) else status}

    results = {}
    for parallel in (int(item) for item in args.parallel.split(",")):
        started = time.perf_counter()
        with ThreadPoolExecutor(parallel) as pool:
            runs = list(pool.map(one, range(args.sessions)))
        wall = time.perf_counter() - started
        processing = sorted(run["processing_ms"] for run in runs)
        results[f"parallel_{parallel}"] = {
            "documents": len(runs), "wall_s": round(wall, 2), "documents_per_minute": round(len(runs) / wall * 60, 1),
            "processing_ms": {"p50": processing[len(processing) // 2], "max": processing[-1],
                              "mean": round(statistics.fmean(processing), 1)},
            "upload_ms_p50": sorted(run["upload_ms"] for run in runs)[len(runs) // 2],
            "final_statuses": sorted({run["final_status"] for run in runs}),
            "capture_statuses": sorted({str(run["capture_status"]) for run in runs})}
        print(f"pipeline parallel={parallel}: {results[f'parallel_{parallel}']}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("cpu", "pipeline"))
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--base-url", default="http://127.0.0.1:8018")
    parser.add_argument("--organization")
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--parallel", default="1,3")
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "pipeline" and not args.organization:
        raise SystemExit("--organization is required for the pipeline benchmark.")
    data = cpu(args) if args.mode == "cpu" else pipeline(args)
    if args.mode == "cpu":
        for name, value in data.items():
            print(f"{name:34} {value}")
    report = {"tool": "scripts/benchmark_stages.py", "mode": args.mode, "label": args.label,
              "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "host": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
              "results": data}
    if args.output:
        existing = json.loads(args.output.read_text()) if args.output.exists() else {"runs": []}
        existing["runs"].append(report)
        args.output.write_text(json.dumps(existing, indent=2) + "\n")


if __name__ == "__main__":
    main()
