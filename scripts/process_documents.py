"""Process sessions waiting in DOCUMENT_PROCESSING (deferred mode, or retries after OCR failures),
then assess (fraud signals + risk decision) every PROCESSING session.

Usage: python scripts/process_documents.py [ORGANIZATION_ID ...] [--parallel N] [--loop SECONDS]
Defaults to the local development organization. Safe to run repeatedly, and safe to run as
several processes: each session is processed under its row lock, and workers only take
sessions that no other worker is processing (SKIP LOCKED).

Phase 18: with DOCUMENT_PROCESSING_MODE=deferred this is the OCR tier. --parallel runs N
sessions at once (Tesseract runs as subprocesses, so this uses N cores); size the
database pool for N connections. --loop keeps polling for new work.
"""

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time

from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy.orm import sessionmaker

from kyc.core.config import get_settings
from kyc.db.session import build_engine
from kyc.main import (build_biometric_cipher, build_capture_store, build_document_processor, build_field_cipher,
                      build_fraud_analyzer)
from kyc.biometrics import FaceMatchPolicy
from kyc.services.documents import pending_sessions
from kyc.risk.policy import RiskPolicy
from kyc.services.risk import SessionAssessor, pending_assessments

settings = get_settings()
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
store, cipher = build_capture_store(settings), build_field_cipher(settings)
processor = build_document_processor(settings, factory, store, cipher)
analyzer = build_fraud_analyzer(settings, factory, cipher, build_biometric_cipher(settings), store, FaceMatchPolicy(
    version=settings.face_match_policy_version, pass_threshold=settings.face_match_pass_threshold,
    fail_threshold=settings.face_match_fail_threshold, calibrated=settings.face_match_calibrated,
    calibration_reference=settings.face_match_calibration_reference))
assessor = SessionAssessor(factory, analyzer, RiskPolicy.load(settings.risk_policy_file))
reason = processor.unavailable_reason()
if reason:
    raise SystemExit(f"Document processing unavailable: {reason}")
parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("organizations", nargs="*", type=UUID)
parser.add_argument("--parallel", type=int, default=1, help="sessions processed at once (default 1)")
parser.add_argument("--loop", type=float, metavar="SECONDS", help="keep running, pausing this long when idle")
args = parser.parse_args()
organizations = args.organizations or [item for item in [settings.development_organization_id] if item is not None]
if not organizations:
    raise SystemExit("Pass one or more organization IDs.")
if not 1 <= args.parallel <= settings.db_pool_size + settings.db_max_overflow:
    raise SystemExit("--parallel must be between 1 and DB_POOL_SIZE + DB_MAX_OVERFLOW.")


RETRY_SECONDS = 60
NO_PROGRESS = {"ERROR", "UNAVAILABLE", "NO_ADAPTER"}   # the session stays waiting; try it again later


def handle(organization_id: UUID, session_id: UUID) -> str:
    outcome = processor.process(organization_id, session_id, uuid4(), wait=False)
    if outcome.status != "BUSY":   # BUSY: another worker has it
        print(f"{session_id}: {outcome.status} {' '.join(outcome.reason_codes)}".rstrip(), flush=True)
    if outcome.status == "ACCEPTED":
        print(f"{session_id}: assessment {assessor.assess(organization_id, session_id, uuid4())}", flush=True)
    return outcome.status


def sweep_assessments() -> None:
    """Decisions left behind by a crash or another path."""
    for organization_id in organizations:
        for session_id in pending_assessments(factory, organization_id):
            print(f"{session_id}: assessment {assessor.assess(organization_id, session_id, uuid4())}", flush=True)


# Keep up to --parallel sessions in flight and refill a slot as soon as one finishes, taking only
# sessions no other worker has locked (Phase 18: batch-at-a-time processing left slots idle and
# several workers queued on the same session's row lock).
in_flight: dict = {}
retry_at: dict = {}
try:
    with ThreadPoolExecutor(args.parallel, thread_name_prefix="kyc-documents") as pool:
        while True:
            free = args.parallel - len(in_flight)
            for organization_id in organizations if free > 0 else ():
                for session_id in pending_sessions(factory, organization_id, unclaimed=True, limit=free + len(in_flight)):
                    if free > 0 and session_id not in in_flight and retry_at.get(session_id, 0) <= time.monotonic():
                        in_flight[session_id] = pool.submit(handle, organization_id, session_id)
                        free -= 1
            if in_flight:
                done, _ = wait(list(in_flight.values()), timeout=args.loop or 0.5, return_when=FIRST_COMPLETED)
                for session_id in [key for key, future in in_flight.items() if future in done]:
                    if in_flight.pop(session_id).result() in NO_PROGRESS:
                        retry_at[session_id] = time.monotonic() + RETRY_SECONDS
                continue
            if args.loop is None and any(when > time.monotonic() for when in retry_at.values()):
                break   # one-shot run: failed sessions are left for the next run
            sweep_assessments()
            if args.loop is None:
                break
            time.sleep(args.loop)
except KeyboardInterrupt:
    pass
finally:
    engine.dispose()
