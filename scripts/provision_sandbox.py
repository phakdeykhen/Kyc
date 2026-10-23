"""Provision synthetic integration credentials using the administrative database role.

  python scripts/provision_sandbox.py --tenant UUID --return-url https://client.example/kyc/return --output var/sandbox.json

No production capability is enabled. The output is created with mode 0600 and is never overwritten.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa
from sqlalchemy.orm import Session
from kyc.core.config import get_settings
from kyc.db.models import Application, Organization
from kyc.db.session import set_tenant
from kyc.integrations.schemas import ApplicationCreate
from kyc.integrations.service import destination
from kyc.services.tenancy import create_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--return-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if settings.migration_database_url is None:
        raise SystemExit("MIGRATION_DATABASE_URL is required.")
    body = ApplicationCreate(name="Synthetic integration sandbox", branding={"display_name": "Synthetic organization"},
        workflows={"staff-onboarding": {"country": "KH", "expected_document_type": "KH_NATIONAL_ID"}},
        return_urls=[args.return_url])
    destination(args.return_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    engine = sa.create_engine(settings.migration_database_url.get_secret_value())
    try:
        with Session(engine) as db, db.begin():
            set_tenant(db, args.tenant)
            if not db.get(Organization, args.tenant):
                raise SystemExit("Provision the tenant first with scripts/manage_tenants.py.")
            app = Application(organization_id=args.tenant, **body.model_dump(mode="json"))
            db.add(app); db.flush()
            row, token = create_key(db, args.tenant, "Synthetic backend", ["sessions:write", "sessions:read", "usage:read"],
                "sandbox-provisioner", uuid4(), expires_in_days=30, rate_limit_per_minute=120, application_id=app.id)
            result = {"tenant_id": str(args.tenant), "application_id": str(app.id), "api_key": token,
                "mode": "sandbox", "expires_at": row.expires_at.isoformat(), "workflow_id": "staff-onboarding",
                "external_subject_id": "SYNTHETIC-EMP-00123", "return_url": args.return_url}
        with os.fdopen(descriptor, "w") as output:
            descriptor = None
            output.write(json.dumps(result, indent=2) + "\n")
        print(f"Sandbox credentials written to {args.output}; results are simulations.")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        engine.dispose()


if __name__ == "__main__":
    main()
