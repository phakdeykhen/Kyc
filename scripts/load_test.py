"""Closed-loop HTTP load test for the KYC API (Phase 18). Standard library only.

  python scripts/load_test.py --base-url http://127.0.0.1:8018 --organization ORG --api-key-env KYC_LOAD_API_KEY \
      --scenario status_poll --concurrency 1,8,32,64 --duration 20 --output artifacts/phase18-load.json

Each virtual user owns one keep-alive connection and sends the next request as soon as the
previous answer arrives (closed loop), so throughput is what the server sustains at that
concurrency and latency includes any queueing inside it. Scenarios:

  health          GET /health/live                      framework floor, no database
  status_poll     GET /v1/kyc/{id} with a client token  the device's polling path
  result          GET /v1/kyc/{id}/result               the integrator's read path
  create_session  POST /v1/kyc/sessions (Idempotency-Key)
  document_upload POST a synthetic, quality-passing document photo to a fresh session
                  (quality gate, AES-GCM storage, state change; OCR runs after the response)
  mixed           70% status_poll, 10% result, 10% create_session, 10% organization

Use a provisioned API key whose rate limit exceeds the planned request rate; the
in-process limiter otherwise turns the test into a 429 measurement. Only synthetic,
non-personal SPECIMEN data is sent. Sessions created here belong to the test organization;
purge or erase them afterwards.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import platform
import random
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MIX = (("status_poll", 70), ("result", 10), ("create_session", 10), ("organization", 10))


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile; None for no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


@dataclass
class Sample:
    operation: str
    status: int
    seconds: float


@dataclass
class Level:
    concurrency: int
    duration: float
    samples: list[Sample] = field(default_factory=list)

    def summary(self) -> dict:
        latencies = [sample.seconds * 1000 for sample in self.samples]
        statuses = Counter(sample.status for sample in self.samples)
        ok = sum(count for status, count in statuses.items() if 200 <= status < 300)
        by_operation = {}
        for name in sorted({sample.operation for sample in self.samples}):
            values = [sample.seconds * 1000 for sample in self.samples if sample.operation == name]
            by_operation[name] = {"requests": len(values), "p50_ms": _round(percentile(values, .5)),
                                  "p95_ms": _round(percentile(values, .95)), "p99_ms": _round(percentile(values, .99))}
        return {"concurrency": self.concurrency, "duration_s": round(self.duration, 2), "requests": len(self.samples),
                "throughput_rps": round(len(self.samples) / self.duration, 1) if self.duration else 0,
                "success_rate": round(ok / len(self.samples), 4) if self.samples else None,
                "statuses": {str(key): value for key, value in sorted(statuses.items())},
                "latency_ms": {"mean": _round(statistics.fmean(latencies) if latencies else None),
                               "p50": _round(percentile(latencies, .5)), "p90": _round(percentile(latencies, .9)),
                               "p95": _round(percentile(latencies, .95)), "p99": _round(percentile(latencies, .99)),
                               "max": _round(max(latencies) if latencies else None)},
                "operations": by_operation}


def _round(value):
    return None if value is None else round(value, 2)


class Client:
    """One keep-alive connection; reconnects after a connection error."""

    def __init__(self, base_url: str, headers: dict[str, str], timeout: float):
        parts = urlsplit(base_url)
        self.host, self.port, self.secure = parts.hostname, parts.port, parts.scheme == "https"
        self.headers, self.timeout = headers, timeout
        self.connection = None

    def _connect(self):
        factory = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        self.connection = factory(self.host, self.port, timeout=self.timeout)

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        merged = {**self.headers, **(headers or {})}
        if body is not None:
            merged["Content-Length"] = str(len(body))
        # A server closes idle keep-alive connections (uvicorn: 5 s). Like any HTTP client, retry
        # once on a fresh connection when a reused one turns out to be closed; the timer restarts.
        for attempt in (1, 2):
            reused = self.connection is not None
            if not reused:
                self._connect()
            started = time.perf_counter()
            try:
                self.connection.request(method, path, body=body, headers=merged)
                response = self.connection.getresponse()
                data = response.read()
                status = response.status
                break
            except (OSError, http.client.HTTPException):
                self.connection.close()
                self.connection = None
                if not reused or attempt == 2:
                    return 0, None, time.perf_counter() - started
        elapsed = time.perf_counter() - started
        parsed = json.loads(data) if data and response.getheader("content-type", "").startswith("application/json") else None
        return status, parsed, elapsed


def multipart(fields: dict[str, str], name: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class Workload:
    def __init__(self, args, api_headers: dict[str, str]):
        self.args = args
        self.api_headers = api_headers
        self.sessions: list[tuple[str, str]] = []   # (session_id, client_token)
        self.document = None
        self.lock = threading.Lock()

    def session_body(self) -> bytes:
        return json.dumps({"user_id": f"load-{uuid4().hex[:12]}", "country": "KH", "expected_document_type": "KH_PASSPORT",
                           "verification_level": "DOCUMENT_ONLY"}).encode()

    def create(self, client: Client) -> tuple[int, dict | None, float]:
        return client.request("POST", "/v1/kyc/sessions", self.session_body(),
                              {"Content-Type": "application/json", "Idempotency-Key": f"load-{uuid4().hex}"})

    def prepare(self, count: int) -> None:
        client = Client(self.args.base_url, self.api_headers, self.args.timeout)
        for _ in range(count):
            status, body, _ = self.create(client)
            if status != 201:
                raise SystemExit(f"Could not create a session for the test (HTTP {status}): {body}")
            status, token, _ = client.request("POST", f"/v1/kyc/{body['session_id']}/client-token", b"{}",
                                              {"Content-Type": "application/json"})
            if status != 201:
                raise SystemExit(f"Could not issue a client token (HTTP {status}): {token}")
            self.sessions.append((body["session_id"], token["client_token"]))
        if self.args.scenario == "document_upload":
            sys.path.insert(0, str(ROOT))
            from tests import images  # synthetic, non-personal document scene
            self.document = images.encode(images.good())

    def operation(self, name: str, client: Client, device: dict[str, Client], rng: random.Random):
        if name == "health":
            return client.request("GET", "/health/live")
        if name == "organization":
            return client.request("GET", "/v1/organization")
        if name == "create_session":
            return self.create(client)
        if name == "document_upload":
            status, body, _ = self.create(client)   # setup, not timed
            if status != 201:
                return status, body, 0.0
            if self.args.document_consent:
                client.request("POST", f"/v1/kyc/{body['session_id']}/consent",
                               json.dumps({"scope": "DOCUMENT_PROCESSING", "granted": True}).encode(),
                               {"Content-Type": "application/json"})
            raw, content_type = multipart({"side": "DATA_PAGE"}, "file", "page.jpg", self.document)
            return client.request("POST", f"/v1/kyc/{body['session_id']}/documents", raw, {"Content-Type": content_type})
        session_id, token = rng.choice(self.sessions)
        if name == "result":
            return client.request("GET", f"/v1/kyc/{session_id}/result")
        if name == "status_poll":
            if session_id not in device:
                device[session_id] = Client(self.args.base_url, {"Authorization": f"Bearer {token}",
                                            "X-Organization-ID": self.args.organization}, self.args.timeout)
            return device[session_id].request("GET", f"/v1/kyc/{session_id}")
        raise ValueError(name)

    def choose(self, rng: random.Random) -> str:
        if self.args.scenario != "mixed":
            return self.args.scenario
        point, total = rng.uniform(0, 100), 0
        for name, weight in MIX:
            total += weight
            if point <= total:
                return name
        return MIX[-1][0]

    def run_level(self, concurrency: int, duration: float, warmup: float) -> Level:
        level = Level(concurrency, duration)
        start = time.perf_counter()
        measure_from, deadline = start + warmup, start + warmup + duration

        def user(seed: int):
            rng = random.Random(seed)
            client, device, local = Client(self.args.base_url, self.api_headers, self.args.timeout), {}, []
            while (now := time.perf_counter()) < deadline:
                name = self.choose(rng)
                status, _, elapsed = self.operation(name, client, device, rng)
                if now >= measure_from:
                    local.append(Sample(name, status, elapsed))
            with self.lock:
                level.samples.extend(local)

        threads = [threading.Thread(target=user, args=(index,), daemon=True) for index in range(concurrency)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(duration + warmup + self.args.timeout + 30)
        return level


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8018")
    parser.add_argument("--organization", required=True)
    parser.add_argument("--api-key-env", default="KYC_LOAD_API_KEY", help="environment variable holding the API key")
    parser.add_argument("--scenario", default="mixed",
                        choices=("health", "status_poll", "result", "create_session", "document_upload", "mixed"))
    parser.add_argument("--concurrency", default="1,8,32,64")
    parser.add_argument("--duration", type=float, default=20.0, help="measured seconds per level")
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--sessions", type=int, default=200, help="pre-created sessions for read scenarios")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--document-consent", action="store_true", help="record consent before uploads")
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(f"Set {args.api_key_env} to a provisioned API key (never pass keys on the command line).")
    workload = Workload(args, {"X-API-Key": api_key, "X-Organization-ID": args.organization, "Connection": "keep-alive"})
    reads = args.scenario in ("status_poll", "result", "mixed")
    workload.prepare(args.sessions if reads else 0)
    levels = []
    for concurrency in (int(item) for item in args.concurrency.split(",")):
        summary = workload.run_level(concurrency, args.duration, args.warmup).summary()
        levels.append(summary)
        latency = summary["latency_ms"]
        print(f"{args.scenario:16} c={concurrency:<4} {summary['throughput_rps']:>8} rps  p50 {latency['p50']} ms  "
              f"p95 {latency['p95']} ms  p99 {latency['p99']} ms  statuses {summary['statuses']}", flush=True)
    report = {"tool": "scripts/load_test.py", "label": args.label, "scenario": args.scenario, "base_url": args.base_url,
              "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "client_host": {"platform": platform.platform(),
              "python": platform.python_version(), "cpus": os.cpu_count()},
              "settings": {"duration_s": args.duration, "warmup_s": args.warmup, "sessions": len(workload.sessions)},
              "levels": levels}
    if args.output:
        existing = json.loads(args.output.read_text()) if args.output.exists() else {"runs": []}
        existing["runs"].append(report)
        args.output.write_text(json.dumps(existing, indent=2) + "\n")


if __name__ == "__main__":
    main()
