"""The Ledger's two invariants, and the traps `main` left behind."""

from __future__ import annotations

import pytest
from openstore.sidecar.core.codes import LedgerKind
from openstore.sidecar.ledger.entries import Ledger, LedgerError

INR = "INR"


async def test_a_hold_closes_by_capture(ledger: Ledger) -> None:
    await ledger.reserve("ord_1", 259700, INR)
    await ledger.capture("ord_1", 259700, INR)
    position = await ledger.position("ord_1")
    assert position.open_holds_minor == 0
    await ledger.assert_invariants("ord_1", closed=True)


async def test_a_hold_closes_by_release(ledger: Ledger) -> None:
    await ledger.reserve("ord_2", 1000, INR)
    await ledger.release("ord_2", 1000, INR)
    await ledger.assert_invariants("ord_2", closed=True)
    position = await ledger.position("ord_2")
    assert position.net_cash_minor == 0
    assert position.released_minor == 1000


async def test_release_cannot_exceed_the_hold(ledger: Ledger) -> None:
    await ledger.reserve("ord_2b", 1000, INR)
    with pytest.raises(LedgerError, match="exceeds"):
        await ledger.release("ord_2b", 1001, INR)


async def test_a_single_paisa_still_open_is_still_a_hold_to_release(ledger: Ledger) -> None:
    await ledger.reserve("ord_2c", 1000, INR)
    await ledger.release("ord_2c", 999, INR)
    await ledger.release("ord_2c", 1, INR, attempt=2)
    assert (await ledger.position("ord_2c")).open_holds_minor == 0


async def test_a_second_reserve_on_an_open_hold_is_refused(ledger: Ledger) -> None:
    """Each RESERVE ends in exactly one CAPTURE or RELEASE. Two open holds
    cannot both close against one closing entry."""
    await ledger.reserve("ord_3", 1000, INR)
    with pytest.raises(LedgerError, match="already holds"):
        await ledger.reserve("ord_3", 1000, INR, attempt=2)


async def test_a_reserve_is_allowed_again_once_the_first_hold_closed(ledger: Ledger) -> None:
    """The guard blocks a second *open* hold, not a second order on an id
    that already closed one — `reserved_minor` alone (the cumulative total
    ever held) is not the signal; `open_holds_minor` is."""
    await ledger.reserve("ord_3b", 1000, INR)
    await ledger.release("ord_3b", 1000, INR)
    await ledger.reserve("ord_3b", 500, INR, attempt=2)
    assert (await ledger.position("ord_3b")).open_holds_minor == 500


async def test_a_second_reserve_is_refused_even_against_a_single_paisa_still_open(
    ledger: Ledger,
) -> None:
    """The guard is "any open hold blocks it", not "a hold above some
    threshold" — a partial release leaving one paisa open still refuses."""
    await ledger.reserve("ord_3c", 1000, INR)
    await ledger.release("ord_3c", 999, INR)
    with pytest.raises(LedgerError, match="already holds 1 paise"):
        await ledger.reserve("ord_3c", 500, INR, attempt=2)


async def test_releasing_nothing_is_refused(ledger: Ledger) -> None:
    """A `sold-out` failure took no hold. Releasing one that was never taken
    makes escrow-zero close on an entry that balances nothing."""
    with pytest.raises(LedgerError, match="no open hold"):
        await ledger.release("ord_never", 1000, INR)


async def test_capturing_without_a_hold_is_refused_unless_it_says_cod(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="no open hold"):
        await ledger.capture("ord_4", 1000, INR)
    entry = await ledger.capture("ord_4", 1000, INR, allow_without_reserve=True)
    assert entry.kind is LedgerKind.CAPTURE


async def test_capture_cannot_exceed_the_hold(ledger: Ledger) -> None:
    await ledger.reserve("ord_5", 1000, INR)
    with pytest.raises(LedgerError, match="exceeds"):
        await ledger.capture("ord_5", 1001, INR)


async def test_a_single_paisa_still_open_is_still_a_hold_to_capture(ledger: Ledger) -> None:
    """The "no hold" and "exceeds the hold" guards are `<= 0` / `> 0` against
    the open balance, not against some larger threshold — a hold whittled
    down to one paisa by a partial release is still capturable, and still
    caps what can be taken from it."""
    await ledger.reserve("ord_5b", 1000, INR)
    await ledger.release("ord_5b", 999, INR)
    with pytest.raises(LedgerError, match="exceeds the 1 held"):
        await ledger.capture("ord_5b", 2, INR)
    entry = await ledger.capture("ord_5b", 1, INR)
    assert entry.closes_hold is True
    assert (await ledger.position("ord_5b")).open_holds_minor == 0


# ── Refunds are bounded, and partials both write ─────────────────────────────


async def test_two_successive_partial_refunds_both_write(ledger: Ledger) -> None:
    """**The `main` trap.** `create_refund_entry` keyed idempotency on the
    checkout id alone, so a second partial refund returned the first entry and
    wrote nothing, silently. The key carries the refund's own id now."""
    await ledger.reserve("ord_6", 10000, INR)
    await ledger.capture("ord_6", 10000, INR)

    first = await ledger.refund("ord_6", 3000, INR, refund_id="rfnd_a")
    second = await ledger.refund("ord_6", 2500, INR, refund_id="rfnd_b")

    assert first.idempotency_key != second.idempotency_key
    entries = [e for e in await ledger.entries("ord_6") if e.kind is LedgerKind.REFUND]
    assert len(entries) == 2, "the second partial refund wrote nothing"
    assert (await ledger.position("ord_6")).refunded_minor == 5500


