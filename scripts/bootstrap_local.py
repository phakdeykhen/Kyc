"""Apply migrations, provision the local organization, and grant minimal API rights."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.config import get_settings
from kyc.db.models import Organization
from kyc.db.session import set_tenant

root = Path(__file__).resolve().parents[1]
settings = get_settings()
url = settings.migration_database_url
if url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required for bootstrap; it is not used by the API.")
configuration = Config(str(root / "alembic.ini"))
command.upgrade(configuration, "head")
engine = sa.create_engine(url.get_secret_value(), pool_pre_ping=True)
with Session(engine) as db, db.begin():
    set_tenant(db, settings.development_organization_id)
    if not db.get(Organization, settings.development_organization_id):
        db.add(Organization(id=settings.development_organization_id, name="Local development organization"))
with engine.begin() as connection:
    connection.execute(sa.text("GRANT USAGE ON SCHEMA public TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT ON organizations, alembic_version TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, UPDATE ON kyc_sessions TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT ON audit_logs TO kyc_app"))
    # Phase 2 capture pipeline; DELETE serves recapture replacement and retention purges.
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON identity_documents, document_images TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON document_checks TO kyc_app"))
    # Phase 3 extraction writes encrypted fields and processing metadata on the document.
    connection.execute(sa.text("GRANT UPDATE ON identity_documents TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON document_fields TO kyc_app"))
    # Phase 5 MRZ engine results.
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON mrz_results TO kyc_app"))
    # Phase 7 barcode results (payload encrypted under the PII keyring).
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON barcode_results TO kyc_app"))
    # Phase 10 liveness: challenges are marked used (UPDATE); checks hold evidence only.
    connection.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON liveness_challenges TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON liveness_checks TO kyc_app"))
    # Phase 11 ePassport chip: AA challenges are marked used (UPDATE); nfc_results hold evidence only.
    connection.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON nfc_challenges TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON nfc_results TO kyc_app"))
    # Phase 12: signals are re-derived on every analysis, so the previous set is deleted and replaced.
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON fraud_signals TO kyc_app"))
    # Phase 13: assessments are an append-only decision history.
    connection.execute(sa.text("GRANT SELECT, INSERT ON risk_assessments TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT, DELETE ON selfie_captures, face_quality_checks, biometric_templates, face_comparisons TO kyc_app"))
    connection.execute(sa.text("GRANT SELECT, INSERT ON consents TO kyc_app"))
engine.dispose()
print("Migrations through phase 13 applied and local organization provisioned.")
