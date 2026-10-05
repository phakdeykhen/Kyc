from collections.abc import Iterator
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from kyc.core.config import Settings


def build_engine(settings: Settings) -> sa.Engine:
    url = settings.database_url.get_secret_value()
    if url.startswith("sqlite"):
        engine = sa.create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})

        @sa.event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys = ON")
        return engine
    return sa.create_engine(url, pool_pre_ping=True, pool_size=settings.db_pool_size,
                            max_overflow=settings.db_max_overflow, pool_timeout=10,
                            connect_args={"connect_timeout": 5})


def set_tenant(db: Session, organization_id: UUID) -> None:
    if db.get_bind().dialect.name == "postgresql":
        # Transaction-local: tenant context cannot leak when a pooled connection is reused.
        db.execute(sa.text("SELECT set_config('app.organization_id', :organization_id, true)"),
                   {"organization_id": str(organization_id)})


def tenant_transaction(factory: sessionmaker, organization_id: UUID) -> Iterator[Session]:
    with factory() as db:
        with db.begin():
            set_tenant(db, organization_id)
            yield db
