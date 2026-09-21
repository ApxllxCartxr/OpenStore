"""`request-refund` is an ask, and the queue is where the ask lands.

The tool was in the closed set from hour 0 and refused as unwired, which was
honest while there was nowhere for a request to go — a refund moves money and
belongs to the Merchant. These assert the line that keeps that true: an agent
can ask, an agent cannot pay itself, and the Merchant's own action is what
closes the ask.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator

import pytest
from conftest import point_stores_at
from fastapi.testclient import TestClient
from openstore.sidecar.app import app
from openstore.sidecar.console.merchant_actions import ActionContext, configure
from openstore.sidecar.console.refunds import RefundQueue, get_refund_queue
from openstore.sidecar.console.render import TABS, page
from openstore.sidecar.console.routes import build_state, get_console_store
from openstore.sidecar.core.codes import OrderStatus, ReasonCode, RefundRequestState
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.trait.errors import TraitError
from openstore.sidecar.trait.signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    new_nonce,
    sign,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SECRET = "conformance-secret"  # noqa: S105 - a test secret, never a deployment one


@pytest.fixture(autouse=True)
async def _empty_queue(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """The process-wide queue, pointed at this test's database — the same
    single call `app.py` makes at startup."""
    point_stores_at(sessionmaker)
    yield
    point_stores_at(None)


@pytest.fixture
def queue(sessionmaker: async_sessionmaker[AsyncSession]) -> RefundQueue:
    return RefundQueue(sessionmaker=sessionmaker)


# ── The queue itself ─────────────────────────────────────────────────────────


async def test_an_agent_can_ask_about_a_paid_order(queue: RefundQueue) -> None:
    request = await queue.request(
        order_id="ord_1",
        agent_id="agent_a",
        reason="arrived damaged",
        order_status=OrderStatus.PAID,
    )

    assert request.state is RefundRequestState.REQUESTED
    assert request.open
    # No amount anywhere: what is refunded is the Merchant's decision.
    assert not hasattr(request, "amount_minor")


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.CANCELLED]
)
async def test_an_order_with_no_money_in_it_cannot_be_refunded(
    status: OrderStatus, queue: RefundQueue
) -> None:
    """Before it is paid the instrument is a cancellation, which is a different
    act with a different route and no money in it."""
    with pytest.raises(TraitError) as refusal:
        await queue.request(order_id="ord_1", agent_id="agent_a", reason="", order_status=status)
    assert refusal.value.code is ReasonCode.CANCEL_NOT_ALLOWED


async def test_asking_twice_is_one_ask(queue: RefundQueue) -> None:
    """A queue an agent can flood is the Merchant's attention spent by somebody
    else."""
    first = await queue.request(
        order_id="ord_1", agent_id="a", reason="late", order_status=OrderStatus.PAID
    )
    second = await queue.request(
        order_id="ord_1", agent_id="a", reason="still late", order_status=OrderStatus.PAID
    )

    assert second.request_id == first.request_id
    assert len(await queue.rows()) == 1


async def test_a_resolved_order_can_be_asked_about_again(queue: RefundQueue) -> None:
    """One *open* request per order, not one ever. A second damaged parcel on a
    partially refunded order is a new ask."""
    first = await queue.request(
        order_id="ord_1", agent_id="a", reason="one", order_status=OrderStatus.PAID
    )
    await queue.resolve("ord_1", RefundRequestState.DECLINED, note="outside the window")
    second = await queue.request(
        order_id="ord_1", agent_id="a", reason="two", order_status=OrderStatus.REFUNDED
    )

    assert second.request_id != first.request_id
    resolved = await queue.get(first.request_id)
    assert resolved is not None
    assert resolved.state is RefundRequestState.DECLINED


async def test_resolving_to_requested_is_refused(queue: RefundQueue) -> None:
    await queue.request(order_id="ord_1", agent_id="a", reason="", order_status=OrderStatus.PAID)
    with pytest.raises(ValueError, match="not a resolution"):
        await queue.resolve("ord_1", RefundRequestState.REQUESTED)


async def test_refunding_an_order_nobody_asked_about_is_not_an_error(queue: RefundQueue) -> None:
    """The ordinary case. It must not fail because the queue is empty."""
    assert await queue.resolve("ord_nobody", RefundRequestState.APPROVED) is None


async def test_open_asks_sort_above_resolved_ones(queue: RefundQueue) -> None:
    await queue.request(order_id="ord_1", agent_id="a", reason="", order_status=OrderStatus.PAID)
    await queue.resolve("ord_1", RefundRequestState.APPROVED)
    await queue.request(order_id="ord_2", agent_id="a", reason="", order_status=OrderStatus.PAID)

    assert [r["order_id"] for r in await queue.rows()] == ["ord_2", "ord_1"]


# ── The console shows it ─────────────────────────────────────────────────────


async def test_the_console_has_a_refunds_tab_fed_by_the_live_queue() -> None:
    await get_refund_queue().request(
        order_id="ord_seen",
        agent_id="agent_thumbprint",
        reason="arrived broken",
        order_status=OrderStatus.PAID,
    )

    assert "refunds" in dict(TABS)
    body = page(await build_state(get_console_store()), "refunds")
    assert "ord_seen" in body
    assert "arrived broken" in body
    assert "open" in body


async def test_an_empty_queue_says_so_rather_than_rendering_nothing() -> None:
    body = page(await build_state(get_console_store()), "refunds")
    assert "No agent has asked for a refund." in body


# ── The Merchant's action closes the ask ─────────────────────────────────────


@pytest.fixture
def client(ledger: Ledger, sessionmaker: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    configure(ActionContext(ledger_factory=lambda: ledger, secret=SECRET))
    with TestClient(app) as c:
        # After startup: the lifespan has just replaced every store with one
        # that has no database.
        point_stores_at(sessionmaker)
        yield c
    configure(ActionContext())


def _post(client: TestClient, path: str, payload: dict[str, object]):  # type: ignore[no-untyped-def]
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    nonce = new_nonce()
    return client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            TIMESTAMP_HEADER: str(timestamp),
            NONCE_HEADER: nonce,
            SIGNATURE_HEADER: sign(SECRET, body=body, timestamp=timestamp, nonce=nonce, path=path),
        },
    )


async def test_a_refund_closes_the_request_that_prompted_it(
    client: TestClient, ledger: Ledger
) -> None:
    await ledger.reserve("ord_r", 10000, "INR")
    await ledger.capture("ord_r", 10000, "INR")
    request = await get_refund_queue().request(
        order_id="ord_r", agent_id="a", reason="damaged", order_status=OrderStatus.PAID
    )

    response = _post(client, "/agentic/refund", {"order_id": "ord_r", "amount_minor": 4000})

    assert response.status_code == 200
    assert response.json()["closed_request_id"] == request.request_id
    # Re-read, because the request is a row now and not a live object: asserting
    # on the copy would pass even if nothing had been written back.
    closed = await get_refund_queue().get(request.request_id)
    assert closed is not None
    assert closed.state is RefundRequestState.APPROVED
    assert "4000 paise" in closed.resolution_note


async def test_a_decline_closes_the_request_and_writes_no_ledger_entry(
    client: TestClient, ledger: Ledger
) -> None:
    """Refusing to refund moves no money, and a `REVERSAL` for a refund that
    never happened would be a false entry in an append-only book."""
    request = await get_refund_queue().request(
        order_id="ord_d", agent_id="a", reason="changed mind", order_status=OrderStatus.PAID
    )

    response = _post(
        client, "/agentic/refund-decline", {"order_id": "ord_d", "reason": "outside the window"}
    )

    assert response.status_code == 200
    assert response.json()["ledger_entries_written"] == 0
    closed = await get_refund_queue().get(request.request_id)
    assert closed is not None
    assert closed.state is RefundRequestState.DECLINED
    assert closed.resolution_note == "outside the window"
    assert await ledger.entries("ord_d") == []


async def test_declining_nothing_is_refused(client: TestClient) -> None:
    response = _post(client, "/agentic/refund-decline", {"order_id": "ord_none"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ReasonCode.NOT_FOUND.value


async def test_an_unsigned_decline_is_refused(client: TestClient) -> None:
    """It closes a Merchant decision. An unsigned caller is anyone who can reach
    the container."""
    response = client.post("/agentic/refund-decline", json={"order_id": "ord_d"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ReasonCode.SIGNATURE_INVALID.value


# ── The tool, over the agent surface ─────────────────────────────────────────


@pytest.fixture
def agent_client(sessionmaker: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    """Built **before** the surface fixture on purpose: the app's lifespan
    configures a surface of its own, so a surface installed first would be
    replaced the moment the client starts."""
    with TestClient(app) as c:
        point_stores_at(sessionmaker)
        yield c


@pytest.fixture
def surface(agent_client: TestClient, trait) -> Iterator[object]:  # type: ignore[no-untyped-def]
    from openstore.sidecar.admission.oauth import Admission
    from openstore.sidecar.admission.ratelimit import RateLimiter
    from openstore.sidecar.protocols.agent_routes import AgentSurface
    from openstore.sidecar.protocols.agent_routes import configure as configure_surface

    state = AgentSurface(
        merchant_domain="spoiledduckie.localhost",
        trait=trait,
        admission=Admission(),
        limiter=RateLimiter(),
    )
    configure_surface(state)
    yield state
    configure_surface(AgentSurface())


async def test_request_refund_queues_an_ask_and_promises_nothing(
    surface,
    trait,
    agent_client: TestClient,  # type: ignore[no-untyped-def]
) -> None:
    """It used to refuse as unwired. Now it records an ask — and still moves no
    money, which is the part that must never change."""
    from openstore.sidecar.trait.models import Destination, Line

    created = await trait.orders_create(
        "cart_refund",
        [Line(sku="SD-TOTE-BLK-M", qty=1)],
        Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028"),
        {"email": "refund@spoiledduckie.test"},
        "rest-of-india",
        agent_id="agent_asker",
    )
    await trait.reserve(created.order_id, [Line(sku="SD-TOTE-BLK-M", qty=1)])
    await trait.orders_set_status(created.order_id, "confirmed", "", attempt=1)
    await trait.orders_set_status(created.order_id, "paid", "", attempt=2)

    token = surface.admission.issue_for_stranger("agent_asker")  # type: ignore[attr-defined]
    response = agent_client.post(
        "/agent/mcp",
        json={
            "tool": "request-refund",
            "input": {"order_id": created.order_id, "reason": "arrived damaged"},
        },
        headers={"authorization": f"Bearer {token.token}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["state"] == RefundRequestState.REQUESTED.value
    assert "No money has moved" in body["note"]
    queued = await get_refund_queue().open_for(created.order_id)
    assert queued is not None and queued.agent_id == "agent_asker"


async def test_request_refund_on_an_unpaid_order_is_refused(
    surface,
    trait,
    agent_client: TestClient,  # type: ignore[no-untyped-def]
) -> None:
    from openstore.sidecar.trait.models import Destination, Line

    created = await trait.orders_create(
        "cart_unpaid",
        [Line(sku="SD-TOTE-BLK-M", qty=1)],
        Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028"),
        {"email": "refund@spoiledduckie.test"},
        "rest-of-india",
        agent_id="agent_asker",
    )

    token = surface.admission.issue_for_stranger("agent_asker")  # type: ignore[attr-defined]
    response = agent_client.post(
        "/agent/mcp",
        json={"tool": "request-refund", "input": {"order_id": created.order_id}},
        headers={"authorization": f"Bearer {token.token}"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ReasonCode.CANCEL_NOT_ALLOWED.value
    assert await get_refund_queue().rows() == []
