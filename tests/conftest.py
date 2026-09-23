"""Shared fixtures: a real async database, and a Merchant behind real HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.fake import FakeMerchant, make_app
from openstore.sidecar.trait.seed import seeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TRAIT_SECRET = "conformance-secret"  # noqa: S105 - a test secret, never a deployment one


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A real database for the stores that now need one.

    SQLite in memory, but a *real* engine with real constraints — the unique
    index is what closes the read-then-act race in the Ledger and in the refund
    queue, so a fixture that faked it would test nothing. Every durable store in
    a test is pointed at this one, exactly as `app.py` points them all at the
    deployment's.
    """
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


@pytest.fixture
async def ledger(session: AsyncSession) -> Ledger:
    return Ledger(session)


@pytest.fixture(autouse=True)
def _fresh_policy() -> Iterator[None]:
    """One live Policy per process means one Policy shared between tests.

    That is the right shape for the product — the Gate, the console and the card
    must never disagree about a limit — and it makes policy a global that a test
    editing it would otherwise leave behind. One test setting `window_open=False`
    used to end there; now it would close the agent window for every test after
    it in the file order.
    """
    from openstore.sidecar.gate.policy import Policy, set_current_policy

    set_current_policy(Policy())
    yield
    set_current_policy(Policy())


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


def point_stores_at(sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
    """Point the process-wide durable stores at a test's database.

    Needed **after** a `TestClient(app)` has started, not before: the app's
    lifespan configures every store from the environment, which in a test has no
    `SIDECAR_DATABASE_URL`, so anything wired earlier is replaced by a store
    with no database at the moment the client comes up.
    """
    from openstore.sidecar.basket import BasketStore
    from openstore.sidecar.console.refunds import configure_refunds
    from openstore.sidecar.evidence.store import configure_receipts
    from openstore.sidecar.protocols.agent_routes import get_surface

    configure_refunds(sessionmaker)
    configure_receipts(sessionmaker)
    get_surface().baskets = BasketStore(sessionmaker=sessionmaker)
