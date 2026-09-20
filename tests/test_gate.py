"""The Gate: twelve checks in fixed order, byte-stable Transcript, no side effects.

Everything runs against the conformance fake over real HTTP, so the Merchant the
Gate re-reads is a Merchant, not a stub that agrees with it.
"""

from __future__ import annotations

import asyncio

import pytest
from openstore.sidecar.core.codes import (
    GATE_CHECK_ORDER,
    AuthorityKind,
    CheckResult,
    GateCheck,
    IntentMechanism,
    LedgerKind,
    OrderStatus,
    PaymentMethod,
    ReasonCode,
)
from openstore.sidecar.gate.decide import (
    Authority,
    DecisionInput,
    Gate,
    GateRefused,
)
from openstore.sidecar.gate.policy import Policy
from openstore.sidecar.gate.settle import ProviderRecord, collect, rto, settle
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.errors import TraitError
from openstore.sidecar.trait.fake import FakeMerchant
from openstore.sidecar.trait.models import Destination, Line
from openstore.sidecar.trait.seed import SEED_ITEMS

DEST_A = Destination(
    line1="4th Cross, Indiranagar", city="Bengaluru", state="KA", postal_code="560038"
)
DEST_B = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "demo@spoiledduckie.test", "phone": "+919000000001"}
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")

GROUP_OF = {sku: group for sku, group, *_ in SEED_ITEMS}
TAGS_OF = {sku: row[-1] for sku, *row in [(i[0], *i[1:]) for i in SEED_ITEMS]}
PRICE_OF = {i[0]: i[3] for i in SEED_ITEMS}


def _request(
    lines: list[Line],
    *,
    order_id: str = "ord_gate",
    destination: Destination = DEST_A,
    authority: Authority | None = None,
    method: PaymentMethod = PaymentMethod.UPI,
    fulfillment: str = "karnataka",
    discount_code: str | None = None,
) -> DecisionInput:
    return DecisionInput(
        order_id=order_id,
        cart_id="cart_gate",
        lines=lines,
        destination=destination,
        contact=CONTACT,
        fulfillment_option_id=fulfillment,
        order_salt=SALT,
        expiry_utc="2026-09-21T12:00:00Z",
        merchant_domain="spoiledduckie.localhost",
        agent_id="agent_test",
        consumer_id="consumer_test",
        authority=authority or Authority(kind=AuthorityKind.UPI_PIN),
        method=method,
        group_of=GROUP_OF,
        tags_of=TAGS_OF,
        attested_prices=PRICE_OF,
        discount_code=discount_code,
    )


@pytest.fixture
def gate(trait: TraitClient) -> Gate:
    return Gate(trait, Policy())


# ── Order and shape ──────────────────────────────────────────────────────────


async def test_all_twelve_checks_run_in_the_fixed_order(gate: Gate) -> None:
    """SPEC §4 froze this order and the Transcript's bytes depend on it, so it
    is not reordered for tidiness."""
    decision = await gate.decide(_request([Line(sku="SD-STICKERS", qty=1)]))
    assert [c.check for c in decision.transcript.checks] == list(GATE_CHECK_ORDER)
    assert len(GATE_CHECK_ORDER) == 12


async def test_the_transcript_is_byte_stable(gate: Gate) -> None:
    """Two identical decisions produce identical bytes. Nothing clock-derived
    goes in beyond the expiry the Consumer was shown — a 'decided at' timestamp
    would make the golden replays impossible."""
    a = await gate.decide(_request([Line(sku="SD-STICKERS", qty=1)]))
    b = await gate.decide(_request([Line(sku="SD-STICKERS", qty=1)]))
    assert a.transcript.to_bytes() == b.transcript.to_bytes()


async def test_a_dry_run_has_no_side_effects(gate: Gate, merchant: FakeMerchant) -> None:
    before = dict(merchant.stock)
    decision = await gate.decide(_request([Line(sku="SD-STICKERS", qty=2)]), dry_run=True)
    assert decision.permitted is False
    assert merchant.stock == before
    assert merchant.orders == {}
    # A dry run checks everything the real one does; it just may not be acted on.
    assert [c.check for c in decision.transcript.checks] == list(GATE_CHECK_ORDER)


