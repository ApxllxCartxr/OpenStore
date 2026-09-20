"""Order lifecycle: eight statuses on both paths, and one `expires_at` column
carrying three different deadlines.

The sidecar owns both expiry clocks and the Merchant never self-expires (SPEC
§5). That is load-bearing in an unobvious way: a wedged or stopped sidecar holds
stock indefinitely, and the only defence is that somebody can see it — which is
why `/agentic` lists overdue holds and the documented release path goes through
the sidecar, never a Merchant-side write (SPEC §14).

`expires_at` always means "the next deadline the sidecar will act on". It is one
column and three deadlines:

1. **`pending` at 24h** — the Quote's validity, `time-limit-reached`. Nothing is
   held, so nothing is returned. "Re-add, don't re-search."
2. **`confirmed` at the payment-link expiry** (prepaid, default 15 min, never
   longer than the Provider's own lifetime) — `payment-window-elapsed`, plus a
   `RELEASE`. An abandoned tap must not hold stock: Woo's `hold-stock` default
   is 60 minutes and a UPI collect request expires in minutes, so leaving
   `confirmed` open for 24h would be an inventory-denial hole any self-registered
   stranger could open at will.
3. **The COD delivery window at 7 days** — `delivery-window-elapsed`, which
   **alerts the Merchant and never auto-cancels**. A parcel that is late is not a
   parcel that is lost, and only the Merchant knows which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from openstore.sidecar.core.codes import (
    CancellationReason,
    OrderStatus,
    PaymentMethod,
    ReasonCode,
)

#: §16.7.
PENDING_TTL = timedelta(hours=24)
PAYMENT_WINDOW = timedelta(minutes=15)
COD_DELIVERY_WINDOW = timedelta(days=7)

#: Which statuses a sweep may act on. `paid`, `completed`, `refunded`,
#: `cancelled` and `failed` are terminal or post-money and are never swept.
SWEEPABLE = frozenset({OrderStatus.PENDING, OrderStatus.CONFIRMED})


@dataclass(frozen=True)
class Deadline:
    """What the sidecar will do next, and when."""

    at: datetime
    reason_code: ReasonCode
    releases_stock: bool
    alerts_only: bool = False
    """The COD delivery window alerts rather than acting. A sweep that
    auto-cancelled a late parcel would cancel orders that are about to arrive."""


def next_deadline(
    status: OrderStatus,
    method: PaymentMethod,
    *,
    confirmed_at: datetime | None = None,
    created_at: datetime | None = None,
    link_expires_at: datetime | None = None,
) -> Deadline | None:
    """The single value `expires_at` holds for this order, right now."""
    if status is OrderStatus.PENDING:
        if created_at is None:
            raise ValueError("a pending order needs a created_at to expire from")
        return Deadline(
            at=created_at + PENDING_TTL,
            reason_code=ReasonCode.TIME_LIMIT_REACHED,
            # Nothing was held: the 24h window is the Quote's validity, not an
            # inventory hold, which is why nothing is reserved until the tap.
            releases_stock=False,
        )

    if status is OrderStatus.CONFIRMED:
        if confirmed_at is None:
            raise ValueError("a confirmed order needs a confirmed_at to expire from")

        if method is PaymentMethod.CASH_ON_DELIVERY:
            return Deadline(
                at=confirmed_at + COD_DELIVERY_WINDOW,
                reason_code=ReasonCode.DELIVERY_WINDOW_ELAPSED,
                releases_stock=False,
                alerts_only=True,
            )

        # Never longer than the Provider's own link lifetime: a hold outliving
        # the link it was taken for is stock nobody can pay for.
        deadline = confirmed_at + PAYMENT_WINDOW
        if link_expires_at is not None:
            deadline = min(deadline, link_expires_at)
        return Deadline(
            at=deadline,
            reason_code=ReasonCode.PAYMENT_WINDOW_ELAPSED,
            releases_stock=True,
        )

    return None


@dataclass(frozen=True)
class SweepAction:
    order_id: str
    to_status: OrderStatus | None
    reason_code: ReasonCode
    release_hold: bool
    alert_only: bool


def sweep(
    order_id: str,
    status: OrderStatus,
    method: PaymentMethod,
    deadline: Deadline,
    *,
    now: datetime | None = None,
) -> SweepAction | None:
    """What the expiry sweeper should do to this order, if anything.

    Returns `None` rather than raising when the deadline has not passed: a
    sweeper runs every 60 seconds over everything, and most of what it looks at
    is fine.
    """
    moment = now or datetime.now(UTC)
    if status not in SWEEPABLE or moment < deadline.at:
        return None

    if deadline.alerts_only:
        return SweepAction(
            order_id=order_id,
            to_status=None,
            reason_code=deadline.reason_code,
            release_hold=False,
            alert_only=True,
        )

    return SweepAction(
        order_id=order_id,
        to_status=OrderStatus.EXPIRED,
        reason_code=deadline.reason_code,
        release_hold=deadline.releases_stock,
        alert_only=False,
    )


#: Legal transitions. `cancelled` is pre-money only and `refunded` post-money
#: only, which is what keeps "status flip alone never moves money" true (Woo
#: parity, SPEC §6).
TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset(
        {OrderStatus.CONFIRMED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
    ),
    OrderStatus.CONFIRMED: frozenset(
        {
            OrderStatus.PAID,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }
    ),
    OrderStatus.PAID: frozenset({OrderStatus.REFUNDED, OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset({OrderStatus.REFUNDED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.FAILED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
}


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    return to_status in TRANSITIONS[from_status]


def rto_transition() -> tuple[OrderStatus, CancellationReason]:
    """An RTO is `cancelled` with an `rto` reason — which is already what
    `cancelled` means: pre-money, stock returned. It is not a ninth status."""
    return OrderStatus.CANCELLED, CancellationReason.RTO
