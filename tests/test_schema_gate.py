"""A shop taking money does not change its own schema on boot.

`create_all` on startup is right for a laptop and wrong for a deploy: a rollback
then leaves a table the old code cannot read, at the worst possible moment, with
nobody having reviewed the change. So demo creates and a real deploy migrates —
and refuses to start when it has not.
"""

from __future__ import annotations

import pytest
from openstore.sidecar.core.db import (
    SchemaNotMigrated,
    create_all,
    ensure_schema,
    head_revision,
    make_engine,
    schema_revision,
)


async def test_demo_creates_its_own_schema() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    try:
        did = await ensure_schema(engine, demo=True)
        assert "created" in did
        # And the tables are really there, not merely reported.
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT COUNT(*) FROM ledger_entries"))
    finally:
        await engine.dispose()


async def test_a_real_deploy_refuses_an_unmigrated_database() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(SchemaNotMigrated, match="alembic upgrade head"):
            await ensure_schema(engine, demo=False)
    finally:
        await engine.dispose()


async def test_a_real_deploy_refuses_a_database_at_the_wrong_revision() -> None:
    """The rollback case: the code moved, the database did not."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    try:
        await create_all(engine)
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            await conn.execute(text("INSERT INTO alembic_version VALUES ('an-older-revision')"))

        assert await schema_revision(engine) == "an-older-revision"
        with pytest.raises(SchemaNotMigrated, match="expects"):
            await ensure_schema(engine, demo=False)
    finally:
        await engine.dispose()


async def test_a_migrated_database_starts() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    head = head_revision()
    assert head, "the checked-in migrations have no head revision"
    try:
        await create_all(engine)
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            await conn.execute(text(f"INSERT INTO alembic_version VALUES ('{head}')"))

        assert await ensure_schema(engine, demo=False) == f"at {head}"
    finally:
        await engine.dispose()


def test_the_migrations_cover_every_table_the_code_declares() -> None:
    """The check that catches a table added to a module and not to a migration.

    It has already caught one: the three passkey tables were registered only by
    whichever import happened to run first, so `create_all` created them by
    luck and the first migration would have left them out entirely.
    """
    import re
    from pathlib import Path

    from openstore.sidecar.core.db import register_tables
    from openstore.sidecar.core.tables import metadata

    register_tables()
    declared = set(metadata.tables)

    created: set[str] = set()
    for migration in Path("migrations/versions").glob("*.py"):
        created |= set(
            re.findall(r"op\.create_table\(\s*[\"']([^\"']+)[\"']", migration.read_text())
        )

    missing = declared - created
    assert not missing, (
        f"these tables exist in the code and in no migration: {sorted(missing)}. "
        f"Run `alembic revision --autogenerate`."
    )