async def test_the_first_failure_is_the_reason_code(gate: Gate) -> None:
    """All failures are recorded; `reason_code` is the first, and the Gate stops
    there rather than collecting a list nobody can act on."""
    with pytest.raises(GateRefused) as exc:
        await gate.decide(_request([Line(sku="SD-RECALLED", qty=1)]))
    assert exc.value.reason_code is ReasonCode.BLOCKED_ITEM
    transcript = exc.value.transcript
    assert transcript.checks[-1].check is GateCheck.BLOCKED
    assert transcript.reason_code is ReasonCode.BLOCKED_ITEM
    # And it stopped: checks after `blocked` never ran.
    assert GateCheck.CAPS not in [c.check for c in transcript.checks]


# ── Check 1: Authority ───────────────────────────────────────────────────────


async def test_upi_pin_defers_rather_than_passing(gate: Gate) -> None:
    """The payer authenticates in their own PSP app, so the Authority arrives
    with the money. Recording it as `pass` here would put a false statement into
    signed evidence."""
    decision = await gate.decide(_request([Line(sku="SD-STICKERS", qty=1)]))
    first = decision.transcript.checks[0]
    assert first.check is GateCheck.AUTHORITY_PRESENT_AND_ACCEPTED
    assert first.result is CheckResult.DEFERRED


async def test_passkey_must_be_present_before_the_gate(gate: Gate) -> None:
    with pytest.raises(GateRefused) as exc:
        await gate.decide(
            _request(
                [Line(sku="SD-STICKERS", qty=1)],
                authority=Authority(kind=AuthorityKind.PASSKEY, present=False),
            )
        )
    assert exc.value.reason_code is ReasonCode.AUTHORITY_MISSING


async def test_a_passkey_bound_to_a_different_cart_is_stale(gate: Gate) -> None:
    """Anything shown on the approve page is covered, so a change after render
    invalidates the token — a Destination edit that does not move the total
    cannot redirect a parcel someone already paid for."""
    with pytest.raises(GateRefused) as exc:
        await gate.decide(
            _request(
                [Line(sku="SD-STICKERS", qty=1)],
                authority=Authority(
                    kind=AuthorityKind.PASSKEY, present=True, bound_cart_hash="something-else"
                ),
            )
        )
    assert exc.value.reason_code is ReasonCode.AUTHORITY_STALE


async def test_a_passkey_bound_to_this_cart_passes(gate: Gate) -> None:
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    probe = await gate.decide(request, dry_run=True)

    bound = _request(
        [Line(sku="SD-STICKERS", qty=1)],
        authority=Authority(
            kind=AuthorityKind.PASSKEY,
            present=True,
            bound_cart_hash=probe.cart_hash,
            ceremony="assertion",
        ),
    )
    decision = await gate.decide(bound)
    assert decision.transcript.checks[0].result is CheckResult.PASS
    assert decision.transcript.ceremony == "assertion"


async def test_mandate_is_refused(gate: Gate) -> None:
    """Defined, registered, and refused in v1 (ADR-0017)."""
    policy = Policy(enabled_authority_kinds=frozenset({AuthorityKind.MANDATE}))
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(  # noqa: SLF001
            _request(
                [Line(sku="SD-STICKERS", qty=1)],
                authority=Authority(kind=AuthorityKind.MANDATE, present=True),
            )
        )
    assert exc.value.reason_code is ReasonCode.AUTHORITY_KIND_NOT_ENABLED


async def test_a_kind_the_merchant_did_not_enable_is_refused(gate: Gate) -> None:
    policy = Policy(enabled_authority_kinds=frozenset({AuthorityKind.PASSKEY}))
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(_request([Line(sku="SD-STICKERS", qty=1)]))  # noqa: SLF001
    assert exc.value.reason_code is ReasonCode.AUTHORITY_KIND_NOT_ENABLED
    assert "passkey" in exc.value.detail


