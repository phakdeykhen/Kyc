"""Delete expired captures and orphaned ciphertext. Schedule this (e.g. hourly).

Usage: python scripts/purge_captures.py [ORGANIZATION_ID ...]
Defaults to the local development organization.
"""

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
organizations = [UUID(value) for value in sys.argv[1:]] or [settings.development_organization_id]
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
for organization_id in organizations:
    report = purge_organization(factory, store, organization_id)
    print(f"{organization_id}: {report.expired_images} expired images, "
          f"{report.expired_documents} expired documents, {report.deleted_objects} objects deleted")
engine.dispose()
