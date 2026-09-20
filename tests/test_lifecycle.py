"""Eight statuses on both paths, and one `expires_at` carrying three deadlines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from openstore.sidecar.core.codes import (
    CancellationReason,
    OrderStatus,
    PaymentMethod,
    ReasonCode,
)
from openstore.sidecar.gate.lifecycle import (
    COD_DELIVERY_WINDOW,
    PAYMENT_WINDOW,
    PENDING_TTL,
    can_transition,
    next_deadline,
    rto_transition,
    sweep,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def test_eight_statuses_and_no_ninth() -> None:
    assert len(OrderStatus) == 8


# ── The three deadlines ──────────────────────────────────────────────────────


def test_pending_expires_at_24h_and_returns_no_stock() -> None:
    """That window is the Quote's validity, not an inventory hold — which is why
    nothing is reserved until the tap."""
    deadline = next_deadline(OrderStatus.PENDING, PaymentMethod.UPI, created_at=NOW)
    assert deadline is not None
    assert deadline.at == NOW + PENDING_TTL
    assert deadline.reason_code is ReasonCode.TIME_LIMIT_REACHED
    assert deadline.releases_stock is False


def test_confirmed_expires_at_the_payment_window_and_releases_stock() -> None:
    """An abandoned tap must not hold stock. Woo's hold-stock default is 60
    minutes and a UPI collect expires in minutes; leaving confirmed open for 24h
    would be an inventory-denial hole any stranger could open at will."""
    deadline = next_deadline(OrderStatus.CONFIRMED, PaymentMethod.UPI, confirmed_at=NOW)
    assert deadline is not None
    assert deadline.at == NOW + PAYMENT_WINDOW
    assert deadline.reason_code is ReasonCode.PAYMENT_WINDOW_ELAPSED
    assert deadline.releases_stock is True


def test_the_payment_window_never_outlives_the_provider_link() -> None:
    """A hold outliving the link it was taken for is stock nobody can pay for."""
    short_link = NOW + timedelta(minutes=5)
    deadline = next_deadline(
        OrderStatus.CONFIRMED, PaymentMethod.UPI, confirmed_at=NOW, link_expires_at=short_link
    )
    assert deadline is not None
    assert deadline.at == short_link


def test_cod_carries_a_delivery_window_that_alerts_rather_than_cancels() -> None:
    """A parcel that is late is not a parcel that is lost, and only the Merchant
    knows which."""
    deadline = next_deadline(
        OrderStatus.CONFIRMED, PaymentMethod.CASH_ON_DELIVERY, confirmed_at=NOW
    )
    assert deadline is not None
    assert deadline.at == NOW + COD_DELIVERY_WINDOW
    assert deadline.reason_code is ReasonCode.DELIVERY_WINDOW_ELAPSED
    assert deadline.alerts_only is True
    assert deadline.releases_stock is False


def test_a_terminal_order_has_no_next_deadline() -> None:
    for status in (OrderStatus.PAID, OrderStatus.REFUNDED, OrderStatus.CANCELLED):
        assert next_deadline(status, PaymentMethod.UPI, confirmed_at=NOW) is None


# ── The sweeper ──────────────────────────────────────────────────────────────


def test_the_sweeper_does_nothing_before_the_deadline() -> None:
    deadline = next_deadline(OrderStatus.PENDING, PaymentMethod.UPI, created_at=NOW)
    assert deadline is not None
    assert sweep("ord_1", OrderStatus.PENDING, PaymentMethod.UPI, deadline, now=NOW) is None


def test_an_abandoned_confirmed_order_releases_stock_at_link_expiry() -> None:
    deadline = next_deadline(OrderStatus.CONFIRMED, PaymentMethod.UPI, confirmed_at=NOW)
    assert deadline is not None
    action = sweep(
        "ord_2",
        OrderStatus.CONFIRMED,
        PaymentMethod.UPI,
        deadline,
        now=NOW + PAYMENT_WINDOW + timedelta(seconds=1),
    )
    assert action is not None
    assert action.to_status is OrderStatus.EXPIRED
    assert action.reason_code is ReasonCode.PAYMENT_WINDOW_ELAPSED
    assert action.release_hold is True


def test_an_expired_pending_order_returns_nothing() -> None:
    deadline = next_deadline(OrderStatus.PENDING, PaymentMethod.UPI, created_at=NOW)
    assert deadline is not None
    action = sweep(
        "ord_3",
        OrderStatus.PENDING,
        PaymentMethod.UPI,
        deadline,
        now=NOW + PENDING_TTL + timedelta(seconds=1),
    )
    assert action is not None
    assert action.to_status is OrderStatus.EXPIRED
    assert action.release_hold is False, "nothing was held, so nothing is returned"


def test_a_late_cod_parcel_alerts_and_changes_no_status() -> None:
    deadline = next_deadline(
        OrderStatus.CONFIRMED, PaymentMethod.CASH_ON_DELIVERY, confirmed_at=NOW
    )
    assert deadline is not None
    action = sweep(
        "ord_4",
        OrderStatus.CONFIRMED,
        PaymentMethod.CASH_ON_DELIVERY,
        deadline,
        now=NOW + COD_DELIVERY_WINDOW + timedelta(days=1),
    )
    assert action is not None
    assert action.alert_only is True
    assert action.to_status is None
    assert action.release_hold is False


def test_the_sweeper_never_touches_a_paid_order() -> None:
    """Only `pending` and `confirmed` are sweepable. Everything else is terminal
    or post-money, and a sweep that expired a paid order would strand money."""
    deadline = next_deadline(OrderStatus.CONFIRMED, PaymentMethod.UPI, confirmed_at=NOW)
    assert deadline is not None
    assert (
        sweep("ord_5", OrderStatus.PAID, PaymentMethod.UPI, deadline, now=NOW + timedelta(days=9))
        is None
    )


# ── Transitions ──────────────────────────────────────────────────────────────


def test_cancelled_is_pre_money_only() -> None:
    assert can_transition(OrderStatus.PENDING, OrderStatus.CANCELLED)
    assert can_transition(OrderStatus.CONFIRMED, OrderStatus.CANCELLED)
    assert not can_transition(OrderStatus.PAID, OrderStatus.CANCELLED)


def test_refunded_is_post_money_only() -> None:
    assert can_transition(OrderStatus.PAID, OrderStatus.REFUNDED)
    assert can_transition(OrderStatus.COMPLETED, OrderStatus.REFUNDED)
    assert not can_transition(OrderStatus.PENDING, OrderStatus.REFUNDED)
    assert not can_transition(OrderStatus.CONFIRMED, OrderStatus.REFUNDED)


def test_a_terminal_status_goes_nowhere() -> None:
    for status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED):
        assert not any(can_transition(status, target) for target in OrderStatus)


def test_an_rto_is_cancelled_with_a_reason_not_a_ninth_status() -> None:
    """Already what `cancelled` means: pre-money, stock returned."""
    status, reason = rto_transition()
    assert status is OrderStatus.CANCELLED
    assert reason is CancellationReason.RTO


def test_a_pending_order_cannot_jump_straight_to_paid() -> None:
    """Money moves through `confirmed`, where the hold is taken."""
    assert not can_transition(OrderStatus.PENDING, OrderStatus.PAID)


def test_deadlines_need_the_timestamp_they_measure_from() -> None:
    with pytest.raises(ValueError, match="created_at"):
        next_deadline(OrderStatus.PENDING, PaymentMethod.UPI)
    with pytest.raises(ValueError, match="confirmed_at"):
        next_deadline(OrderStatus.CONFIRMED, PaymentMethod.UPI)
