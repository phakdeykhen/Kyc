"""Provision organizations and their API keys (administrative; uses MIGRATION_DATABASE_URL).

  python scripts/manage_tenants.py org-create "Acme Bank" [--rate-limit 120] [--pii-retention-days 30]
  python scripts/manage_tenants.py org-suspend ORG_ID      | org-activate ORG_ID
  python scripts/manage_tenants.py key-create ORG_ID "Backend" [--scope sessions:create ...] [--expires-days 365]
  python scripts/manage_tenants.py key-list ORG_ID
  python scripts/manage_tenants.py key-revoke ORG_ID KEY_ID
  python scripts/manage_tenants.py key-rotate ORG_ID KEY_ID [--grace-hours 24]

A key is printed once and only its SHA-256 is stored; hand it over a secure channel. Give an
organization one key with the keys:manage scope and it can issue and revoke the rest through
/v1/api-keys. Suspending an organization stops all its keys at once and keeps its data.
"""

import argparse
from datetime import timedelta
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.models import ApiKey, AuditLog, Organization  # noqa: E402
from kyc.db.session import set_tenant  # noqa: E402
from kyc.tenancy import keys  # noqa: E402

ACTOR = "administrator"

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = parser.add_subparsers(dest="command", required=True)
org_create = sub.add_parser("org-create")
org_create.add_argument("name")
org_create.add_argument("--rate-limit", type=int, default=120)
org_create.add_argument("--pii-retention-days", type=int, default=30)
org_create.add_argument("--capture-retention-hours", type=int, default=24)
org_create.add_argument("--template-retention-hours", type=int, default=24)
for command in ("org-suspend", "org-activate", "key-list"):
    sub.add_parser(command).add_argument("organization", type=UUID)
key_create = sub.add_parser("key-create")
key_create.add_argument("organization", type=UUID)
key_create.add_argument("name")
key_create.add_argument("--scope", action="append", choices=sorted(keys.SCOPES),
                        help=f"Repeat for several. Default: {' '.join(keys.DEFAULT_SCOPES)}")
key_create.add_argument("--expires-days", type=int)
for command in ("key-revoke", "key-rotate"):
    parsed = sub.add_parser(command)
    parsed.add_argument("organization", type=UUID)
    parsed.add_argument("key_id", type=UUID)
sub.choices["key-rotate"].add_argument("--grace-hours", type=int, default=24)
args = parser.parse_args()

settings = get_settings()
if settings.migration_database_url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required to manage organizations and keys.")
engine = sa.create_engine(settings.migration_database_url.get_secret_value())
request_id = uuid4()


def organization_or_exit(db: Session, organization_id: UUID) -> Organization:
    record = db.get(Organization, organization_id)
    if record is None:
        raise SystemExit("Organization not found.")
    return record


def key_or_exit(db: Session, organization_id: UUID, key_id: UUID) -> ApiKey:
    record = keys.find_key(db, organization_id, key_id)
    if record is None:
        raise SystemExit("API key not found in this organization.")
    return record


def print_issued(result: keys.IssuedKey) -> None:
    record = result.record
    print(f"API key {record.id} ({record.key_prefix}) scopes: {' '.join(record.scopes)}")
    print(f"Expires: {record.expires_at.isoformat() if record.expires_at else 'never'}")
    print(f"Key (shown once): {result.key}")


try:
    with Session(engine) as db, db.begin():
        if args.command == "org-create":
            organization = Organization(id=uuid4(), name=args.name.strip()[:200], api_rate_limit_per_minute=args.rate_limit,
                                        pii_retention_days=args.pii_retention_days,
                                        capture_retention_hours=args.capture_retention_hours,
                                        template_retention_hours=args.template_retention_hours)
            set_tenant(db, organization.id)
            db.add(organization)
            db.flush()
            db.add(AuditLog(organization_id=organization.id, actor_id=ACTOR, action="ORGANIZATION_CREATED",
                            request_id=request_id, reason_codes=["ORGANIZATION_CREATED"],
                            event_metadata={"rate_limit_per_minute": args.rate_limit}))
            print(f"Organization {organization.id} created: {organization.name}")
        elif args.command in ("org-suspend", "org-activate"):
            set_tenant(db, args.organization)
            organization = organization_or_exit(db, args.organization)
            organization.active = args.command == "org-activate"
            action = "ORGANIZATION_ACTIVATED" if organization.active else "ORGANIZATION_SUSPENDED"
            db.add(AuditLog(organization_id=organization.id, actor_id=ACTOR, action=action, request_id=request_id,
                            reason_codes=[action]))
            print(f"Organization {organization.id} is now {'active' if organization.active else 'suspended'}.")
        elif args.command == "key-create":
            set_tenant(db, args.organization)
            organization_or_exit(db, args.organization)
            expires = keys.now() + timedelta(days=args.expires_days) if args.expires_days else None
            print_issued(keys.issue_key(db, args.organization, args.name, args.scope or keys.DEFAULT_SCOPES, ACTOR,
                                        request_id, expires))
        elif args.command == "key-list":
            set_tenant(db, args.organization)
            organization_or_exit(db, args.organization)
            for record in db.scalars(sa.select(ApiKey).where(ApiKey.organization_id == args.organization)
                                     .order_by(ApiKey.created_at)):
                state = "active" if keys.is_usable(record) else "revoked" if record.revoked_at else "expired"
                print(f"{record.id}  {record.key_prefix}  {state:8}  {record.name}  [{' '.join(record.scopes)}]")
        elif args.command == "key-revoke":
            set_tenant(db, args.organization)
            record = keys.revoke_key(db, key_or_exit(db, args.organization, args.key_id), ACTOR, request_id)
            print(f"API key {record.id} ({record.key_prefix}) revoked.")
        else:
            set_tenant(db, args.organization)
            print_issued(keys.rotate_key(db, key_or_exit(db, args.organization, args.key_id), ACTOR, request_id,
                                         timedelta(hours=args.grace_hours)))
            print(f"The old key stops working in {args.grace_hours} hour(s).")
except (LookupError, ValueError) as error:
    raise SystemExit(str(error)) from None
finally:
    engine.dispose()
