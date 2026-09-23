"""Retry safety, pagination, and order events.

The three things whose absence reads as "not production grade" rather than "out
of scope": an agent could not retry safely, could not page a catalogue, and
could not stop polling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from openstore.sidecar.admission.profile import ProfileRefused
from openstore.sidecar.protocols.events import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    EventStore,
    PendingEvent,
    deliver,
    event_body,
    signing_payload,
)
from openstore.sidecar.protocols.idempotency import (
    IdempotencyConflict,
    IdempotencyStore,
    fingerprint,
)

# ── Idempotency ──────────────────────────────────────────────────────────────


async def test_the_same_key_and_the_same_request_replays_the_first_answer(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    store = IdempotencyStore(sessionmaker=sessionmaker)
    digest = fingerprint("add-line", {"sku": "SD-TOTE-BLK-M", "qty": 1})

    assert await store.replay("retry-1", "agent-a", digest) is None
    await store.remember("retry-1", "agent-a", "add-line", digest, {"lines": [{"qty": 1}]})

    again = await store.replay("retry-1", "agent-a", digest)
    assert again == {"lines": [{"qty": 1}]}


async def test_argument_order_is_not_a_different_request(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """Key order is whatever the model emitted. Two orderings are one question,
    and treating them as two would refuse a correct retry."""
    store = IdempotencyStore(sessionmaker=sessionmaker)
    one = fingerprint("add-line", {"sku": "X", "qty": 2})
    other = fingerprint("add-line", {"qty": 2, "sku": "X"})
    assert one == other

    await store.remember("k", "agent-a", "add-line", one, {"ok": True})
    assert await store.replay("k", "agent-a", other) == {"ok": True}


async def test_the_same_key_with_different_arguments_is_refused(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """Returning the first result for a second, different request would answer a
    question nobody asked — the one case where failing beats doing nothing."""
    store = IdempotencyStore(sessionmaker=sessionmaker)
    await store.remember("k", "agent-a", "add-line", fingerprint("add-line", {"qty": 1}), {"a": 1})

    with pytest.raises(IdempotencyConflict):
        await store.replay("k", "agent-a", fingerprint("add-line", {"qty": 99}))


async def test_one_agents_key_is_not_another_agents_key(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """A key scoped globally would let any agent read another's stored basket by
    guessing a string — `retry-1` is not an unusual guess."""
    store = IdempotencyStore(sessionmaker=sessionmaker)
    digest = fingerprint("add-line", {"qty": 1})
    await store.remember("retry-1", "agent-a", "add-line", digest, {"owner": "a"})

    assert await store.replay("retry-1", "agent-b", digest) is None


async def test_one_key_across_two_tools_is_a_conflict_not_a_replay(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """The tool name is inside the digest, so a key reused across tools cannot
    replay the wrong one's answer."""
    store = IdempotencyStore(sessionmaker=sessionmaker)
    await store.remember(
        "k", "agent-a", "add-line", fingerprint("add-line", {"sku": "X"}), {"from": "add"}
    )
    with pytest.raises(IdempotencyConflict):
        await store.replay("k", "agent-a", fingerprint("remove-line", {"sku": "X"}))


