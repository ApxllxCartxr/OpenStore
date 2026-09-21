"""The Razorpay adapter, against a mock that speaks Razorpay's real envelopes.

**This is not a claim that it works against Razorpay.** No money has moved
through this adapter; what is asserted here is that it sends what Razorpay's API
reference documents and reads what Razorpay's documented responses contain. The
envelopes below are copied from those examples, field for field, because an
adapter tested against envelopes we invented would agree with us and nobody
else.

The cases that matter are the ones where a plausible implementation is wrong:
a `partially_paid` link read as settled, a refund issued against a link id
instead of a payment id, an `expire_by` Razorpay refuses, and a duplicate
`reference_id` treated as an error rather than as the recovery it is.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from openstore.sidecar.core.settings import BootRefused
from openstore.sidecar.provider.razorpay import (
    MIN_EXPIRY_SECONDS,
    RazorpayProvider,
    RazorpayRefused,
)

TEST_KEY = "rzp_test_abcdef123456"


def _link_body(**over: Any) -> dict[str, Any]:
    """A payment link as Razorpay's own create/fetch example returns one."""
    body = {
        "amount": 259700,
        "amount_paid": 0,
        "cancelled_at": 0,
        "created_at": 1591097057,
        "currency": "INR",
        "description": "Order ord_1",
        "expire_by": int(time.time()) + 960,
        "expired_at": 0,
        "id": "plink_ExjpAUN3gVHrPJ",
        "payments": None,
        "reference_id": "ord_1",
        "short_url": "https://rzp.io/i/nxrHnLJ",
        "status": "created",
        "updated_at": 1591097057,
    }
    body.update(over)
    return body


def _paid_body(amount_paid: int = 259700, status: str = "paid") -> dict[str, Any]:
    return _link_body(
        status=status,
        amount_paid=amount_paid,
        payments=[
            {
                "payment_id": "pay_29QQoUBi66xm2f",
                "plink_id": "plink_ExjpAUN3gVHrPJ",
                "amount": amount_paid,
                "method": "upi",
                "status": "captured",
                "created_at": 1661852741,
            }
        ],
    )


class _Razorpay:
    """A mock that records what it was asked and answers as Razorpay does."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: list[tuple[int, dict[str, Any]]] = []

    def queue(self, status: int, body: dict[str, Any]) -> None:
        self.responses.append((status, body))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected call to {request.url}")
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body)

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) if r.content else {} for r in self.requests]


@pytest.fixture
def rail() -> _Razorpay:
    return _Razorpay()


@pytest.fixture
def provider(rail: _Razorpay) -> RazorpayProvider:
    return RazorpayProvider(
        key_id=TEST_KEY,
        key_secret="secret",
        demo_mode=True,
        client=httpx.AsyncClient(transport=httpx.MockTransport(rail.handler)),
    )


# ── make_link ────────────────────────────────────────────────────────────────


async def test_make_link_sends_paise_and_the_order_as_its_reference(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    rail.queue(200, _link_body())

    link = await provider.make_link("ord_1", 259700, "INR", expires_in_seconds=900)

    sent = rail.sent[0]
    assert sent["amount"] == 259700, "Razorpay wants the smallest unit; paise are already it"
    assert sent["currency"] == "INR"
    assert sent["reference_id"] == "ord_1"
    assert link.link_id == "plink_ExjpAUN3gVHrPJ"
    assert link.url.startswith("https://rzp.io/")


async def test_no_consumer_pii_crosses_the_seam(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """The sidecar holds commitments to the Destination and Contact Point that
    become unopenable on erasure. A copy in a Provider's dashboard is a copy
    erasure cannot reach."""
    rail.queue(200, _link_body())

    await provider.make_link("ord_1", 259700, "INR")

    sent = rail.sent[0]
    assert "customer" not in sent
    assert sent["notify"] == {"sms": False, "email": False}
    assert sent["reminder_enable"] is False


async def test_the_expiry_clears_razorpays_own_minimum(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """§16.7's payment window is exactly 15 minutes and Razorpay refuses an
    `expire_by` that is not **more than** 15 minutes out. Taken literally, every
    link this sidecar asked for would be refused at creation."""
    rail.queue(200, _link_body())

    await provider.make_link("ord_1", 100, "INR", expires_in_seconds=900)

    asked_for = rail.sent[0]["expire_by"] - int(time.time())
    assert (
        asked_for >= MIN_EXPIRY_SECONDS
    ), f"asked Razorpay for {asked_for}s, which it refuses as not more than 15 minutes"


async def test_a_duplicate_reference_adopts_the_existing_link(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """A crash between `make_link` and storing its id is recovered by asking
    what happened — never by creating a second link, which is two ways for one
    Consumer to pay for one order."""
    rail.queue(
        400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "reference id already exists"}}
    )
    rail.queue(200, {"payment_links": [_link_body()]})

    link = await provider.make_link("ord_1", 259700, "INR")

    assert link.link_id == "plink_ExjpAUN3gVHrPJ"
    assert "reference_id=ord_1" in str(rail.requests[1].url)


async def test_a_refusal_with_no_existing_link_is_raised(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """Adoption is for the duplicate case only. Every other refusal must reach
    the caller with Razorpay's own words."""
    rail.queue(400, {"error": {"description": "amount must be at least 100"}})
    rail.queue(200, {"payment_links": []})

    with pytest.raises(RazorpayRefused, match="amount must be at least 100"):
        await provider.make_link("ord_1", 1, "INR")


# ── check_status ─────────────────────────────────────────────────────────────


