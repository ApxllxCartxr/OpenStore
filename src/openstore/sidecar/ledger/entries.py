"""The Ledger: append-only, one row per money event.

**Single entry per money event, not double-entry.** `main` used named account
legs (`merchant_revenue`, `customer`) with paired rows per event. That is not
carried over, and the reason is worth stating: this plan's invariants are stated
*per order*, and mixing the two representations makes the arithmetic disagree
without failing anything loudly — the balance looks right while the per-order
sums do not (SPECS/PLAN.md §3).

Two invariants, deliberately separate so they fail independently:

1. **Holds close.** Each `RESERVE` ends in exactly one `CAPTURE` or `RELEASE`.
   This is the escrow-zero rule and it says nothing about cash.
2. **Refunds are bounded.** `sum(REFUND) <= captured - already_refunded`,
   enforced at write time rather than checked afterwards.

A `REVERSAL` is bounded by neither. It records money removed by someone other
than the Merchant — an adjudicated UPI complaint, a post-settlement adjustment,
a bank correction, a card chargeback — and it is written **even when it drives
the order's net cash negative**, because refusing to record money that has
already left is how books go wrong. Negative net raises an alert, never a
refusal.

COD (ADR-0018): entries here are about money only; stock is held by trait door
3 alone. `cash-on-delivery` writes nothing at order time, a single `CAPTURE`
with **no preceding `RESERVE`** at collection, and nothing at all on RTO.
Escrow-zero needs no exception for this — it quantifies over `RESERVE` entries,
and a capture that never passed through a hold cannot fail to close one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy import Column as Col
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from openstore.sidecar.core.codes import LedgerKind, ReasonCode
from openstore.sidecar.trait.errors import TraitError

metadata = MetaData()

#: One row per money event. No account legs, no derived balances stored — a
#: stored balance is a second source of truth that drifts silently.
ledger_entries = Table(
    "ledger_entries",
    metadata,
    # SQLite only autoincrements INTEGER PRIMARY KEY, never BIGINT, so the
    # variant is load-bearing: without it every insert arrives with a NULL id
    # and looks like a duplicate-key collision.
    Col(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Col("kind", String(16), nullable=False),
    Col("order_id", String(64), nullable=False),
    Col("amount_minor", BigInteger, nullable=False),
    Col("currency", String(3), nullable=False),
    Col("created_at", DateTime(timezone=True), nullable=False),
    Col("idempotency_key", String(128), nullable=False),
    # Whether this entry closes a RESERVE. Recorded rather than inferred from
    # the kind, because COD's CAPTURE closes nothing — it never passed through a
    # hold (ADR-0018). Inferring it made a cash sale look like it had closed a
    # hold that never existed, which then read as "closed more than it held".
    Col("closes_hold", Boolean, nullable=False, default=False),
    # The unique constraint is the race-closer, not the read-then-act check
    # above it. Two concurrent retries both pass a "have we already written
    # this?" SELECT; only one survives the INSERT.
    UniqueConstraint("idempotency_key", name="uq_ledger_idempotency"),
    CheckConstraint("amount_minor >= 0", name="ck_ledger_amount_non_negative"),
    Index("ix_ledger_order", "order_id"),
)


class LedgerError(Exception):
    """An invariant refused a write. Never caught to continue — the write does
    not happen, and the caller is wrong about the state of the money."""


@dataclass(frozen=True)
class Entry:
    kind: LedgerKind
    order_id: str
    amount_minor: int
    currency: str
    created_at: datetime
    idempotency_key: str
    closes_hold: bool = False


@dataclass(frozen=True)
class OrderPosition:
    """What the Ledger says about one order, computed from the rows every time.

    Derived, never stored. A cached balance is a second source of truth, and the
    first thing it does is disagree with the rows.
    """

    reserved_minor: int
    captured_minor: int
    released_minor: int
    refunded_minor: int
    reversed_minor: int
    closing_minor: int
    """How much has actually closed a hold. Not the same as captured + released:
    a COD capture closes nothing, because there was nothing to close."""

    @property
    def open_holds_minor(self) -> int:
        """Escrow-zero: what is still held. Zero once every hold has closed.

        Quantifies over `RESERVE` entries — a capture that never passed through
        a hold cannot fail to close one, which is why COD needs no exception
        here (ADR-0018).
        """
        return self.reserved_minor - self.closing_minor

    @property
    def refundable_minor(self) -> int:
        return self.captured_minor - self.refunded_minor

    @property
    def net_cash_minor(self) -> int:
        """What the Merchant actually kept. Can go negative after a `REVERSAL`,
        and that is a fact to alert on, not a state to refuse."""
        return self.captured_minor - self.refunded_minor - self.reversed_minor

    @property
    def is_net_negative(self) -> bool:
        return self.net_cash_minor < 0


class Ledger:
    """Append-only. There is no update and no delete, by construction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def position(self, order_id: str) -> OrderPosition:
        rows = (
            await self._session.execute(
                select(
                    ledger_entries.c.kind,
                    ledger_entries.c.amount_minor,
                    ledger_entries.c.closes_hold,
                ).where(ledger_entries.c.order_id == order_id)
            )
        ).all()
        totals = dict.fromkeys(LedgerKind, 0)
        closing = 0
        for kind, amount, closes in rows:
            totals[LedgerKind(kind)] += amount
            if closes:
                closing += amount
        return OrderPosition(
            reserved_minor=totals[LedgerKind.RESERVE],
            captured_minor=totals[LedgerKind.CAPTURE],
            released_minor=totals[LedgerKind.RELEASE],
            refunded_minor=totals[LedgerKind.REFUND],
            reversed_minor=totals[LedgerKind.REVERSAL],
            closing_minor=closing,
        )

    async def entries(self, order_id: str) -> list[Entry]:
        rows = (
            await self._session.execute(
                select(ledger_entries)
                .where(ledger_entries.c.order_id == order_id)
                .order_by(ledger_entries.c.id)
            )
        ).all()
        return [
            Entry(
                kind=LedgerKind(r.kind),
                order_id=r.order_id,
                amount_minor=r.amount_minor,
                currency=r.currency,
                created_at=r.created_at,
                idempotency_key=r.idempotency_key,
                closes_hold=r.closes_hold,
            )
            for r in rows
        ]

    # ── writes ───────────────────────────────────────────────────────────────

    async def _append(
        self,
        kind: LedgerKind,
        order_id: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        *,
        closes_hold: bool = False,
    ) -> Entry:
        if amount_minor < 0:
            raise LedgerError(f"{kind.value} amount {amount_minor} is negative")

        entry = Entry(
            kind=kind,
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            created_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
            closes_hold=closes_hold,
        )
        try:
            # A SAVEPOINT, not the whole transaction: a duplicate key is an
            # expected outcome of a retry, and rolling the session back would
            # discard every write that legitimately came before it in this unit
            # of work.
            async with self._session.begin_nested():
                await self._session.execute(
                    ledger_entries.insert().values(
                        kind=entry.kind.value,
                        order_id=entry.order_id,
                        amount_minor=entry.amount_minor,
                        currency=entry.currency,
                        created_at=entry.created_at,
                        idempotency_key=entry.idempotency_key,
                        closes_hold=entry.closes_hold,
                    )
                )
        except IntegrityError:
            # The key already wrote. Return what is there rather than the entry
            # we built — a retry must observe the first attempt's result, not a
            # second one that looks like it.
            existing = (
                await self._session.execute(
                    select(ledger_entries).where(
                        ledger_entries.c.idempotency_key == idempotency_key
                    )
                )
            ).one()
            return Entry(
                kind=LedgerKind(existing.kind),
                order_id=existing.order_id,
                amount_minor=existing.amount_minor,
                currency=existing.currency,
                created_at=existing.created_at,
                idempotency_key=existing.idempotency_key,
                closes_hold=existing.closes_hold,
            )
        return entry

    async def reserve(
        self, order_id: str, amount_minor: int, currency: str, *, attempt: int = 1
    ) -> Entry:
        """Money held. Stock is held separately by trait door 3 — the two are
        different acts and COD is where that stops being academic."""
        position = await self.position(order_id)
        if position.reserved_minor and position.open_holds_minor > 0:
            raise LedgerError(
                f"{order_id} already holds {position.open_holds_minor} paise; a second "
                f"RESERVE would give it two holds to close"
            )
        return await self._append(
            LedgerKind.RESERVE, order_id, amount_minor, currency, f"{order_id}:reserve:{attempt}"
        )

    async def capture(
        self,
        order_id: str,
        amount_minor: int,
        currency: str,
        *,
        attempt: int = 1,
        allow_without_reserve: bool = False,
    ) -> Entry:
        """Money taken.

        `allow_without_reserve` is the COD path (ADR-0018) and is explicit at
        the call site rather than inferred: cash collected at the doorstep never
        passed through a hold, and a capture that silently invents its own
        permission would also cover a prepaid bug.
        """
        position = await self.position(order_id)
        if position.open_holds_minor <= 0 and not allow_without_reserve:
            raise LedgerError(
                f"{order_id} has no open hold to capture. If this is cash on delivery, "
                f"say so with allow_without_reserve=True (ADR-0018)."
            )
        if position.open_holds_minor > 0 and amount_minor > position.open_holds_minor:
            raise LedgerError(
                f"capture of {amount_minor} exceeds the {position.open_holds_minor} held"
            )
        return await self._append(
            LedgerKind.CAPTURE,
            order_id,
            amount_minor,
            currency,
            f"{order_id}:capture:{attempt}",
            # A COD capture closes nothing: there was no hold.
            closes_hold=position.open_holds_minor > 0,
        )

    async def release(
        self, order_id: str, amount_minor: int, currency: str, *, attempt: int = 1
    ) -> Entry:
        """A hold let go. Refuses when there is nothing held: a `sold-out`
        failure took no hold, and releasing one that was never taken would make
        escrow-zero close on an entry that balances nothing."""
        position = await self.position(order_id)
        if position.open_holds_minor <= 0:
            raise LedgerError(
                f"{order_id} has no open hold to release (`{ReasonCode.NO_HOLD.value}`)"
            )
        if amount_minor > position.open_holds_minor:
            raise LedgerError(
                f"release of {amount_minor} exceeds the {position.open_holds_minor} held"
            )
        return await self._append(
            LedgerKind.RELEASE,
            order_id,
            amount_minor,
            currency,
            f"{order_id}:release:{attempt}",
            closes_hold=True,
        )

    async def refund(
        self, order_id: str, amount_minor: int, currency: str, *, refund_id: str
    ) -> Entry:
        """Money given back, bounded at write time.

        The key carries **the refund's own id**, not just the order. `main` keyed
        on the order alone, so a second partial refund returned the first entry
        and wrote nothing, silently — a bug a test would have caught only by
        asserting two refunds produce two rows (§3).
        """
        if amount_minor <= 0:
            raise LedgerError(f"refund of {amount_minor} is not an amount")
        position = await self.position(order_id)
        if amount_minor > position.refundable_minor:
            raise LedgerError(
                f"refund of {amount_minor} exceeds {position.refundable_minor} refundable "
                f"({position.captured_minor} captured, {position.refunded_minor} already "
                f"refunded)"
            )
        return await self._append(
            LedgerKind.REFUND, order_id, amount_minor, currency, f"{order_id}:refund:{refund_id}"
        )

    async def reversal(
        self, order_id: str, amount_minor: int, currency: str, *, reversal_id: str
    ) -> Entry:
        """Money removed by someone other than the Merchant, as the Provider
        reports it.

        Bounded by neither invariant and written even when it drives net cash
        negative. Refusing to record money that has already left is how books go
        wrong; the caller alerts on `is_net_negative` and never refuses.
        """
        if amount_minor <= 0:
            raise LedgerError(f"reversal of {amount_minor} is not an amount")
        return await self._append(
            LedgerKind.REVERSAL,
            order_id,
            amount_minor,
            currency,
            f"{order_id}:reversal:{reversal_id}",
        )

    # ── invariants, checkable on demand ──────────────────────────────────────

    async def assert_invariants(self, order_id: str, *, closed: bool = False) -> None:
        """Raise unless the two invariants hold.

        `closed=True` additionally requires every hold to have closed, which is
        only true once the order has reached a terminal state — asserting it
        mid-flight would fail every order that is merely in progress.
        """
        position = await self.position(order_id)

        if position.open_holds_minor < 0:
            raise LedgerError(
                f"{order_id} closed more than it held: {position.captured_minor} captured + "
                f"{position.released_minor} released against {position.reserved_minor} reserved"
            )
        if closed and position.open_holds_minor != 0:
            raise LedgerError(
                f"{order_id} still holds {position.open_holds_minor} paise; every RESERVE "
                f"must end in exactly one CAPTURE or RELEASE"
            )
        if position.refunded_minor > position.captured_minor:
            raise LedgerError(
                f"{order_id} refunded {position.refunded_minor} against "
                f"{position.captured_minor} captured"
            )


def no_hold_error(order_id: str) -> TraitError:
    """The closed-code form of a release with nothing to release."""
    return TraitError(ReasonCode.NO_HOLD, f"no open hold for {order_id}")
