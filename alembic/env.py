"""Alembic runtime config.

Liest ``DATABASE_URL`` aus dem Environment / ``.env``, damit der Wert nicht
in der alembic.ini landet.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context
from methods.models import Base

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://methods:methods@localhost:5432/methods",
)
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ARG001
    # Ignoriere Postgres-System-Schemas bei Autogenerate
    if type_ == "table" and getattr(obj, "schema", None) in {"pg_catalog", "information_schema"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
