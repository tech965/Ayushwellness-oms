"""Alembic environment configuration.

Uses the app's Settings (DATABASE_URL) and the shared declarative Base so
`alembic revision --autogenerate` picks up every model imported in
app.db.base_models (added starting Phase 1). Runs migrations with a sync
psycopg2 URL derived from the async DATABASE_URL, since Alembic's
autogenerate path is simplest against a sync connection.
"""

from __future__ import annotations

from logging.config import fileConfig

# Importing app.models populates Base.metadata with every table.
import app.models  # noqa: F401,E402
from alembic import context
from app.core.config import settings
from app.db.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url() -> str:
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _sync_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
