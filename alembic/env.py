# OpenStore Alembic env (SID-3, SID-6)
# Runs migrations against the DB URL from the OpenStore config. The serve path
# calls apply_migrations() to run `alembic upgrade head` at boot (SID-3).

from __future__ import annotations

from logging.config import fileConfig
from os.path import abspath, dirname, join
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Import so SQLModel.metadata is fully populated and autogenerate sees all tables.
import openstore.models  # noqa: F401  (registers tables)


def get_env_db_url() -> str:
    import os

    return os.environ.get("OPENSTORE_DB_URL", "sqlite:///openstore.db")


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Environment override: OPENSTORE_DB_URL points alembic at the real DB.
config.set_main_option("sqlalchemy.url", get_env_db_url())

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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