async def test_an_otp_is_not_a_confirmed_intent_mechanism(gate: Gate) -> None:
    """Closed at two. An OTP to a Contact Point binds no funding instrument and
    never becomes a member."""
    with pytest.raises(GateRefused) as exc:
        await gate.decide(
            _request(
                [Line(sku="SD-STICKERS", qty=1)],
                authority=Authority(kind=AuthorityKind.CONFIRMED_INTENT, present=True),
            )
        )
    assert exc.value.reason_code is ReasonCode.INTENT_MECHANISM_NOT_ENABLED


# ── Checks 4–9: policy ───────────────────────────────────────────────────────


async def test_a_per_group_cap_cannot_be_walked_by_taking_one_of_each_colour(
    gate: Gate,
) -> None:
    """Caps evaluate at the Product Group. Two colours of one limited item is
    still two of that item."""
    policy = Policy(per_group_qty_overrides={"tote": 2})
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(  # noqa: SLF001
            _request(
                [
                    Line(sku="SD-TOTE-BLK-M", qty=1),
                    Line(sku="SD-TOTE-RED-M", qty=1),
                    Line(sku="SD-TOTE-RED-L", qty=1),
                ]
            )
        )
    assert exc.value.reason_code is ReasonCode.QTY_EXCEEDED
    assert "tote" in exc.value.detail


async def test_the_per_order_cap_refuses(gate: Gate) -> None:
    policy = Policy(per_order_cap_minor=1000)
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(_request([Line(sku="SD-STICKERS", qty=1)]))  # noqa: SLF001
    assert exc.value.reason_code is ReasonCode.CAP_EXCEEDED


async def test_a_closed_window_refuses_plainly(gate: Gate) -> None:
    """Not 'sold out'. A Merchant who has closed the agent window is not out of
    stock, and telling a Consumer otherwise sends them away for the wrong reason."""
    policy = Policy(window_open=False)
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(_request([Line(sku="SD-STICKERS", qty=1)]))  # noqa: SLF001
    assert exc.value.reason_code is ReasonCode.WINDOW_CLOSED


async def test_a_line_count_cap_counts_groups(gate: Gate) -> None:
    policy = Policy(per_order_line_count=1)
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(  # noqa: SLF001
            _request([Line(sku="SD-STICKERS", qty=1), Line(sku="SD-KEYCHAIN", qty=1)])
        )
    assert exc.value.reason_code is ReasonCode.COUNT_EXCEEDED


# ── Check 10: quote-consistent ───────────────────────────────────────────────


async def test_a_merchant_whose_sums_do_not_add_up_is_refused(gate: Gate) -> None:
    """Checking a Merchant's arithmetic is not computing prices. Without it a
    buggy or compromised Merchant gets its total signed unchallenged."""
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    honest = await gate.decide(request, dry_run=True)

    tampered = honest.quote.model_copy(update={"total_minor": honest.quote.total_minor + 100})
    request.pinned_quote = tampered
    request.pinned_quote_bytes = honest.quote_bytes

    with pytest.raises(GateRefused) as exc:
        await gate.decide(request)
    assert exc.value.reason_code is ReasonCode.QUOTE_INCONSISTENT


async def test_a_tampered_line_total_is_refused(gate: Gate) -> None:
    """The fold identity: qty x unit + add-ons must equal the line total. A
    Merchant that inflates one line while keeping the subtotal is caught here."""
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    honest = await gate.decide(request, dry_run=True)

    line = honest.quote.lines[0]
    bad_line = line.model_copy(update={"line_total_minor": line.line_total_minor + 50})
    request.pinned_quote = honest.quote.model_copy(
        update={
            "lines": [bad_line],
            "subtotal_minor": honest.quote.subtotal_minor + 50,
            "total_minor": honest.quote.total_minor + 50,
        }
    )
    request.pinned_quote_bytes = honest.quote_bytes

    with pytest.raises(GateRefused) as exc:
        await gate.decide(request)
    assert exc.value.reason_code is ReasonCode.QUOTE_INCONSISTENT
    assert "folds to" in exc.value.detail