async def test_the_same_refund_id_replays_rather_than_doubling(ledger: Ledger) -> None:
    await ledger.reserve("ord_7", 10000, INR)
    await ledger.capture("ord_7", 10000, INR)
    await ledger.refund("ord_7", 3000, INR, refund_id="rfnd_same")
    await ledger.refund("ord_7", 3000, INR, refund_id="rfnd_same")
    assert (await ledger.position("ord_7")).refunded_minor == 3000


async def test_over_refund_is_refused(ledger: Ledger) -> None:
    await ledger.reserve("ord_8", 10000, INR)
    await ledger.capture("ord_8", 10000, INR)
    await ledger.refund("ord_8", 7000, INR, refund_id="r1")
    with pytest.raises(LedgerError, match="exceeds"):
        await ledger.refund("ord_8", 3001, INR, refund_id="r2")
    await ledger.refund("ord_8", 3000, INR, refund_id="r3")
    assert (await ledger.position("ord_8")).refundable_minor == 0


async def test_a_zero_refund_is_refused(ledger: Ledger) -> None:
    """Distinct from `_append`'s general "negative amount" guard: a refund of
    exactly zero is not negative, but there is nothing to give back."""
    await ledger.reserve("ord_8b", 10000, INR)
    await ledger.capture("ord_8b", 10000, INR)
    with pytest.raises(LedgerError, match="is not an amount"):
        await ledger.refund("ord_8b", 0, INR, refund_id="r1")


async def test_refunding_an_uncaptured_order_is_refused(ledger: Ledger) -> None:
    await ledger.reserve("ord_9", 10000, INR)
    with pytest.raises(LedgerError, match="exceeds"):
        await ledger.refund("ord_9", 1, INR, refund_id="r")


# ── REVERSAL is bounded by neither ───────────────────────────────────────────


async def test_a_reversal_after_a_full_refund_lands_net_negative_and_alerts(
    ledger: Ledger,
) -> None:
    """Money that has already left must be recorded. Refusing it is how books go
    wrong; negative net raises an alert, never a refusal."""
    await ledger.reserve("ord_10", 10000, INR)
    await ledger.capture("ord_10", 10000, INR)
    await ledger.refund("ord_10", 10000, INR, refund_id="r1")

    await ledger.reversal("ord_10", 10000, INR, reversal_id="rev_1")

    position = await ledger.position("ord_10")
    assert position.net_cash_minor == -10000
    assert position.is_net_negative
    # And the invariants still hold: a REVERSAL is bounded by neither.
    await ledger.assert_invariants("ord_10", closed=True)


async def test_a_reversal_needs_no_capture_at_all(ledger: Ledger) -> None:
    await ledger.reversal("ord_11", 500, INR, reversal_id="rev_x")
    assert (await ledger.position("ord_11")).reversed_minor == 500


async def test_a_zero_reversal_is_refused(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="is not an amount"):
        await ledger.reversal("ord_11b", 0, INR, reversal_id="rev_y")


# ── COD writes nothing until collection ──────────────────────────────────────


async def test_cod_writes_nothing_at_order_time_and_one_capture_at_collection(
    ledger: Ledger,
) -> None:
    """ADR-0018. The Ledger's entries are about money only; stock is held by
    door 3 alone, so an order holding no money is expressible."""
    assert await ledger.entries("ord_cod") == []

    await ledger.capture("ord_cod", 25000, INR, allow_without_reserve=True)

    entries = await ledger.entries("ord_cod")
    assert [e.kind for e in entries] == [LedgerKind.CAPTURE]
    position = await ledger.position("ord_cod")
    assert position.reserved_minor == 0
    # Escrow-zero needs no exception: it quantifies over RESERVE entries.
    await ledger.assert_invariants("ord_cod", closed=True)


# ── Shape ────────────────────────────────────────────────────────────────────


async def test_entries_are_append_only_and_ordered(ledger: Ledger) -> None:
    await ledger.reserve("ord_12", 1000, INR)
    await ledger.capture("ord_12", 1000, INR)
    await ledger.refund("ord_12", 400, INR, refund_id="r1")
    assert [e.kind for e in await ledger.entries("ord_12")] == [
        LedgerKind.RESERVE,
        LedgerKind.CAPTURE,
        LedgerKind.REFUND,
    ]


async def test_every_entry_carries_an_amount_and_a_utc_timestamp(ledger: Ledger) -> None:
    entry = await ledger.reserve("ord_13", 1234, INR)
    assert entry.amount_minor == 1234
    assert entry.created_at.tzinfo is not None, "time is UTC and never naive"


async def test_a_negative_amount_is_refused(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="negative"):
        await ledger.reserve("ord_14", -1, INR)


async def test_position_is_derived_not_stored(ledger: Ledger) -> None:
    """A cached balance is a second source of truth, and the first thing it does
    is disagree with the rows."""
    await ledger.reserve("ord_15", 5000, INR)
    await ledger.capture("ord_15", 5000, INR)
    first = await ledger.position("ord_15")
    await ledger.refund("ord_15", 1000, INR, refund_id="r")
    second = await ledger.position("ord_15")
    assert first.net_cash_minor == 5000
    assert second.net_cash_minor == 4000


async def test_assert_invariants_closed_raises_on_an_order_still_open(ledger: Ledger) -> None:
    """The `closed=True` check has never been made to fail before: every other
    call site is a positive assertion. An unclosed hold on one order, checked
    while another order in the same ledger is clean, is also what proves the
    check reads *this* order's position rather than a generically-empty one."""
    await ledger.reserve("ord_16", 1000, INR)
    await ledger.reserve("ord_17", 500, INR)
    await ledger.capture("ord_17", 500, INR)
    await ledger.assert_invariants("ord_17", closed=True)
    with pytest.raises(LedgerError, match="still holds 1000 paise"):
        await ledger.assert_invariants("ord_16", closed=True)