async def test_a_paid_link_reports_what_arrived_and_the_payment_to_refund(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    rail.queue(200, _paid_body())

    status = await provider.check_status("plink_ExjpAUN3gVHrPJ")

    assert status.paid is True
    assert status.amount_minor == 259700
    assert status.order_id == "ord_1"
    assert (
        status.reference == "pay_29QQoUBi66xm2f"
    ), "without the payment id a paid order cannot be refunded at all"


async def test_a_partially_paid_link_is_not_settled(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """The one a plausible implementation gets wrong. Settling against it would
    capture a hold the Consumer has not covered."""
    rail.queue(200, _paid_body(amount_paid=100000, status="partially_paid"))

    status = await provider.check_status("plink_ExjpAUN3gVHrPJ")

    assert status.paid is False
    assert status.amount_minor == 100000, "what arrived, not what was asked for"


async def test_an_unpaid_link_reports_zero_not_the_amount_asked_for(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """Reconciling against the ask would make every underpayment look like a
    settlement."""
    rail.queue(200, _link_body())

    status = await provider.check_status("plink_ExjpAUN3gVHrPJ")

    assert status.paid is False
    assert status.amount_minor == 0


async def test_cancelled_and_expired_links_report_cancelled(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    for state in ("cancelled", "expired"):
        rail.queue(200, _link_body(status=state))
        status = await provider.check_status("plink_ExjpAUN3gVHrPJ")
        assert status.cancelled is True, state
        assert status.paid is False, state


# ── cancel ───────────────────────────────────────────────────────────────────


async def test_cancelling_an_already_cancelled_link_is_not_a_failure(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    rail.queue(400, {"error": {"description": "payment link is already cancelled"}})
    rail.queue(200, _link_body(status="cancelled"))

    await provider.cancel("plink_ExjpAUN3gVHrPJ")


async def test_cancelling_a_paid_link_is_confirmed_rather_than_assumed(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """Swallowing the refusal without checking would be this adapter deciding
    money did not arrive."""
    rail.queue(400, {"error": {"description": "payment link is already paid"}})
    rail.queue(200, _paid_body())

    await provider.cancel("plink_ExjpAUN3gVHrPJ")

    assert "/payment_links/plink_ExjpAUN3gVHrPJ" in str(rail.requests[1].url)


async def test_a_cancel_refused_for_another_reason_is_raised(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    rail.queue(401, {"error": {"description": "authentication failed"}})
    rail.queue(200, _link_body(status="created"))

    with pytest.raises(RazorpayRefused, match="authentication failed"):
        await provider.cancel("plink_ExjpAUN3gVHrPJ")


# ── refund ───────────────────────────────────────────────────────────────────


async def test_a_refund_is_issued_against_the_payment_and_keyed_by_its_own_id(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """Razorpay's `receipt` is its idempotency key, so a retry of one partial
    refund returns the first rather than issuing a second — the same property
    the Ledger key has, enforced on both sides of the seam."""
    rail.queue(
        200,
        {
            "id": "rfnd_FP8QHiV938haTz",
            "entity": "refund",
            "amount": 40000,
            "payment_id": "pay_29QQoUBi66xm2f",
            "status": "processed",
            "currency": "INR",
            "receipt": "rf_1",
        },
    )

    result = await provider.refund("pay_29QQoUBi66xm2f", 40000, "INR", refund_id="rf_1")

    assert "/payments/pay_29QQoUBi66xm2f/refund" in str(rail.requests[0].url)
    assert rail.sent[0]["amount"] == 40000
    assert rail.sent[0]["receipt"] == "rf_1"
    assert result.refund_id == "rfnd_FP8QHiV938haTz"
    assert result.amount_minor == 40000


async def test_a_refund_with_no_payment_reference_refuses_before_the_network(
    provider: RazorpayProvider, rail: _Razorpay
) -> None:
    """A refund is issued against a payment. An order with none recorded cannot
    be refunded, and saying so here is better than a 404 from Razorpay."""
    with pytest.raises(RazorpayRefused, match="no payment reference"):
        await provider.refund("", 40000, "INR", refund_id="rf_1")

    assert rail.requests == [], "nothing should have been sent"


# ── the webhook, and the boot refusal ────────────────────────────────────────


async def test_the_webhook_yields_only_the_two_identifiers() -> None:
    """The body is a trigger, never an instruction: parsing a verdict out of it
    would make a forged-but-signed replay authoritative."""
    provider = RazorpayProvider(key_id=TEST_KEY, key_secret="s", demo_mode=True)

    event = provider.read_webhook(
        {
            "id": "evt_Jl2yTDtyfxHkpX",
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_ExjpAUN3gVHrPJ",
                        "status": "paid",
                        "customer": {"contact": "+919000000001"},
                    }
                }
            },
        }
    )

    assert event.event_id == "evt_Jl2yTDtyfxHkpX"
    assert event.link_id == "plink_ExjpAUN3gVHrPJ"
    assert event.payer_handle == "+919000000001"


def test_a_live_key_in_demo_mode_refuses_to_boot() -> None:
    with pytest.raises(BootRefused, match="not a rzp_test_"):
        RazorpayProvider(key_id="rzp_live_realmoney", key_secret="s", demo_mode=True)


def test_it_declares_upi_and_nothing_it_cannot_do() -> None:
    from openstore.sidecar.core.codes import PaymentMethod

    provider = RazorpayProvider(key_id=TEST_KEY, key_secret="s", demo_mode=True)

    assert provider.declared_methods() == frozenset({PaymentMethod.UPI})
    assert provider.supports(PaymentMethod.UPI)
    assert not provider.supports(PaymentMethod.CASH_ON_DELIVERY)