async def test_the_gate_checks_the_quote_against_the_pinned_attestation(gate: Gate) -> None:
    """What the Merchant signed per item is what the Quote may charge. A later
    catalogue edit cannot rewrite what was bought."""
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    request.attested_prices = {"SD-STICKERS": 12345}
    with pytest.raises(GateRefused) as exc:
        await gate.decide(request)
    assert exc.value.reason_code is ReasonCode.QUOTE_INCONSISTENT
    assert "attested" in exc.value.detail


# ── Check 11: quote-fresh ────────────────────────────────────────────────────


async def test_a_price_that_moves_between_pin_and_capture_fails_and_moves_no_money(
    drifting_trait: TraitClient, ledger: Ledger
) -> None:
    """The Consumer re-taps the new number. Nothing is coerced and no money
    moves."""
    gate = Gate(drifting_trait, Policy())
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    pinned = await gate.decide(request, dry_run=True)

    request.pinned_quote = pinned.quote
    request.pinned_quote_bytes = pinned.quote_bytes
    with pytest.raises(GateRefused) as exc:
        await gate.decide(request)

    assert exc.value.reason_code is ReasonCode.PRICE_CHANGED
    assert "SD-STICKERS" in exc.value.detail or "total" in exc.value.detail
    assert await ledger.entries(request.order_id) == [], "a refused decision moved money"


# ── Check 12: method-enabled ─────────────────────────────────────────────────


async def test_a_disabled_method_refuses_naming_what_is_enabled(gate: Gate) -> None:
    policy = Policy(enabled_methods=frozenset({PaymentMethod.UPI}))
    with pytest.raises(GateRefused) as exc:
        await Gate(gate._trait, policy).decide(  # noqa: SLF001
            _request([Line(sku="SD-STICKERS", qty=1)], method=PaymentMethod.CARD)
        )
    assert exc.value.reason_code is ReasonCode.METHOD_NOT_SUPPORTED
    assert "upi" in exc.value.detail


async def test_a_card_succeeds_on_a_merchant_that_enabled_cards(gate: Gate) -> None:
    """The same attempt refuses on a UPI-only store and passes here. That is the
    deviation the conformance badge names (SPEC §9)."""
    policy = Policy(enabled_methods=frozenset({PaymentMethod.UPI, PaymentMethod.CARD}))
    decision = await Gate(gate._trait, policy).decide(  # noqa: SLF001
        _request([Line(sku="SD-STICKERS", qty=1)], method=PaymentMethod.CARD)
    )
    assert decision.transcript.checks[-1].result is CheckResult.PASS


# ── settle() ─────────────────────────────────────────────────────────────────


async def test_settle_resolves_the_deferred_check_and_captures(gate: Gate, ledger: Ledger) -> None:
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    decision = await gate.decide(request)
    await ledger.reserve(request.order_id, decision.total_minor, "INR")

    result = await settle(
        decision,
        ProviderRecord(request.order_id, decision.total_minor, "INR", "pay_1", succeeded=True),
        ledger,
    )

    assert result.status is OrderStatus.PAID
    assert decision.transcript.deferred_checks == []
    assert decision.transcript.binding is not None
    assert decision.transcript.binding.what.value == "amount"
    assert decision.transcript.binding.by.value == "payer-bank"
    position = await ledger.position(request.order_id)
    assert position.open_holds_minor == 0
    await ledger.assert_invariants(request.order_id, closed=True)


async def test_an_amount_mismatch_releases_exactly_its_own_hold(gate: Gate, ledger: Ledger) -> None:
    """Never auto-capture a mismatch. The hold it had is released, the order
    fails naming why, and nothing is captured."""
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    decision = await gate.decide(request)
    await ledger.reserve(request.order_id, decision.total_minor, "INR")

    result = await settle(
        decision,
        ProviderRecord(request.order_id, decision.total_minor - 1, "INR", "p", succeeded=True),
        ledger,
    )

    assert result.status is OrderStatus.FAILED
    assert result.reason_code is ReasonCode.AMOUNT_MISMATCH
    entries = await ledger.entries(request.order_id)
    assert [e.kind for e in entries] == [LedgerKind.RESERVE, LedgerKind.RELEASE]
    assert (await ledger.position(request.order_id)).captured_minor == 0


