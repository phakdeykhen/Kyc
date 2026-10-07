"""Process-local metrics in the Prometheus text format (no extra dependency).

Business counters are derived from committed audit records, so a metric moves only when
the event it counts is durable, and a rolled-back attempt is never counted. Rates such as
manual_review_rate or ocr_failure_rate are ratios of these counters, computed by the
monitoring system (see infra/monitoring/alerts.yaml). Latency is recorded in histograms so
p50/p95/p99 can be derived.

Labels never carry identity data: only route templates, stages, results and reason codes.
Each worker process keeps its own counters; the scraper sums them.
"""

from bisect import bisect_left
from collections import defaultdict
import threading
import time

from sqlalchemy import event
from sqlalchemy.orm import Session as ORMSession

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

HELP = {
    "kyc_http_requests_total": "HTTP requests by route template and status class.",
    "kyc_http_request_seconds": "HTTP request latency by route template.",
    "kyc_sessions_total": "Sessions created.",
    "kyc_pass_total": "Risk-engine PASS decisions.",
    "kyc_review_total": "Risk-engine REVIEW decisions (sent to manual review).",
    "kyc_fail_total": "Risk-engine FAIL decisions.",
    "kyc_expired_total": "Sessions that expired.",
    "kyc_technical_error_total": "Steps stopped by an internal failure, by stage (never an identity decision).",
    "kyc_recapture_total": "Captures the person was asked to retake, by stage.",
    "kyc_document_quality_total": "Document capture quality gate outcomes.",
    "kyc_ocr_total": "Document extraction outcomes.",
    "kyc_face_match_total": "Face comparison results by band.",
    "kyc_liveness_total": "Liveness results.",
    "kyc_nfc_total": "ePassport chip results by status.",
    "kyc_manual_review_decisions_total": "Reviewer decisions by action.",
    "kyc_reason_codes_total": "Reason codes on risk decisions.",
    "kyc_ocr_seconds": "OCR engine call latency.",
    "kyc_face_seconds": "Face detection and embedding latency by operation.",
    "kyc_db_pool_checked_out": "Database connections in use by this process.",
    "kyc_requests_in_flight": "Requests holding an admission slot in this process.",
}
TYPES = {name: "histogram" if name.endswith("_seconds") else "gauge" if name in (
    "kyc_db_pool_checked_out", "kyc_requests_in_flight") else "counter" for name in HELP}


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self.histograms: dict[tuple[str, tuple], list] = {}
        self.gauges: dict[str, callable] = {}

    def inc(self, name: str, amount: float = 1, **labels) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += amount

    def observe(self, name: str, seconds: float, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            state = self.histograms.setdefault(key, [[0] * (len(LATENCY_BUCKETS) + 1), 0.0, 0])
            state[0][bisect_left(LATENCY_BUCKETS, seconds)] += 1
            state[1] += seconds
            state[2] += 1

    def timer(self, name: str, **labels):
        registry = self

        class _Timer:
            def __enter__(self):
                self.start = time.perf_counter()

            def __exit__(self, *exc):
                registry.observe(name, time.perf_counter() - self.start, **labels)
                return False
        return _Timer()

    def gauge(self, name: str, read) -> None:
        self.gauges[name] = read

    def value(self, name: str, **labels) -> float:
        return self.counters.get((name, tuple(sorted(labels.items()))), 0)

    def render(self) -> str:
        with self._lock:
            counters, histograms = dict(self.counters), {k: [list(v[0]), v[1], v[2]] for k, v in self.histograms.items()}
        lines, seen = [], set()

        def header(name):
            if name not in seen:
                seen.add(name)
                lines.append(f"# HELP {name} {HELP.get(name, name)}")
                lines.append(f"# TYPE {name} {TYPES.get(name, 'counter')}")

        def labels_text(labels, extra=()):
            items = [*labels, *extra]
            return "{" + ",".join(f'{k}="{_escape(str(v))}"' for k, v in items) + "}" if items else ""

        for (name, labels), value in sorted(counters.items()):
            header(name)
            lines.append(f"{name}{labels_text(labels)} {value:g}")
        for (name, labels), (buckets, total, count) in sorted(histograms.items()):
            header(name)
            running = 0
            for bound, hits in zip(LATENCY_BUCKETS, buckets):
                running += hits
                lines.append(f"{name}_bucket{labels_text(labels, (('le', f'{bound:g}'),))} {running}")
            lines.append(f"{name}_bucket{labels_text(labels, (('le', '+Inf'),))} {count}")
            lines.append(f"{name}_sum{labels_text(labels)} {total:.6f}")
            lines.append(f"{name}_count{labels_text(labels)} {count}")
        for name, read in sorted(self.gauges.items()):
            try:
                value = float(read())
            except Exception:  # noqa: BLE001 - a gauge that cannot be read is omitted, not reported as zero
                continue
            header(name)
            lines.append(f"{name} {value:g}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


REGISTRY = Registry()

TECHNICAL_STAGES = {"RISK_ASSESSMENT_FAILED": "risk", "DOCUMENT_PROCESSING_FAILED": "document",
                    "FRAUD_ANALYSIS_FAILED": "fraud", "BIOMETRIC_PROCESSING_UNAVAILABLE": "face",
                    "LIVENESS_UNAVAILABLE": "liveness", "REFERENCE_FACE_UNAVAILABLE": "portrait"}
RECAPTURE_STAGES = {"DOCUMENT_CAPTURE_REJECTED": "document", "DOCUMENT_RECAPTURE_REQUESTED": "document",
                    "SELFIE_CAPTURE_RECAPTURE": "selfie", "SELFIE_CAPTURE_REJECTED": "selfie",
                    "LIVENESS_RETRY_REQUIRED": "liveness", "NFC_RETRY_REQUIRED": "nfc"}
DECISIONS = {"PASS": "kyc_pass_total", "REVIEW": "kyc_review_total", "FAIL": "kyc_fail_total"}


def count_audit(row, registry: Registry = REGISTRY) -> None:
    """Translate one committed audit record into business metrics."""
    action, meta = row.action, row.event_metadata or {}
    if action == "SESSION_CREATED":
        registry.inc("kyc_sessions_total")
    elif action == "RISK_ASSESSED" and meta.get("decision") in DECISIONS:
        registry.inc(DECISIONS[meta["decision"]])
        for code in row.reason_codes or []:
            registry.inc("kyc_reason_codes_total", decision=meta["decision"], reason=code)
    elif action == "SESSION_EXPIRED":
        registry.inc("kyc_expired_total")
    elif action in TECHNICAL_STAGES:
        registry.inc("kyc_technical_error_total", stage=TECHNICAL_STAGES[action])
    if action in RECAPTURE_STAGES:
        registry.inc("kyc_recapture_total", stage=RECAPTURE_STAGES[action])
    if action in ("DOCUMENT_CAPTURE_ACCEPTED", "DOCUMENT_CAPTURE_REJECTED"):
        registry.inc("kyc_document_quality_total", result="PASS" if action.endswith("ACCEPTED") else "RECAPTURE")
    elif action in ("DOCUMENT_EXTRACTED", "DOCUMENT_PROCESSING_FAILED"):
        registry.inc("kyc_ocr_total", result="SUCCESS" if action == "DOCUMENT_EXTRACTED" else "FAILURE")
    elif action == "FACE_COMPARISON_RECORDED":
        registry.inc("kyc_face_match_total", result=str(meta.get("result", "UNKNOWN")))
    elif action == "LIVENESS_RECORDED":
        registry.inc("kyc_liveness_total", result=str(meta.get("result", "UNKNOWN")))
    elif action == "NFC_RECORDED":
        registry.inc("kyc_nfc_total", status=str(meta.get("nfc_status", "UNKNOWN")))
    elif action == "REVIEW_DECISION":
        registry.inc("kyc_manual_review_decisions_total", action=str(meta.get("action", "UNKNOWN")))


_PENDING = "kyc_metrics_pending_audit"


def install(registry: Registry = REGISTRY) -> None:
    """Count audit records when, and only when, their transaction commits."""
    from kyc.db.models import AuditLog

    if getattr(install, "done", False):
        return
    install.done = True

    @event.listens_for(ORMSession, "after_flush")
    def _collect(session, _context):
        found = [item for item in session.new if isinstance(item, AuditLog)]
        if found:
            session.info.setdefault(_PENDING, []).extend(found)

    @event.listens_for(ORMSession, "after_commit")
    def _commit(session):
        for row in session.info.pop(_PENDING, []):
            count_audit(row, registry)

    @event.listens_for(ORMSession, "after_rollback")
    def _rollback(session):
        session.info.pop(_PENDING, None)
