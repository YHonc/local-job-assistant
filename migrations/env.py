from alembic import context
from sqlalchemy import engine_from_config, pool
from app.database.models import Base
from app.core.config import settings
# Load import-only entities into Base.metadata for Alembic autogeneration.
from app.imports.draft_models import ImportDraft  # noqa: F401
from app.email_sync.progress_models import (  # noqa: F401
    ApplicationStatusHistory,
    EmailManualFactRequest,
    EmailProgressSuggestion,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
