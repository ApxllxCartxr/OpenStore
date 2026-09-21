"""Async engine and session for the sidecar's own database.

The sidecar's database is its own (SPEC §5). It holds the Ledger, Transcripts
and evidence — never Merchant truth, which lives behind the nine doors and is
re-read fresh at every Gate. There is no cross-grant: `sc_app` cannot SELECT any
`spoiledduckie` table, and `docker/postgres-init.sql` is what makes that
structural.

Async because the request path is: the Gate makes several Merchant round trips
per decision, and a synchronous session inside the same handler would serialise
the event loop behind them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openstore.sidecar.core.tables import metadata


def as_utc(value: datetime | None) -> datetime | None:
    """Read a stored timestamp back as an aware UTC one.

    SQLite hands back a **naive** datetime even from a `DateTime(timezone=True)`
    column, while Postgres hands back an aware one. Every timestamp the sidecar
    stores is a deadline that gets compared against `datetime.now(UTC)`, and
    comparing naive with aware raises `TypeError` — so the difference between
    the test database and the deployed one would surface as a crash on the
    expiry path and nowhere else. Normalized here, once, on the way out.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def rows_affected(result: Any) -> int:
    """How many rows a DML statement actually changed.

    `session.execute` is typed as returning a `Result`, which has no
    `rowcount` — a DML statement really returns a `CursorResult`, which does.
    The count is load-bearing rather than cosmetic: a conditional UPDATE that
    changed nothing is how a second spend of a single-use token is refused, so
    this is one narrow cast in one place instead of an ignore at every call.
    """
    return int(cast("CursorResult[Any]", result).rowcount)


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """SQLite for tests, Postgres for everything else.

    SQLite needs one extra thing to be honest about concurrency: foreign keys
    are off by default, and the in-memory database is per-connection unless the
    pool is told to hold a single one.
    """
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        return create_async_engine(
            url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def register_tables() -> None:
    """Import every module that declares a table.

    A `Table` registers on the shared `MetaData` as a side effect of import, so
    a module nobody imported is a table `create_all` silently does not create —
    which surfaces later as "no such table" on the first write, far from the
    cause. Listed explicitly rather than discovered by walking the package: a
    scan would also import whatever happens to be next to them.
    """
    from openstore.sidecar import (
        basket,  # noqa: F401
        checkout_store,  # noqa: F401
    )
    from openstore.sidecar.authority import tokens  # noqa: F401
    from openstore.sidecar.console import refunds  # noqa: F401
    from openstore.sidecar.evidence import store  # noqa: F401
    from openstore.sidecar.ledger import entries  # noqa: F401


async def create_all(engine: AsyncEngine) -> None:
    """Create the sidecar's tables.

    Used by tests and first boot. A real deployment migrates with alembic — a
    schema that appears by import is a schema nobody reviewed.
    """
    register_tables()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One transaction per unit of work, committed or rolled back — never left
    open. A money path that leaks a session leaks a lock with it."""
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
