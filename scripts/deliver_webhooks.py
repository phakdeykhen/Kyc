"""Deliver due webhooks and their retries (run on a schedule, or with --loop).

  python scripts/deliver_webhooks.py [ORG ...] [--loop SECONDS]

The API delivers new events right after they commit (WEBHOOK_DELIVERY_MODE=background).
This worker sends the retries that come due later, and everything when the API runs
with WEBHOOK_DELIVERY_MODE=worker. Deliveries go out as the restricted API role, inside
each organization's RLS context. Without ORG arguments, organization IDs are listed
through MIGRATION_DATABASE_URL, which needs a role that bypasses row security.
"""

import argparse
from pathlib import Path
import sys
import time
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.session import build_engine  # noqa: E402
from kyc.main import build_field_cipher  # noqa: E402
from kyc.webhooks.delivery import HTTPSender  # noqa: E402
from kyc.webhooks.dispatcher import WebhookDispatcher  # noqa: E402
from kyc.webhooks.secrets import build_webhook_cipher  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("organizations", nargs="*", type=UUID)
parser.add_argument("--loop", type=float, metavar="SECONDS", help="keep running, pausing this long between rounds")
args = parser.parse_args()

settings = get_settings()
engine = build_engine(settings)
dispatcher = WebhookDispatcher(sessionmaker(engine, expire_on_commit=False),
                               build_webhook_cipher(settings, build_field_cipher(settings)),
                               HTTPSender(settings.webhook_timeout_seconds, settings.webhook_allow_private_targets),
                               settings.webhook_max_attempts, background=False)


def organizations() -> list[UUID]:
    if args.organizations:
        return args.organizations
    if settings.migration_database_url is None:
        raise SystemExit("Pass organization IDs, or set MIGRATION_DATABASE_URL to list them.")
    admin = sa.create_engine(settings.migration_database_url.get_secret_value())
    try:
        with admin.connect() as connection:
            return list(connection.execute(sa.text("SELECT id FROM organizations WHERE active")).scalars())
    finally:
        admin.dispose()


try:
    while True:
        sent = {str(organization): dispatcher.drain(organization) for organization in organizations()}
        print(f"Delivery round: {sum(sent.values())} attempt(s) {sent}", flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)
finally:
    engine.dispose()
