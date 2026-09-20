"""`settle()` and `collect()` — the two ways money finishes moving.

`settle()` is the prepaid path: the Provider's webhook says the money moved,
and this verifies that record, resolves whatever `decide()` deferred, records
the Authority with its Binding, and captures.

`collect()` is COD's (ADR-0018), and it is a third entry point rather than a
branch inside `settle()` because it is a different act:

- It verifies no Provider record, because there is no rail.
- It records nothing about Authority — `confirmed-intent` already resolved at
  `decide()`, days earlier.
- It writes exactly one `CAPTURE` **with no preceding `RESERVE`**.
- It may run no Gate check at all: the goods are on the doorstep and there is
  nothing left to refuse.

The Provider decides the money-moved fact only, never authority (SPEC §8).
"""

from __future__ import annotations

from dataclasses import dataclass

from openstore.sidecar.core.codes import (
    AuthorityKind,
    CheckResult,
    GateCheck,
    LedgerKind,
    OrderStatus,
    ReasonCode,
)
from openstore.sidecar.gate.decide import Decision
from openstore.sidecar.gate.transcript import binding_for
from openstore.sidecar.ledger.entries import Ledger


class SettlementRefused(Exception):
    def __init__(self, reason_code: ReasonCode, detail: str) -> None:
        super().__init__(f"{reason_code.value}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class ProviderRecord:
    """What the Provider says happened. Untrusted as to authority, authoritative
    only as to whether money moved."""

    order_id: str
    amount_minor: int
    currency: str
    reference: str
    succeeded: bool


@dataclass
class Settlement:
    status: OrderStatus
    ledger_kind: LedgerKind | None
    reason_code: ReasonCode | None = None


async def settle(
    decision: Decision,
    record: ProviderRecord,
    ledger: Ledger,
    *,
    attempt: int = 1,
) -> Settlement:
    """Verify the Provider record, resolve deferrals, record Authority, capture.

    The amount/currency reconcile is the **only** post-payment check. A Merchant
    price edit between tap and webhook must not fail a paid order: that strands
    real money against no order, which is worse than the drift it would catch.
    """
    transcript = decision.transcript

    if not record.succeeded:
        await _release_if_held(ledger, decision, attempt=attempt)
        return Settlement(OrderStatus.FAILED, LedgerKind.RELEASE, ReasonCode.AMOUNT_MISMATCH)

    if record.currency != decision.quote.currency or record.amount_minor != decision.total_minor:
        # Never auto-capture a mismatch. The hold it had is released and the
        # order fails naming why.
        await _release_if_held(ledger, decision, attempt=attempt)
        return Settlement(
            OrderStatus.FAILED,
            LedgerKind.RELEASE,
            ReasonCode.AMOUNT_MISMATCH,
        )

    # Resolve whatever deferred. For `upi-pin` this is where the Authority
    # actually lands: the payer's own bank attested the amount.
    for deferred in list(transcript.deferred_checks):
        transcript.resolve_deferred(deferred.check, CheckResult.PASS)

    kind = transcript.authority_kind
    if kind is None:
        raise SettlementRefused(ReasonCode.AUTHORITY_MISSING, "no Authority kind on the decision")
    binding, _ = binding_for(kind, transcript.authority_mechanism)
    transcript.binding = binding

    transcript.assert_complete()

    await ledger.capture(
        decision.transcript.order_id,
        record.amount_minor,
        record.currency,
        attempt=attempt,
    )
    return Settlement(OrderStatus.PAID, LedgerKind.CAPTURE)


async def collect(
    decision: Decision,
    ledger: Ledger,
    *,
    attempt: int = 1,
) -> Settlement:
    """Cash taken at the door (ADR-0018).

    One `CAPTURE` with no preceding `RESERVE`, which the escrow-zero invariant
    permits because it quantifies over `RESERVE` entries — a capture that never
    passed through a hold cannot fail to close one. No Provider is touched,
    because there is none.
    """
    transcript = decision.transcript
    if transcript.authority_kind is not AuthorityKind.CONFIRMED_INTENT:
        raise SettlementRefused(
            ReasonCode.AUTHORITY_KIND_NOT_ENABLED,
            f"cash on delivery runs on confirmed-intent, not {transcript.authority_kind}",
        )
    transcript.assert_complete()

    await ledger.capture(
        transcript.order_id,
        decision.total_minor,
        decision.quote.currency,
        attempt=attempt,
        allow_without_reserve=True,
    )
    return Settlement(OrderStatus.PAID, LedgerKind.CAPTURE)


async def rto(decision: Decision) -> Settlement:
    """Returned to origin. Nothing moved, so nothing is written.

    The goods come back through `restock` on the trait, and the order is
    `cancelled` with an `rto` reason — which is already what `cancelled` means:
    pre-money, stock returned.
    """
    return Settlement(OrderStatus.CANCELLED, None)


async def _release_if_held(ledger: Ledger, decision: Decision, *, attempt: int) -> None:
    """Release the hold this order took, and only if it took one.

    A `sold-out` failure never reserved, so there is nothing to close; an
    `amount-mismatch` arrives after a successful reserve and does release it.
    Writing a `RELEASE` in the first case would balance an entry that does not
    exist.
    """
    position = await ledger.position(decision.transcript.order_id)
    if position.open_holds_minor > 0:
        await ledger.release(
            decision.transcript.order_id,
            position.open_holds_minor,
            decision.quote.currency,
            attempt=attempt,
        )


def assert_check_order(decision: Decision) -> None:
    """Every check ran in §6.5's order. The Transcript's bytes depend on it, so
    it is not reordered for tidiness."""
    ran = [c.check for c in decision.transcript.checks]
    if ran and ran[0] is not GateCheck.AUTHORITY_PRESENT_AND_ACCEPTED:
        raise ValueError("authority is check 1; a decision that starts elsewhere is not one")
