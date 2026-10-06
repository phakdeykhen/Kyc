"""Create or deactivate a reviewer for one organization (administrative; uses MIGRATION_DATABASE_URL).

  python scripts/create_reviewer.py create "Sok Dara" --role REVIEWER [--organization UUID]
  python scripts/create_reviewer.py deactivate REVIEWER_ID [--organization UUID]

The token is printed once and only its SHA-256 is stored. Hand it to the reviewer over a
secure channel; it cannot be recovered, only replaced by creating a new reviewer.
"""

import argparse
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.models import AuditLog, Reviewer  # noqa: E402
from kyc.db.session import set_tenant  # noqa: E402
from kyc.review.access import ROLES, new_token  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = parser.add_subparsers(dest="command", required=True)
create = sub.add_parser("create")
create.add_argument("name")
create.add_argument("--role", choices=sorted(ROLES), default="REVIEWER")
create.add_argument("--organization", type=UUID)
off = sub.add_parser("deactivate")
off.add_argument("reviewer_id", type=UUID)
off.add_argument("--organization", type=UUID)
args = parser.parse_args()

settings = get_settings()
if settings.migration_database_url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required to manage reviewers.")
organization = args.organization or settings.development_organization_id
if organization is None:
    raise SystemExit("Pass --organization.")
engine = sa.create_engine(settings.migration_database_url.get_secret_value())
with Session(engine) as db, db.begin():
    set_tenant(db, organization)
    if args.command == "create":
        token, digest = new_token()
        reviewer = Reviewer(organization_id=organization, display_name=args.name.strip()[:120], role=args.role,
                            token_sha256=digest, active=True)
        db.add(reviewer)
        db.flush()
        db.add(AuditLog(organization_id=organization, actor_id="administrator", action="REVIEWER_CREATED",
                        request_id=reviewer.id, reason_codes=[args.role], event_metadata={"reviewer_id": str(reviewer.id)}))
        print(f"Reviewer {reviewer.id} ({args.role}) created for organization {organization}.")
        print(f"Token (shown once): {token}")
    else:
        reviewer = db.scalar(sa.select(Reviewer).where(Reviewer.id == args.reviewer_id, Reviewer.organization_id == organization))
        if reviewer is None:
            raise SystemExit("Reviewer not found in this organization.")
        reviewer.active = False
        db.add(AuditLog(organization_id=organization, actor_id="administrator", action="REVIEWER_DEACTIVATED",
                        request_id=reviewer.id, reason_codes=[reviewer.role], event_metadata={"reviewer_id": str(reviewer.id)}))
        print(f"Reviewer {reviewer.id} deactivated; the token no longer works.")
engine.dispose()
