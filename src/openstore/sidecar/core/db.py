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

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openstore.sidecar.ledger.entries import metadata


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


async def create_all(engine: AsyncEngine) -> None:
    """Create the sidecar's tables.

    Used by tests and first boot. A real deployment migrates with alembic — a
    schema that appears by import is a schema nobody reviewed.
    """
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
