"""Synthetic encrypted-gallery CPU benchmark; never biometric validation or release approval.

Measures decryption, validated deserialization and exact comparison, excluding the
database, HTTP, camera/PAD and profile authorization. No identities or scores emitted.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
from kyc.biometrics.embeddings import serialize_embedding, deserialize_embedding
from kyc.biometrics.types import FaceEmbedding
from kyc.storage.biometrics import BiometricCipher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--populations", default="100,1000,10000")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    populations = [int(value) for value in args.populations.split(",")]
    if not populations or min(populations) < 1 or max(populations) > 10000 or not 10 <= args.repeat <= 100:
        parser.error("Use populations 1–10000 and 10–100 repetitions.")
    cipher = BiometricCipher("synthetic:" + base64.b64encode(os.urandom(32)).decode())
    random = np.random.default_rng(20261023)
    probe = random.normal(size=128).astype(np.float32)
    probe /= np.linalg.norm(probe)
    results = []
    for population in populations:
        rows = []
        for _ in range(population):
            vector = random.normal(size=128).astype(np.float32)
            vector /= np.linalg.norm(vector)
            aad = f"biometric/synthetic/{uuid4()}/face-identity/enrollment/model/sealed/v1"
            sealed, version = cipher.seal(serialize_embedding(FaceEmbedding(vector)), aad)
            rows.append((sealed, version, aad))
        def search():
            # The running service likewise compares distinct enrollments exactly,
            # with no plaintext persistent vector index or correlated frame votes.
            best, second = -float("inf"), -float("inf")
            for sealed, version, aad in rows:
                vector = deserialize_embedding(cipher.open(sealed, version, aad)).vector
                score = float(np.dot(probe, vector))
                if score > best: second, best = best, score
                elif score > second: second = score
        search()
        elapsed = []
        for _ in range(args.repeat):
            started = time.perf_counter()
            search()
            elapsed.append((time.perf_counter() - started) * 1000)
        ordered = sorted(elapsed)
        results.append({"population": population, "repetitions": args.repeat,
            "p50_ms": round(statistics.median(elapsed), 3), "p95_ms": round(ordered[max(0, int(args.repeat * .95) - 1)], 3)})
    report = {"synthetic": True, "biometric_accuracy_measured": False, "production_ready": False,
        "scope": "CPU_ONLY_ENCRYPTED_GALLERY_DECRYPT_DESERIALIZE_EXACT_COMPARE", "machine": platform.machine(),
        "excludes": ["database", "HTTP", "camera", "quality/PAD", "authorization", "real biometric accuracy"], "results": results}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
