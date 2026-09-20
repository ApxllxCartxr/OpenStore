"""The four money actions the Merchant's admin initiates over HMAC.

None of them are browser routes, and none of them let the Merchant write the
order row directly — the sidecar holds the `RESERVE`, so a local write would
strand a Ledger hold.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from openstore.sidecar.app import app
from openstore.sidecar.console.merchant_actions import ActionContext, configure
from openstore.sidecar.core.codes import LedgerKind, OrderStatus, ReasonCode
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.trait.signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    new_nonce,
    sign,
)

SECRET = "conformance-secret"  # noqa: S105


@pytest.fixture
def client(ledger: Ledger) -> Iterator[TestClient]:
    configure(
        ActionContext(
            ledger_factory=lambda: ledger,
            orders={"ord_cod": {"status": "confirmed", "total_minor": 25000}},
            secret=SECRET,
        )
    )
    with TestClient(app) as c:
        yield c
    configure(ActionContext())


def _post(client: TestClient, path: str, payload: dict[str, object]):
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


async def test_an_unsigned_request_is_refused(client: TestClient) -> None:
    """These reach real money. An unsigned caller is anyone who can reach the
    container."""
    response = client.post("/agentic/refund", json={"order_id": "x"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ReasonCode.SIGNATURE_INVALID.value


async def test_two_partial_refunds_both_write(client: TestClient, ledger: Ledger) -> None:
    await ledger.reserve("ord_1", 10000, "INR")
    await ledger.capture("ord_1", 10000, "INR")

    first = _post(
        client, "/agentic/refund", {"order_id": "ord_1", "amount_minor": 3000, "refund_id": "r1"}
    )
    second = _post(
        client, "/agentic/refund", {"order_id": "ord_1", "amount_minor": 2500, "refund_id": "r2"}
    )

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["refunded_minor"] == 5500
    assert second.json()["status"] == OrderStatus.REFUNDED.value


async def test_over_refund_is_refused(client: TestClient, ledger: Ledger) -> None:
    await ledger.reserve("ord_2", 5000, "INR")
    await ledger.capture("ord_2", 5000, "INR")
    response = _post(
        client, "/agentic/refund", {"order_id": "ord_2", "amount_minor": 5001, "refund_id": "r"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ReasonCode.AMOUNT_MISMATCH.value


async def test_collection_writes_one_capture_with_no_reserve(
    client: TestClient, ledger: Ledger
) -> None:
    assert await ledger.entries("ord_cod") == []
    response = _post(client, "/agentic/collect", {"order_id": "ord_cod"})
    assert response.status_code == 200
    assert response.json()["status"] == OrderStatus.PAID.value

    entries = await ledger.entries("ord_cod")
    assert [e.kind for e in entries] == [LedgerKind.CAPTURE]
    assert entries[0].closes_hold is False
    await ledger.assert_invariants("ord_cod", closed=True)


async def test_an_rto_writes_no_ledger_entry_at_all(client: TestClient, ledger: Ledger) -> None:
    response = _post(client, "/agentic/rto", {"order_id": "ord_rto"})
    body = response.json()
    assert body["status"] == OrderStatus.CANCELLED.value
    assert body["reason"] == "rto"
    assert body["ledger_entries_written"] == 0
    assert await ledger.entries("ord_rto") == []


async def test_shop_reject_releases_the_hold(client: TestClient, ledger: Ledger) -> None:
    await ledger.reserve("ord_3", 7000, "INR")
    response = _post(client, "/agentic/reject", {"order_id": "ord_3", "reason": "out of stock"})
    assert response.json()["released_minor"] == 7000
    await ledger.assert_invariants("ord_3", closed=True)


async def test_rejecting_a_paid_order_is_refused(client: TestClient) -> None:
    """`cancelled` is pre-money only. Past that, a refund is the instrument."""
    from openstore.sidecar.console.merchant_actions import get_context

    get_context().orders["ord_paid"] = {"status": "paid", "total_minor": 1000}
    response = _post(client, "/agentic/reject", {"order_id": "ord_paid"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ReasonCode.CANCEL_NOT_ALLOWED.value
