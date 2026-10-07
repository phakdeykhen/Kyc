"""Create, rotate or deactivate a reviewer for one organization (administrative; uses MIGRATION_DATABASE_URL).

  python scripts/create_reviewer.py create "Sok Dara" --role REVIEWER [--organization UUID] [--expires-in-days N]
  python scripts/create_reviewer.py rotate REVIEWER_ID [--organization UUID] [--expires-in-days N]
  python scripts/create_reviewer.py deactivate REVIEWER_ID [--organization UUID]

The token is printed once and only its SHA-256 is stored. Hand it to the reviewer over a
secure channel; it cannot be recovered. Tokens expire (default REVIEWER_TOKEN_MAX_DAYS,
90 days); `rotate` issues a new token and the old one stops working at once.
"""

import argparse
from datetime import datetime, timedelta, timezone
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
create.add_argument("--expires-in-days", type=int)
rotate = sub.add_parser("rotate")
rotate.add_argument("reviewer_id", type=UUID)
rotate.add_argument("--organization", type=UUID)
rotate.add_argument("--expires-in-days", type=int)
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
days = getattr(args, "expires_in_days", None)
days = settings.reviewer_token_max_days if days is None else days
if not 1 <= days <= settings.reviewer_token_max_days:
    raise SystemExit(f"--expires-in-days must be between 1 and {settings.reviewer_token_max_days}.")
expires_at = datetime.now(timezone.utc) + timedelta(days=days)
engine = sa.create_engine(settings.migration_database_url.get_secret_value())
with Session(engine) as db, db.begin():
    set_tenant(db, organization)
    if args.command == "create":
        token, digest = new_token()
        reviewer = Reviewer(organization_id=organization, display_name=args.name.strip()[:120], role=args.role,
                            token_sha256=digest, active=True, expires_at=expires_at)
        db.add(reviewer)
        db.flush()
        db.add(AuditLog(organization_id=organization, actor_id="administrator", action="REVIEWER_CREATED",
                        request_id=reviewer.id, reason_codes=[args.role], event_metadata={"reviewer_id": str(reviewer.id)}))
        print(f"Reviewer {reviewer.id} ({args.role}) created for organization {organization}; expires {expires_at:%Y-%m-%d}.")
        print(f"Token (shown once): {token}")
    else:
        reviewer = db.scalar(sa.select(Reviewer).where(Reviewer.id == args.reviewer_id, Reviewer.organization_id == organization))
        if reviewer is None:
            raise SystemExit("Reviewer not found in this organization.")
    if args.command == "rotate":
        if not reviewer.active:
            raise SystemExit("Reviewer is deactivated; create a new reviewer instead.")
        token, reviewer.token_sha256 = new_token()
        reviewer.expires_at = expires_at
        db.add(AuditLog(organization_id=organization, actor_id="administrator", action="REVIEWER_TOKEN_ROTATED",
                        request_id=reviewer.id, reason_codes=[reviewer.role], event_metadata={"reviewer_id": str(reviewer.id)}))
        print(f"Reviewer {reviewer.id}: new token issued, expires {expires_at:%Y-%m-%d}; the old token no longer works.")
        print(f"Token (shown once): {token}")
    elif args.command == "deactivate":
        reviewer.active = False
        db.add(AuditLog(organization_id=organization, actor_id="administrator", action="REVIEWER_DEACTIVATED",
                        request_id=reviewer.id, reason_codes=[reviewer.role], event_metadata={"reviewer_id": str(reviewer.id)}))
        print(f"Reviewer {reviewer.id} deactivated; the token no longer works.")
engine.dispose()
