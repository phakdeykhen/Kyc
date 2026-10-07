"""Erase a data subject's personal and biometric data (operator tool for data-subject requests).

  python scripts/erase_subject.py ORGANIZATION_ID --user-id CUSTOMER-REF [--reason DATA_SUBJECT_REQUEST]
  python scripts/erase_subject.py ORGANIZATION_ID --session SESSION_ID

Runs as the API role (DATABASE_URL) inside the organization's row-level-security context
and does what POST /v1/kyc/{session}/erase does, for every session with that `user_id`.
Decisions, reason codes and the audit trail are kept; see docs/architecture-phase17.md.
"""

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from kyc.api.dependencies import TenantContext  # noqa: E402
from kyc.core.config import get_settings  # noqa: E402
from kyc.db.models import KYCSession  # noqa: E402
from kyc.db.session import build_engine, set_tenant  # noqa: E402
from kyc.main import build_capture_store  # noqa: E402
from kyc.services.erasure import delete_objects, erase_session, subject_sessions  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("organization", type=UUID)
target = parser.add_mutually_exclusive_group(required=True)
target.add_argument("--user-id", help="the integrator's user reference, exactly as sent at session creation")
target.add_argument("--session", type=UUID)
parser.add_argument("--reason", default="DATA_SUBJECT_REQUEST", choices=("DATA_SUBJECT_REQUEST", "CONSENT_WITHDRAWN",
                                                                         "LEGAL_REQUIREMENT"))
args = parser.parse_args()

settings = get_settings()
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
store = build_capture_store(settings)
actor = TenantContext(args.organization, actor_id="administrator")
request_id = uuid4()
reports = []
try:
    with factory() as db, db.begin():
        set_tenant(db, args.organization)
        if args.session:
            sessions = list(db.scalars(sa.select(KYCSession).where(KYCSession.organization_id == args.organization,
                                                                   KYCSession.id == args.session).with_for_update()))
        else:
            sessions = subject_sessions(db, args.organization, args.user_id)
        if not sessions:
            raise SystemExit("No matching session in this organization (already erased sessions no longer carry the user_id).")
        for record in sessions:
            reports.append((erase_session(db, actor, record, request_id, args.reason), record.status.value))
    removed = sum(delete_objects(store, report) for report, _ in reports) if store is not None else 0
    print(json.dumps({"organization_id": args.organization, "request_id": request_id, "objects_deleted": removed,
                      "sessions": [report.view(status) for report, status in reports]}, indent=2, default=str))
finally:
    engine.dispose()
