"""Alembic's entry point, reading this deploy's own database URL.

**The URL comes from the environment, never from `alembic.ini`.** A checked-in
connection string is a checked-in password, and the one thing every deployment
of this changes is where its database is.

Async, because the engine is: `create_all` and the running app share one
`MetaData`, and a migration run through a second, synchronous driver would be a
different connection story than the one the application is tested against.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from openstore.sidecar.core.db import register_tables
from openstore.sidecar.core.tables import metadata
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Every module that declares a table, imported before autogenerate compares
# anything: a table nobody imported looks to alembic like a table to drop.
register_tables()
target_metadata = metadata


def _url() -> str:
    url = os.environ.get("SIDECAR_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "SIDECAR_DATABASE_URL is unset, so there is no database to migrate. "
            "This is the sidecar's own database — never the Merchant's."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL without connecting, for a DBA who wants to read it first."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # On, because a column whose type drifted is exactly the kind of change
        # that produces a clean migration and a broken read.
        compare_type=True,
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead. Harmless on Postgres, load-bearing for a small install
        # that runs on SQLite.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
