"""Apply migrations, provision the local organization, and grant the API role exactly its declared privileges."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.config import get_settings
from kyc.db.models import Organization
from kyc.db.privileges import grant_statements
from kyc.db.session import set_tenant

root = Path(__file__).resolve().parents[1]
settings = get_settings()
url = settings.migration_database_url
if url is None:
    raise SystemExit("MIGRATION_DATABASE_URL is required for bootstrap; it is not used by the API.")
configuration = Config(str(root / "alembic.ini"))
command.upgrade(configuration, "head")
engine = sa.create_engine(url.get_secret_value(), pool_pre_ping=True)
if settings.development_organization_id is not None:
    with Session(engine) as db, db.begin():
        set_tenant(db, settings.development_organization_id)
        if not db.get(Organization, settings.development_organization_id):
            db.add(Organization(id=settings.development_organization_id, name="Local development organization"))
with engine.begin() as connection:
    # Phase 17: the privilege matrix in kyc/db/privileges.py is authoritative.
    # API and PUBLIC table, column and function grants are reset in one transaction.
    # scripts/security_check.py also checks inherited rights and role memberships.
    for statement in grant_statements():
        connection.execute(sa.text(statement))
engine.dispose()
print("Migrations through phase 17 applied, API role privileges reset to the declared matrix.")