async def test_a_sold_out_failure_writes_no_release(gate: Gate, ledger: Ledger) -> None:
    """A `sold-out` refusal took no hold, so there is nothing to close. The
    trait refuses a release that balances nothing."""
    request = _request([Line(sku="SD-CAP-M", qty=99)])
    with pytest.raises(TraitError) as exc:
        await gate._trait.reserve(request.order_id, request.lines)  # noqa: SLF001
    assert exc.value.code is ReasonCode.SOLD_OUT
    assert await ledger.entries(request.order_id) == []


async def test_a_failed_provider_record_releases_and_fails(gate: Gate, ledger: Ledger) -> None:
    request = _request([Line(sku="SD-STICKERS", qty=1)])
    decision = await gate.decide(request)
    await ledger.reserve(request.order_id, decision.total_minor, "INR")
    result = await settle(
        decision,
        ProviderRecord(request.order_id, decision.total_minor, "INR", "p", succeeded=False),
        ledger,
    )
    assert result.status is OrderStatus.FAILED
    await ledger.assert_invariants(request.order_id, closed=True)


# ── COD ──────────────────────────────────────────────────────────────────────


async def test_a_cod_order_writes_nothing_at_order_time_and_one_capture_at_collection(
    gate: Gate, ledger: Ledger
) -> None:
    """ADR-0018's whole point: no money is held across a COD delivery, because
    no available UPI primitive holds it."""
    request = _request(
        [Line(sku="SD-STICKERS", qty=1)],
        authority=Authority(
            kind=AuthorityKind.CONFIRMED_INTENT,
            mechanism=IntentMechanism.UPI_VERIFY,
            present=True,
        ),
        method=PaymentMethod.CASH_ON_DELIVERY,
    )
    decision = await gate.decide(request)

    assert await ledger.entries(request.order_id) == [], "COD wrote at order time"

    result = await collect(decision, ledger)

    assert result.status is OrderStatus.PAID
    entries = await ledger.entries(request.order_id)
    assert [e.kind for e in entries] == [LedgerKind.CAPTURE]
    assert entries[0].closes_hold is False, "a COD capture closes a hold that never existed"
    await ledger.assert_invariants(request.order_id, closed=True)


async def test_an_rto_writes_nothing_at_all(gate: Gate, ledger: Ledger) -> None:
    """Nothing moved. The goods return through `restock`, and the order is
    `cancelled` with an `rto` reason — which is already what `cancelled` means."""
    request = _request(
        [Line(sku="SD-STICKERS", qty=1)],
        authority=Authority(
            kind=AuthorityKind.CONFIRMED_INTENT,
            mechanism=IntentMechanism.UPI_VERIFY,
            present=True,
        ),
        method=PaymentMethod.CASH_ON_DELIVERY,
    )
    decision = await gate.decide(request)
    result = await rto(decision)
    assert result.status is OrderStatus.CANCELLED
    assert result.ledger_kind is None
    assert await ledger.entries(request.order_id) == []


# ── Concurrency ──────────────────────────────────────────────────────────────


async def test_fifty_concurrent_taps_on_five_units_yield_exactly_five(
    merchant: FakeMerchant, trait: TraitClient
) -> None:
    """Against the conformance fake. B2 repeats this against real Postgres —
    they test different things and both are required."""
    merchant.stock["SD-PLUSH-MINI"] = 5

    async def attempt(n: int) -> bool:
        try:
            await trait.reserve(f"ord_race_{n}", [Line(sku="SD-PLUSH-MINI", qty=1)])
            return True
        except TraitError:
            return False

    results = await asyncio.gather(*(attempt(n) for n in range(50)))

    assert sum(results) == 5, f"{sum(results)} reservations against 5 units"
    assert merchant.stock["SD-PLUSH-MINI"] == 0
    assert all(v >= 0 for v in merchant.stock.values()), "stock went negative"
