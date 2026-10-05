from logging.config import fileConfig

from alembic import context
import sqlalchemy as sa

from kyc.core.config import get_settings
from kyc.db.models import Base

configuration = context.config
if configuration.config_file_name:
    fileConfig(configuration.config_file_name)
url = configuration.attributes.get("database_url")
if not url:
    settings = get_settings()
    url = (settings.migration_database_url or settings.database_url).get_secret_value()

if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = configuration.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()
