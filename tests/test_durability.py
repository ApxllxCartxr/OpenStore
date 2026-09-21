"""What survives a restart, and why each one had to.

Every store here held a dict until 09-21. A dict is invisible in a test that
never restarts anything, which is why all of this was green while a deploy in
the middle of a purchase lost the order the money belonged to.

**A restart is simulated the only honest way: by throwing the objects away and
building new ones over the same database.** A test that reused the store would
be asserting about the dict it is trying to stop relying on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from openstore.sidecar import checkout as flow
from openstore.sidecar.authority.passkey import CEREMONY_TTL, PasskeyRP
from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.basket import BasketStore
from openstore.sidecar.checkout_store import CheckoutStore
from openstore.sidecar.console.refunds import RefundQueue
from openstore.sidecar.core.codes import OrderStatus, PaymentMethod, RefundRequestState
from openstore.sidecar.evidence.keys import Keyring
from openstore.sidecar.evidence.store import ReceiptStore
from openstore.sidecar.gate.transcript import transcript_from_dict
from openstore.sidecar.provider.fake import FakeProvider
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.models import Destination, Line
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEST = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "durable@spoiledduckie.test"}
SKU = "SD-TOTE-BLK-M"


def _context(trait: TraitClient, maker: async_sessionmaker[AsyncSession]) -> flow.CheckoutContext:
    """A money path built from scratch over `maker` — one process's worth."""
    ring = Keyring(merchant_domain="spoiledduckie.localhost")
    ring.enroll("k1")
    return flow.CheckoutContext(
        trait=trait,
        provider=FakeProvider(),
        keyring=ring,
        sessionmaker=maker,
        tokens=TokenStore(sessionmaker=maker),
        store=CheckoutStore(sessionmaker=maker),
        receipts=ReceiptStore(sessionmaker=maker),
        merchant_domain="spoiledduckie.localhost",
        deploy_pseudonym_key=b"durability-test-key",
    )


async def _start(ctx: flow.CheckoutContext, cart_id: str) -> flow.Pending:
    return await flow.start(
        ctx,
        cart_id=cart_id,
        lines=[Line(sku=SKU, qty=1)],
        destination=DEST,
        contact=CONTACT,
        fulfillment_option_id="rest-of-india",
        method=PaymentMethod.UPI,
        agent_id="agent_durable",
    )


# ── The one that cost money ──────────────────────────────────────────────────


