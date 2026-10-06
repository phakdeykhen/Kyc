"""Re-encrypt stored data under each keyring's active key, or report which key versions are in use.

  python scripts/rotate_keys.py inventory [ORGANIZATION_ID ...]
  python scripts/rotate_keys.py reseal    [ORGANIZATION_ID ...] [--only document_fields,capture_objects,...]

Rotation procedure (docs/architecture-phase17.md):
  1. generate a new key and put it FIRST in the keyring (the old one stays, second);
  2. deploy, so new data uses the new key;
  3. run `reseal`, then `inventory`: no record may remain under the old version;
  4. remove the old key from the keyring and deploy again.

Uses MIGRATION_DATABASE_URL: reviewer notes are append-only for the API role, so only the
administrative role may rewrite their ciphertext. Every organization is processed inside
its own row-level-security context. With no IDs, every organization is processed.
Local capture namespaces are included even when the organization row no longer exists.
Exit 2 means selected classes could not be verified or old versions remain in use.
"""

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.models import Organization  # noqa: E402
from kyc.main import build_biometric_cipher, build_capture_store, build_field_cipher  # noqa: E402
from kyc.services.rekey import CLASSES, Keyrings, rekey_organization, retired_versions_in_use  # noqa: E402
from kyc.webhooks.secrets import build_webhook_cipher  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("command", choices=("inventory", "reseal"))
parser.add_argument("organizations", nargs="*", type=UUID)
parser.add_argument("--only", help="comma-separated data classes: " + ", ".join(CLASSES))
args = parser.parse_args()

settings = get_settings()
if settings.migration_database_url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required to rotate keys.")
classes = [item.strip() for item in args.only.split(",")] if args.only else list(CLASSES)
unknown = set(classes) - set(CLASSES)
if unknown:
    raise SystemExit(f"Unknown data classes: {sorted(unknown)}.")
field_cipher = build_field_cipher(settings)
keys = Keyrings(field_cipher, build_biometric_cipher(settings), build_webhook_cipher(settings, field_cipher),
                build_capture_store(settings))
engine = sa.create_engine(settings.migration_database_url.get_secret_value())
factory = sessionmaker(engine, expire_on_commit=False)
try:
    organizations = args.organizations
    if not organizations:
        with factory() as db:
            organizations = list(db.scalars(sa.select(Organization.id).order_by(Organization.created_at)))
        if "capture_objects" in classes and keys.capture_store is not None:
            organizations = sorted(set(organizations) | set(keys.capture_store.list_organization_ids()))
    key_attributes = {"document_fields": "field_cipher", "barcode_payloads": "field_cipher",
                      "review_notes": "field_cipher", "webhook_secrets": "webhook_cipher",
                      "biometric_templates": "biometric_cipher", "capture_objects": "capture_store"}
    unconfigured = [name for name in classes if getattr(keys, key_attributes[name]) is None]
    summary, blocked = {}, bool(unconfigured)
    for organization_id in organizations:
        if args.command == "reseal":
            changed = rekey_organization(factory, organization_id, keys, apply=True, classes=classes)
        after = rekey_organization(factory, organization_id, keys, apply=False, classes=classes)
        remaining = {name: versions for name, versions in retired_versions_in_use(after).items() if versions}
        unverified = {name: report.skipped for name, report in after.items() if report.skipped}
        blocked = blocked or bool(remaining) or bool(unverified)
        summary[str(organization_id)] = {
            "classes": {name: (changed[name].view() | {"remaining": after[name].view()["records_by_version"]})
                        if args.command == "reseal" else after[name].view() for name in classes},
            "old_versions_still_in_use": remaining,
            "unverified_classes": unverified}
    print(json.dumps(summary, indent=2, default=str))
    if blocked:
        if unconfigured:
            print("Unconfigured keyrings prevent verification of: " + ", ".join(unconfigured), file=sys.stderr)
        print("Selected classes are unverified or still use old keys; do not remove those keys yet.", file=sys.stderr)
        sys.exit(2)
finally:
    engine.dispose()
