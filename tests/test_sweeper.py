"""The expiry sweep, driven against a real Merchant and a real Ledger.

`lifecycle.sweep` was already unit-tested as a pure function; what was missing
was anything that ran it. These tests assert the consequences an operator
actually cares about: an abandoned tap gives the stock back, an unapproved
checkout stops being approvable, and a late COD parcel is visible without being
cancelled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from openstore.sidecar import checkout as flow
from openstore.sidecar import sweeper
from openstore.sidecar.checkout import CheckoutContext
from openstore.sidecar.core.codes import OrderStatus, PaymentMethod, ReasonCode
from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
from openstore.sidecar.evidence.keys import Keyring
from openstore.sidecar.evidence.store import ReceiptStore
from openstore.sidecar.gate.lifecycle import COD_DELIVERY_WINDOW, PENDING_TTL
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.provider.fake import FakeProvider
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.models import Destination, Line

DEST = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "sweeper@spoiledduckie.test"}
SKU = "SD-TOTE-BLK-M"


@pytest.fixture
async def ctx(trait: TraitClient) -> AsyncIterator[CheckoutContext]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    ring = Keyring(merchant_domain="spoiledduckie.localhost")
    ring.enroll("k1")
    yield CheckoutContext(
        trait=trait,
        provider=FakeProvider(),
        keyring=ring,
        sessionmaker=make_sessionmaker(engine),
        receipts=ReceiptStore(),
        merchant_domain="spoiledduckie.localhost",
        deploy_pseudonym_key=b"sweeper-test-key",
    )
    await engine.dispose()


async def _start(ctx: CheckoutContext, method: PaymentMethod = PaymentMethod.UPI) -> flow.Pending:
    return await flow.start(
        ctx,
        cart_id=f"cart_sweep_{method.value}",
        lines=[Line(sku=SKU, qty=1)],
        destination=DEST,
        contact=CONTACT,
        fulfillment_option_id="rest-of-india",
        method=method,
        agent_id="agent_sweeper",
    )


async def _stock(trait: TraitClient) -> int:
    return (await trait.stock_read([SKU]))[SKU]


# ── Nothing happens before a deadline ────────────────────────────────────────


async def test_a_live_checkout_is_left_alone(ctx: CheckoutContext) -> None:
    await _start(ctx)
    assert await sweeper.sweep_once(ctx) == []


# ── `pending` at 24h: nothing held, so nothing returned ──────────────────────


async def test_an_unapproved_checkout_expires_and_returns_no_stock(ctx: CheckoutContext) -> None:
    assert ctx.trait is not None
    checkout = await _start(ctx)
    before = await _stock(ctx.trait)

    swept = await sweeper.sweep_once(
        ctx, now=checkout.created_at + PENDING_TTL + timedelta(minutes=1)
    )

    assert [s.reason_code for s in swept] == [ReasonCode.TIME_LIMIT_REACHED]
    assert swept[0].to_status is OrderStatus.EXPIRED
    assert swept[0].released_stock is False
    # The 24h window is the Quote's validity, not an inventory hold: nothing was
    # reserved, so nothing comes back.
    assert await _stock(ctx.trait) == before


async def test_an_expired_checkouts_approve_link_stops_opening(ctx: CheckoutContext) -> None:
    checkout = await _start(ctx)
    token = checkout.tap_token

    await sweeper.sweep_once(ctx, now=checkout.created_at + PENDING_TTL + timedelta(minutes=1))

    assert token not in ctx.by_token
    with pytest.raises(flow.CheckoutRefused) as refusal:
        await flow.tap(ctx, token)
    assert refusal.value.code is ReasonCode.NOT_FOUND


# ── `confirmed` prepaid: the hold is the whole point ─────────────────────────


async def test_an_abandoned_tap_releases_its_stock_and_its_ledger_hold(
    ctx: CheckoutContext,
) -> None:
    assert ctx.trait is not None
    before = await _stock(ctx.trait)
    checkout = await _start(ctx)
    result = await flow.tap(ctx, checkout.tap_token)
    assert not result.refused
    assert await _stock(ctx.trait) == before - 1

    assert checkout.link_expires_at is not None
    swept = await sweeper.sweep_once(ctx, now=checkout.link_expires_at + timedelta(seconds=1))

    assert [s.reason_code for s in swept] == [ReasonCode.PAYMENT_WINDOW_ELAPSED]
    assert swept[0].released_stock is True
    assert await _stock(ctx.trait) == before

    async with ctx.sessionmaker() as session:
        position = await Ledger(session).position(checkout.order_id)
    assert position.open_holds_minor == 0


async def test_the_hold_never_outlives_the_payment_link(ctx: CheckoutContext) -> None:
    """The link's own lifetime is the ceiling. A hold that outlived it would be
    stock nobody can pay for."""
    checkout = await _start(ctx)
    await flow.tap(ctx, checkout.tap_token)

    assert checkout.confirmed_at is not None and checkout.link_expires_at is not None
    assert checkout.link_expires_at - checkout.confirmed_at == timedelta(
        seconds=flow.PAYMENT_LINK_SECONDS
    )
    assert await sweeper.sweep_once(ctx, now=checkout.link_expires_at - timedelta(seconds=1)) == []


async def test_a_paid_order_is_never_swept(ctx: CheckoutContext) -> None:
    checkout = await _start(ctx)
    result = await flow.tap(ctx, checkout.tap_token)
    ctx.provider.approve(result.checkout.link_id)
    await flow.complete(ctx, result.checkout.link_id, payer_handle="demo@upi")
    assert checkout.status is OrderStatus.PAID

    far_future = datetime.now(UTC) + timedelta(days=365)
    assert await sweeper.sweep_once(ctx, now=far_future) == []


# ── COD: alert, never cancel ─────────────────────────────────────────────────


async def test_a_late_cod_parcel_alerts_and_is_not_cancelled(ctx: CheckoutContext) -> None:
    from openstore.sidecar.console.routes import get_console_store

    get_console_store().overdue_holds.clear()
    checkout = await _start(ctx, PaymentMethod.CASH_ON_DELIVERY)
    await flow.tap(ctx, checkout.tap_token)
    assert checkout.confirmed_at is not None

    swept = await sweeper.sweep_once(
        ctx, now=checkout.confirmed_at + COD_DELIVERY_WINDOW + timedelta(minutes=1)
    )

    assert [s.reason_code for s in swept] == [ReasonCode.DELIVERY_WINDOW_ELAPSED]
    assert swept[0].alerted is True
    assert swept[0].to_status is None
    # A parcel that is late is not a parcel that is lost.
    assert checkout.status is OrderStatus.CONFIRMED
    rows = get_console_store().overdue_holds
    assert [r["order_id"] for r in rows] == [checkout.order_id]


# ── The Merchant is the serializer ───────────────────────────────────────────


async def test_a_stale_copy_is_corrected_before_the_deadline_is_applied(
    ctx: CheckoutContext,
) -> None:
    """The deadline belongs to the status, so a stale status applies the wrong
    one. A `confirmed` order swept as `pending` would expire **without releasing
    the stock it is holding** — the exact failure this loop exists to prevent.
    """
    assert ctx.trait is not None
    before = await _stock(ctx.trait)
    checkout = await _start(ctx)
    await flow.tap(ctx, checkout.tap_token)  # `confirmed` at the shop, holding one

    # Dragged back: what a stale in-memory row looks like to the sweeper.
    checkout.status = OrderStatus.PENDING

    swept = await sweeper.sweep_once(
        ctx, now=checkout.created_at + PENDING_TTL + timedelta(minutes=1)
    )

    # The `confirmed` deadline, not the `pending` one — and so the hold comes back.
    assert [s.reason_code for s in swept] == [ReasonCode.PAYMENT_WINDOW_ELAPSED]
    assert swept[0].released_stock is True
    assert await _stock(ctx.trait) == before


async def test_an_order_the_merchant_already_moved_is_left_alone(ctx: CheckoutContext) -> None:
    """Cancelled at the shop while this copy still said `pending`. `cancelled`
    is terminal and outside `SWEEPABLE`, so the pass corrects itself and does
    nothing — rather than expiring an order that is already closed."""
    checkout = await _start(ctx)
    await flow.cancel(ctx, checkout.order_id, reason="consumer-walkaway")
    checkout.status = OrderStatus.PENDING

    swept = await sweeper.sweep_once(
        ctx, now=checkout.created_at + PENDING_TTL + timedelta(minutes=1)
    )

    assert swept == []
    assert checkout.status is OrderStatus.CANCELLED