async def test_expired_keys_are_swept(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    from openstore.sidecar.core.db import session_scope
    from openstore.sidecar.protocols.idempotency import idempotency_keys

    store = IdempotencyStore(sessionmaker=sessionmaker)
    await store.remember("old", "agent-a", "add-line", "d", {"x": 1})
    async with session_scope(sessionmaker) as session:
        await session.execute(
            idempotency_keys.update().values(stored_at=datetime.now(UTC) - timedelta(days=2))
        )

    assert await store.sweep() == 1
    assert await store.replay("old", "agent-a", "d") is None


# ── Pagination ───────────────────────────────────────────────────────────────


class _FakeTrait:
    """A catalogue of `count` single-variant groups. Enough to page."""

    def __init__(self, count: int) -> None:
        from openstore.sidecar.trait.models import CatalogueItem, ProductGroup

        self._groups = [
            ProductGroup(id=f"g{i:03d}", slug=f"g{i:03d}", name=f"Item {i:03d}")
            for i in range(count)
        ]
        self._items = [
            CatalogueItem(
                sku=f"SKU-{i:03d}",
                group_id=f"g{i:03d}",
                name=f"Item {i:03d}",
                price_minor=1000,
                low_stock_threshold=3,
                hsn_sac="4202",
                gst_rate_bp=1800,
            )
            for i in range(count)
        ]

    async def catalog_read(self) -> Any:
        from openstore.sidecar.trait.models import Catalog

        return Catalog(groups=self._groups, items=self._items)

    async def stock_read(self, skus: list[str]) -> dict[str, int]:
        return dict.fromkeys(skus, 10)


async def test_search_pages_rather_than_returning_a_whole_catalogue() -> None:
    """A shop with fifty thousand SKUs answering every query with all of them is
    not a catalogue endpoint, it is an amplification surface on a public route."""
    from openstore.sidecar.protocols import tools

    page = await tools.search(_FakeTrait(120), "", limit=10)
    assert len(page["results"]) == 10
    assert page["total"] == 120
    assert page["next_cursor"]


async def test_paging_with_the_cursor_walks_every_row_exactly_once() -> None:
    from openstore.sidecar.protocols import tools

    trait = _FakeTrait(25)
    seen: list[str] = []
    cursor = ""
    for _ in range(10):
        page = await tools.search(trait, "", limit=10, cursor=cursor)
        seen.extend(str(r["group"]) for r in page["results"])
        cursor = str(page.get("next_cursor", ""))
        if not cursor:
            break

    assert len(seen) == 25
    assert len(set(seen)) == 25, "a row was served twice"


async def test_the_last_page_carries_no_cursor() -> None:
    """A cursor on the final page is an agent that asks one more time for
    nothing, forever, if it loops on the field's presence."""
    from openstore.sidecar.protocols import tools

    page = await tools.search(_FakeTrait(5), "", limit=10)
    assert "next_cursor" not in page
    assert len(page["results"]) == 5


async def test_a_caller_cannot_ask_for_more_than_the_shop_will_do() -> None:
    from openstore.sidecar.protocols import tools
    from openstore.sidecar.protocols.tools import SEARCH_MAX_PAGE_SIZE

    page = await tools.search(_FakeTrait(400), "", limit=100_000)
    assert len(page["results"]) == SEARCH_MAX_PAGE_SIZE


async def test_an_unknown_cursor_restarts_rather_than_truncating() -> None:
    """An agent paging a catalogue being edited under it should see too much,
    never too little — a silently short page is a product nobody finds."""
    from openstore.sidecar.protocols import tools

    page = await tools.search(_FakeTrait(30), "", limit=10, cursor="g999-gone")
    assert len(page["results"]) == 10
    assert page["results"][0]["group"] == "g000"


# ── Order events ─────────────────────────────────────────────────────────────


def _event(url: str = "https://agent.test/hook", attempts: int = 0) -> PendingEvent:
    body = event_body(
        event_id="evt_1",
        merchant_domain="spoiledduckie.localhost",
        order_id="ord_1",
        kind="order.paid",
        at="2026-09-23T12:00:00+00:00",
        receipt_id="rcpt_1",
    )
    return PendingEvent(
        event_id="evt_1",
        agent_id="agent-a",
        order_id="ord_1",
        kind="order.paid",
        body=body,
        callback_url=url,
        attempts=attempts,
    )


def test_an_event_carries_no_pii_and_no_money() -> None:
    """A webhook body is the easiest thing in the system to end up in somebody
    else's logging pipeline."""
    body = _event().body
    assert set(body) == {"event_id", "merchant", "order_id", "kind", "at", "receipt_id"}
    flat = str(body).lower()
    for leak in ("email", "phone", "line1", "postal", "total", "minor", "amount"):
        assert leak not in flat


async def test_a_failed_delivery_backs_off_and_eventually_parks(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    store = EventStore(sessionmaker=sessionmaker)
    await store.enqueue(
        event_id="evt_1",
        agent_id="agent-a",
        order_id="ord_1",
        kind="order.paid",
        body={"event_id": "evt_1"},
        callback_url="https://agent.test/hook",
    )

    state = await store.failed("evt_1", 1, "connection refused")
    assert state == "pending"
    # Backed off, so it is not due again immediately. `failed` schedules from
    # "now", so the not-due check uses a moment just before the backoff lands.
    assert await store.due(now=datetime.now(UTC) + timedelta(seconds=BACKOFF_SECONDS[0] - 5)) == []
    assert await store.due(now=datetime.now(UTC) + timedelta(seconds=BACKOFF_SECONDS[0] + 5))

    assert await store.failed("evt_1", MAX_ATTEMPTS, "still down") == "parked"
    assert await store.due(now=datetime.now(UTC) + timedelta(days=1)) == []


async def test_a_delivered_event_is_not_sent_again(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    store = EventStore(sessionmaker=sessionmaker)
    await store.enqueue(
        event_id="evt_1",
        agent_id="agent-a",
        order_id="ord_1",
        kind="order.paid",
        body={},
        callback_url="https://agent.test/hook",
    )
    assert len(await store.due()) == 1
    await store.delivered("evt_1")
    assert await store.due() == []


async def test_the_callback_url_is_rechecked_on_every_send() -> None:
    """Registered public, repointed at loopback tomorrow.

    Checking only at registration would turn this sidecar into a request
    forwarder for the rest of the queue's life — an SSRF hole with a delay fuse.
    """
    refused: list[str] = []

    class _Fetcher:
        def check_url(self, url: str) -> None:
            refused.append(url)
            raise ProfileRefused("resolves to a private address")

    with pytest.raises(ProfileRefused):
        await deliver(_event(), signature="sig", kid="k1", fetcher=_Fetcher(), client=object())

    assert refused == ["https://agent.test/hook"]


async def test_a_redirect_is_not_a_delivery() -> None:
    """A 302 is a second URL that passed none of the checks the first one did,
    and marking the event delivered would mean it went somewhere unchecked."""

    class _Fetcher:
        def check_url(self, url: str) -> None:
            return None

    class _Response:
        status_code = 302

    class _Client:
        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            return _Response()

    with pytest.raises(RuntimeError):
        await deliver(_event(), signature="s", kid="k1", fetcher=_Fetcher(), client=_Client())


async def test_the_signature_verifies_against_the_shops_published_keys() -> None:
    """The agent pinned this JWKS at registration, so verifying an event needs
    no second trust root and no shared secret."""
    from openstore.sidecar.evidence.keys import Keyring, sign, verify_signature

    ring = Keyring("spoiledduckie.localhost")
    ring.enroll("k1")
    body = _event().body
    signature = sign(ring.current, signing_payload(body))

    jwk = next(k for k in ring.jwks()["keys"] if k["kid"] == "k1")
    assert verify_signature(jwk, signing_payload(body), signature)
    tampered = {**body, "order_id": "ord_somebody_elses"}
    assert not verify_signature(jwk, signing_payload(tampered), signature)


# ── The wiring, end to end ───────────────────────────────────────────────────


async def test_a_retried_call_over_the_real_surface_replays_rather_than_repeats(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """The unit tests above prove the store. This proves the wiring — that
    `params.idempotency_key` reaches it at all, which is the half that a
    correct store cannot save you from getting wrong.
    """
    from fastapi.testclient import TestClient
    from openstore.sidecar.app import app
    from openstore.sidecar.basket import BasketStore
    from openstore.sidecar.protocols.agent_routes import AgentSurface, get_surface
    from openstore.sidecar.protocols.agent_routes import configure as configure_surface

    surface = AgentSurface(
        trait=_FakeTrait(3),
        idempotency=IdempotencyStore(sessionmaker=sessionmaker),
        baskets=BasketStore(sessionmaker=sessionmaker),
    )
    configure_surface(surface)
    try:
        with TestClient(app) as client:
            # Re-point after startup: lifespan rebuilds the surface from the
            # environment, and a test has no database in it.
            configure_surface(surface)
            token = get_surface().admission.issue_for_stranger("agent_r")
            head = {"authorization": f"Bearer {token.token}"}

            def call(key: str, qty: int) -> dict[str, Any]:
                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "add-line",
                        "arguments": {"sku": "SKU-000", "qty": qty},
                        "idempotency_key": key,
                    },
                }
                return dict(client.post("/agent/mcp", json=body, headers=head).json())

            first = call("retry-1", 1)
            structured = first["result"]["structuredContent"]
            assert structured["lines"][0]["qty"] == 1
            assert "replayed" not in structured

            # The retry does NOT add a second line, and says it was a replay.
            again = call("retry-1", 1)
            replayed = again["result"]["structuredContent"]
            assert replayed["replayed"] is True
            assert replayed["lines"][0]["qty"] == 1

            # The same key with a different quantity is refused, not answered.
            clash = call("retry-1", 5)
            error = clash["result"]["structuredContent"]["error"]
            assert error["code"] == "idempotency-conflict"
            # And it carries the request id, so the refusal is traceable.
            assert error["request_id"].startswith("req_")
    finally:
        configure_surface(AgentSurface())


async def test_a_read_is_never_stored_against_a_key(
    sessionmaker,
) -> None:  # type: ignore[no-untyped-def]
    """Storing `search` would turn the key table into a catalogue cache with no
    invalidation: an agent retrying a search would be served yesterday's stock."""
    from fastapi.testclient import TestClient
    from openstore.sidecar.app import app
    from openstore.sidecar.basket import BasketStore
    from openstore.sidecar.protocols.agent_routes import AgentSurface, get_surface
    from openstore.sidecar.protocols.agent_routes import configure as configure_surface

    surface = AgentSurface(
        trait=_FakeTrait(3),
        idempotency=IdempotencyStore(sessionmaker=sessionmaker),
        baskets=BasketStore(sessionmaker=sessionmaker),
    )
    configure_surface(surface)
    try:
        with TestClient(app) as client:
            configure_surface(surface)
            token = get_surface().admission.issue_for_stranger("agent_s")
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": ""},
                    "idempotency_key": "k",
                },
            }
            head = {"authorization": f"Bearer {token.token}"}
            client.post("/agent/mcp", json=body, headers=head)
            again = client.post("/agent/mcp", json=body, headers=head).json()
            assert "replayed" not in again["result"]["structuredContent"]
    finally:
        configure_surface(AgentSurface())
