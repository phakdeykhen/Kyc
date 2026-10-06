"""Provision organizations and their API keys (administrative; uses MIGRATION_DATABASE_URL).

  python scripts/manage_tenants.py org create "Acme Bank" [--id UUID] [--pii-retention-days 30]
                                   [--capture-retention-hours 24] [--template-retention-hours 24]
  python scripts/manage_tenants.py org show ORG
  python scripts/manage_tenants.py org list
  python scripts/manage_tenants.py org suspend ORG | org activate ORG
  python scripts/manage_tenants.py org retention ORG [--pii-retention-days N] [--capture-retention-hours N]
                                   [--template-retention-hours N]
  python scripts/manage_tenants.py key create ORG "Backend" [--scope sessions:write --scope sessions:read ...]
                                   [--expires-in-days N] [--rate-limit N]
  python scripts/manage_tenants.py key list ORG
  python scripts/manage_tenants.py key revoke ORG KEY_ID

Each organization's rows are written inside its own row-level-security context. A key's
secret is printed once; only its SHA-256 is stored. `org list` reads every organization,
so it needs a role that bypasses row security (the local migrator owns the database).
"""

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.models import Organization  # noqa: E402
from kyc.db.session import set_tenant  # noqa: E402
from kyc.services import tenancy  # noqa: E402
from kyc.tenancy.keys import DEFAULT_SCOPES, SCOPES  # noqa: E402

ADMIN = "administrator"


def retention_options(parser, defaults):
    for name, default in zip(("pii-retention-days", "capture-retention-hours", "template-retention-hours"), defaults):
        parser.add_argument(f"--{name}", type=int, default=default)


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
groups = parser.add_subparsers(dest="group", required=True)
org = groups.add_parser("org").add_subparsers(dest="command", required=True)
created = org.add_parser("create")
created.add_argument("name")
created.add_argument("--id", type=UUID)
retention_options(created, (30, 24, 24))
for command in ("show", "suspend", "activate"):
    org.add_parser(command).add_argument("organization", type=UUID)
org.add_parser("list")
retention = org.add_parser("retention")
retention.add_argument("organization", type=UUID)
retention_options(retention, (None, None, None))
key = groups.add_parser("key").add_subparsers(dest="command", required=True)
key_create = key.add_parser("create")
key_create.add_argument("organization", type=UUID)
key_create.add_argument("name")
key_create.add_argument("--scope", action="append", choices=sorted(SCOPES), dest="scopes")
key_create.add_argument("--expires-in-days", type=int)
key_create.add_argument("--rate-limit", type=int)
key.add_parser("list").add_argument("organization", type=UUID)
key_revoke = key.add_parser("revoke")
key_revoke.add_argument("organization", type=UUID)
key_revoke.add_argument("key_id", type=UUID)
args = parser.parse_args()

settings = get_settings()
if settings.migration_database_url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required to manage tenants.")
engine = sa.create_engine(settings.migration_database_url.get_secret_value())


def show(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def load(db: Session, organization_id: UUID) -> Organization:
    row = db.get(Organization, organization_id)
    if row is None:
        raise SystemExit("Organization not found.")
    return row


try:
    with Session(engine) as db, db.begin():
        request_id = uuid4()
        if args.group == "org" and args.command == "list":
            show([tenancy.organization_view(row) for row in db.scalars(sa.select(Organization).order_by(Organization.created_at))])
        elif args.group == "org" and args.command == "create":
            organization_id = args.id or uuid4()
            set_tenant(db, organization_id)
            if db.get(Organization, organization_id):
                raise SystemExit("Organization already exists.")
            row = Organization(id=organization_id, name=args.name.strip()[:200], pii_retention_days=args.pii_retention_days,
                               capture_retention_hours=args.capture_retention_hours,
                               template_retention_hours=args.template_retention_hours, active=True)
            db.add(row)
            db.flush()
            tenancy.audit(db, organization_id, ADMIN, "ORGANIZATION_CREATED", request_id)
            show(tenancy.organization_view(row))
        elif args.group == "org":
            set_tenant(db, args.organization)
            row = load(db, args.organization)
            if args.command in ("suspend", "activate"):
                row.active = args.command == "activate"
                tenancy.audit(db, row.id, ADMIN, "ORGANIZATION_SUSPENDED" if not row.active else "ORGANIZATION_ACTIVATED",
                              request_id)
            elif args.command == "retention":
                for field in ("pii_retention_days", "capture_retention_hours", "template_retention_hours"):
                    if getattr(args, field) is not None:
                        setattr(row, field, getattr(args, field))
                tenancy.audit(db, row.id, ADMIN, "ORGANIZATION_RETENTION_CHANGED", request_id)
            db.flush()
            show(tenancy.organization_view(row))
        else:
            set_tenant(db, args.organization)
            load(db, args.organization)
            if args.command == "create":
                row, token = tenancy.create_key(db, args.organization, args.name.strip()[:120],
                                                args.scopes or list(DEFAULT_SCOPES), ADMIN, request_id,
                                                args.expires_in_days, args.rate_limit or settings.api_rate_limit_per_minute)
                show(tenancy.key_view(row))
                print(f"API key (shown once): {token}")
            elif args.command == "list":
                show([tenancy.key_view(row) for row in tenancy.list_keys(db, args.organization)])
            else:
                show(tenancy.key_view(tenancy.revoke_key(db, args.organization, args.key_id, ADMIN, request_id)))
except Exception as error:  # FastAPI's HTTPException carries the message for shared validation
    detail = getattr(error, "detail", None)
    if detail is None:
        raise
    raise SystemExit(detail) from None
finally:
    engine.dispose()
