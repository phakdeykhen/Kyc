"""Delete expired captures and orphaned ciphertext. Schedule this (e.g. hourly).

Usage: python scripts/purge_captures.py [ORGANIZATION_ID ...]
Defaults to the local development organization.
"""

from datetime import timedelta
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy.orm import sessionmaker

from kyc.core.config import get_settings
from kyc.db.session import build_engine
from kyc.main import build_capture_store
from kyc.services.retention import purge_organization

settings = get_settings()
store = build_capture_store(settings)
if store is None:
    raise SystemExit("CAPTURE_ENCRYPTION_KEYS is not configured; nothing to purge.")
organizations = [UUID(value) for value in sys.argv[1:]] or [
    item for item in [settings.development_organization_id] if item is not None]
if not organizations:
    raise SystemExit("Pass one or more organization IDs.")
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
for organization_id in organizations:
    report = purge_organization(factory, store, organization_id,
                                webhook_retention=timedelta(days=settings.webhook_delivery_retention_days))
    print(f"{organization_id}: {report.expired_images} expired images, "
          f"{report.expired_documents} expired documents, {report.expired_selfies} expired selfies, "
          f"{report.expired_templates} expired templates, {report.expired_face_checks} expired face checks, "
          f"{report.expired_webhook_deliveries} finished webhook deliveries, {report.deleted_objects} objects deleted")
engine.dispose()