async def test_a_payment_that_lands_after_a_restart_still_finds_its_order(
    trait: TraitClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """**The failure durability exists for.** The Consumer taps, the process is
    replaced, and the Provider's callback arrives at a sidecar that never saw
    the checkout. It used to answer "No checkout is waiting on that payment" —
    with the money already moved, the stock still held, and the order sitting
    `confirmed` until the sweeper expired it.
    """
    before = _context(trait, sessionmaker)
    checkout = await _start(before, "cart_restart_pay")
    tapped = await flow.tap(before, checkout.tap_token)
    assert not tapped.refused
    link_id = tapped.checkout.link_id
    before.provider.approve(link_id)

    # The restart. Nothing of the old process is carried over but its database
    # and the Provider, which is the one thing that genuinely outlives it.
    after = _context(trait, sessionmaker)
    after.provider = before.provider

    settled = await flow.complete(after, link_id, payer_handle="demo@upi")

    assert settled.status is OrderStatus.PAID
    assert settled.receipt_id
    assert await after.receipts.get(settled.receipt_id) is not None


async def test_the_decision_survives_and_is_the_one_the_money_settles_against(
    trait: TraitClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """`settle` reconciles what the Provider reports against what the Gate
    decided. A Decision that did not survive would leave nothing to reconcile
    *to*, and the transcript in the receipt is the same bytes either way."""
    before = _context(trait, sessionmaker)
    checkout = await _start(before, "cart_restart_decision")
    tapped = await flow.tap(before, checkout.tap_token)
    assert tapped.decision is not None
    original = tapped.decision.transcript.to_bytes()

    after = _context(trait, sessionmaker)
    restored = await after.store.decision_for(checkout.order_id)

    assert restored is not None
    assert restored.transcript.to_bytes() == original
    assert restored.quote_bytes == tapped.decision.quote_bytes
    assert restored.cart_hash == tapped.decision.cart_hash


async def test_the_transcript_round_trip_is_byte_stable(
    trait: TraitClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The check that keeps `to_dict` and `transcript_from_dict` in step.

    These bytes are what the receipt carries and what the golden replays pin, so
    a field the round trip forgot would not be a missing field — it would be a
    different signed document.
    """
    ctx = _context(trait, sessionmaker)
    checkout = await _start(ctx, "cart_round_trip")
    tapped = await flow.tap(ctx, checkout.tap_token)
    assert tapped.decision is not None
    transcript = tapped.decision.transcript

    rebuilt = transcript_from_dict(transcript.to_dict())

    assert rebuilt.to_bytes() == transcript.to_bytes()
    assert rebuilt.authority_kind is transcript.authority_kind
    assert rebuilt.binding == transcript.binding
    assert [c.check for c in rebuilt.checks] == [c.check for c in transcript.checks]


# ── Tokens ───────────────────────────────────────────────────────────────────


async def test_an_approve_link_still_opens_after_a_restart(
    trait: TraitClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    before = _context(trait, sessionmaker)
    checkout = await _start(before, "cart_restart_token")

    after = _context(trait, sessionmaker)
    result = await flow.tap(after, checkout.tap_token)

    assert not result.refused
    assert result.checkout.order_id == checkout.order_id


async def test_a_token_spent_before_the_restart_is_still_spent(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Single-use has to outlive the process, or a restart is a free replay of
    a human's agreement."""
    from openstore.sidecar.trait.errors import TraitError

    before = TokenStore(sessionmaker=sessionmaker)
    token = await before.issue_tap("ord_1", "cart-hash", 10000)
    await before.spend_tap(token.token, cart_hash="cart-hash")

    after = TokenStore(sessionmaker=sessionmaker)
    with pytest.raises(TraitError, match="already been used"):
        await after.spend_tap(token.token, cart_hash="cart-hash")


async def test_two_concurrent_spends_of_one_link_produce_one_hold(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The conditional UPDATE, not the check above it. Both callers read an
    unspent token; only one survives the write."""
    import asyncio

    from openstore.sidecar.trait.errors import TraitError

    store = TokenStore(sessionmaker=sessionmaker)
    token = await store.issue_tap("ord_race", "cart-hash", 10000)

    results = await asyncio.gather(
        store.spend_tap(token.token, cart_hash="cart-hash"),
        store.spend_tap(token.token, cart_hash="cart-hash"),
        return_exceptions=True,
    )

    spent = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, TraitError)]
    assert len(spent) == 1, "two taps of one link would be two holds"
    assert len(refused) == 1


# ── Everything else that used to evaporate ───────────────────────────────────


async def test_a_basket_survives_the_process_that_holds_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    before = BasketStore(sessionmaker=sessionmaker)
    basket = await before.for_agent("agent_shopper")
    basket.add(SKU, 2)
    basket.destination = DEST
    basket.contact = dict(CONTACT)
    basket.fulfillment_option_id = "rest-of-india"
    await before.save(basket)

    after = BasketStore(sessionmaker=sessionmaker)
    restored = await after.for_agent("agent_shopper")

    assert restored.cart_id == basket.cart_id
    assert [(ln.sku, ln.qty) for ln in restored.lines] == [(SKU, 2)]
    assert restored.destination == DEST
    assert restored.quotable()


async def test_a_receipt_link_outlives_the_process_that_sealed_it(
    trait: TraitClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The evidence is the product. A receipt URL handed to a Consumer and
    voided by the next deploy is not evidence."""
    before = _context(trait, sessionmaker)
    checkout = await _start(before, "cart_restart_receipt")
    tapped = await flow.tap(before, checkout.tap_token)
    before.provider.approve(tapped.checkout.link_id)
    settled = await flow.complete(before, tapped.checkout.link_id, payer_handle="demo@upi")

    after = ReceiptStore(sessionmaker=sessionmaker)
    bundle = await after.get(settled.receipt_id)

    assert bundle is not None
    assert bundle.receipt_id == settled.receipt_id
    # The document that comes back is the one that was signed, byte for byte.
    from openstore.sidecar.verify.checks import ExitCode, verify

    assert verify(bundle).exit_code is ExitCode.VALID


async def test_an_ask_in_the_refund_queue_is_not_lost_on_restart(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    before = RefundQueue(sessionmaker=sessionmaker)
    asked = await before.request(
        order_id="ord_refund",
        agent_id="agent_asker",
        reason="arrived damaged",
        order_status=OrderStatus.PAID,
    )

    after = RefundQueue(sessionmaker=sessionmaker)
    still_open = await after.open_for("ord_refund")

    assert still_open is not None
    assert still_open.request_id == asked.request_id
    assert still_open.reason == "arrived damaged"
    resolved = await after.resolve("ord_refund", RefundRequestState.APPROVED, note="refunded")
    assert resolved is not None and resolved.state is RefundRequestState.APPROVED


async def test_one_open_ask_per_order_is_the_index_and_not_the_lookup(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The lookup above the insert cannot make this true: two asks racing on one
    order both read an empty queue and both decide to write. The partial unique
    index is what refuses the second, which is then answered with the first.

    Asserted against the index directly rather than by racing two coroutines:
    the test database is SQLite on a single shared connection, so a `gather`
    there proves nothing about what two workers against Postgres would do, while
    the constraint is the same object in both.
    """
    from openstore.sidecar.console.refunds import refund_requests
    from openstore.sidecar.core.db import session_scope
    from sqlalchemy.exc import IntegrityError

    queue = RefundQueue(sessionmaker=sessionmaker)
    first = await queue.request(
        order_id="ord_race", agent_id="a", reason="one", order_status=OrderStatus.PAID
    )

    # What a second worker's INSERT looks like when it has already passed the
    # "is there an open ask?" check.
    with pytest.raises(IntegrityError):
        async with session_scope(sessionmaker) as session:
            await session.execute(
                refund_requests.insert().values(
                    request_id="rfrq_second_writer",
                    order_id="ord_race",
                    agent_id="a",
                    reason="two",
                    requested_at=datetime.now(UTC),
                    state=RefundRequestState.REQUESTED.value,
                    resolved_at=None,
                    resolution_note="",
                )
            )

    # And the caller that loses is answered with the ask that exists.
    second = await queue.request(
        order_id="ord_race", agent_id="a", reason="two", order_status=OrderStatus.PAID
    )
    assert second.request_id == first.request_id
    assert len(await queue.rows()) == 1


# ── The passkey working set, which stays short-lived on purpose ──────────────


async def test_a_ceremony_half_finished_is_still_answerable_after_a_restart(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The challenge is handed to a browser, and the Consumer answers it with a
    fingerprint a second later. A restart in between used to refuse with
    `authority-stale`, which reads to the Consumer as "you took too long"."""
    before = PasskeyRP(rp_id="shop.test", origin="https://shop.test", sessionmaker=sessionmaker)
    stage, options = await before.begin(
        token="tok_restart",
        cart_hash="cart-hash",
        total_minor=10000,
        currency="INR",
        expiry_utc="2026-09-22T00:00:00Z",
    )
    assert stage == "enrollment"

    after = PasskeyRP(rp_id="shop.test", origin="https://shop.test", sessionmaker=sessionmaker)
    same = await after.options_for("tok_restart", "cart-hash")

    assert same["challenge"] == options["challenge"]


async def test_the_ceremony_working_set_is_swept_rather_than_kept(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """There are no Consumer accounts here. A credential enrolled to authorize
    one spend has no meaning after it, so durable means "for this purchase",
    not "forever"."""
    rp = PasskeyRP(rp_id="shop.test", origin="https://shop.test", sessionmaker=sessionmaker)
    await rp.begin(
        token="tok_old",
        cart_hash="cart-hash",
        total_minor=10000,
        currency="INR",
        expiry_utc="2026-09-22T00:00:00Z",
    )

    swept = await rp.forget_expired(now=datetime.now(UTC) + CEREMONY_TTL + timedelta(minutes=1))

    assert swept == 1
    from openstore.sidecar.authority.passkey import PasskeyRefused

    with pytest.raises(PasskeyRefused):
        await rp.options_for("tok_old", "cart-hash")
