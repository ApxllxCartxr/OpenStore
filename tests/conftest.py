"""Shared fixtures: a real async database, and a Merchant behind real HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.fake import FakeMerchant, make_app
from openstore.sidecar.trait.seed import seeded
from sqlalchemy.ext.asyncio import AsyncSession

TRAIT_SECRET = "conformance-secret"  # noqa: S105 - a test secret, never a deployment one


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """SQLite in memory, but a *real* engine with a real unique constraint —
    the constraint is what closes the read-then-act race, so a fixture that
    faked it would test nothing."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    maker = make_sessionmaker(engine)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def ledger(session: AsyncSession) -> Ledger:
    return Ledger(session)


@pytest.fixture
def merchant() -> FakeMerchant:
    return seeded()


@pytest.fixture
async def trait(merchant: FakeMerchant) -> AsyncIterator[TraitClient]:
    transport = httpx.ASGITransport(app=make_app(merchant, TRAIT_SECRET))
    async with TraitClient(
        "http://merchant.internal",
        TRAIT_SECRET,
        client=httpx.AsyncClient(transport=transport, base_url="http://merchant.internal"),
    ) as client:
        yield client


@pytest.fixture
async def drifting_trait() -> AsyncIterator[TraitClient]:
    """A Merchant whose price moves between calls, so `quote-fresh` has
    something real to catch rather than a mock that agrees to disagree."""
    merchant = seeded(quote_drift_paise=500)
    transport = httpx.ASGITransport(app=make_app(merchant, TRAIT_SECRET))
    async with TraitClient(
        "http://merchant.internal",
        TRAIT_SECRET,
        client=httpx.AsyncClient(transport=transport, base_url="http://merchant.internal"),
    ) as client:
        yield client
