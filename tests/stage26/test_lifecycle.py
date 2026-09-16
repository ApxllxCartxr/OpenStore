# tests/stage26/test_lifecycle.py
# Stage 26.4: capture decrements; cancel, expiry, and failure restore; refund
# restocks — each idempotent under both reconciliation paths (webhook and
# hold-loop reconcile can both fire for one payment).

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import CART_ONE_VANILLA, MERCHANT, seed_tracked
from openstore.core.api import create_checkout_from_policy
from openstore.core.holdcancel import (
    cancel_hold,
    check_and_expire_checkouts,
)
from openstore.core.holdcancel import (
    refund_checkout as core_refund,
)
from openstore.core.inventory import (
    available_qty,
    committed_for_checkout,
    outstanding_for_checkout,
    verify_inventory_balances,
)
from openstore.models import Checkout, OrderState
from sqlmodel import select

SKU = "gelato_vanilla"
GOLDEN = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"


def _checkout(session, config, policy, checkout_id: str) -> Checkout:
    result = create_checkout_from_policy(
        config=config,
        session=session,
        trace_id=f"trace_{checkout_id}",
        client_id="cli_life",
        merchant_id=MERCHANT,
        cart_items=CART_ONE_VANILLA,
        cart_hash=f"hash_{checkout_id}",
        cart_version=1,
        policy=policy,
        assertion_verified=True,
        idempotency_key=checkout_id,
    )
    assert result.allowed is True
    session.commit()
    row = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
    assert row is not None and row.state == OrderState.HELD
    return row


def _paid_payload(checkout_id: str) -> dict:
    payload = json.loads((GOLDEN / "payment_link_paid.json").read_text())
    payload["payload"]["payment_link"]["entity"]["reference_id"] = checkout_id
    return payload


def test_capture_commits_and_balances(session, config, policy):
    """Webhook paid -> COMMIT beside the money CAPTURE; reserved nets to zero."""
    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_cap")
    assert available_qty(session, MERCHANT, SKU) == 4
    _apply_payment_link_paid(session, "payment_link.paid", _paid_payload(row.id))
    session.commit()
    assert outstanding_for_checkout(session, row.id, SKU) == 0
    assert committed_for_checkout(session, row.id, SKU) == 1
    assert available_qty(session, MERCHANT, SKU) == 4
    assert verify_inventory_balances(session, row.id) is True


def test_paid_webhook_twice_is_idempotent(session, config, policy):
    """Both reconciliation paths firing for one payment: the second dispatch
    (distinct event, same checkout) is a guarded no-op."""
    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_idem")
    _apply_payment_link_paid(session, "payment_link.paid", _paid_payload(row.id))
    session.commit()
    _apply_payment_link_paid(session, "payment_link.paid", _paid_payload(row.id))
    session.commit()
    assert committed_for_checkout(session, row.id, SKU) == 1
    assert verify_inventory_balances(session, row.id) is True


def test_cancel_restores(session, config, policy):
    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_cancel")
    cancel_hold(session, row.id, "t", "c", reason="test cancel")
    session.commit()
    assert outstanding_for_checkout(session, row.id, SKU) == 0
    assert available_qty(session, MERCHANT, SKU) == 5
    assert verify_inventory_balances(session, row.id) is True


def test_expiry_restores(session, config, policy):
    """Hold-loop expiry (AAL1 -> cancel path) releases the reservation."""
    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_exp")
    # Force expiry: AAL1 checkouts hold for an hour; backdate instead.
    row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    session.add(row)
    session.commit()
    assert check_and_expire_checkouts(session) >= 1
    session.commit()
    assert outstanding_for_checkout(session, row.id, SKU) == 0
    assert available_qty(session, MERCHANT, SKU) == 5
    assert verify_inventory_balances(session, row.id) is True


def test_failure_restores(session, config, policy):
    """payment.failed on a HELD checkout releases beside the money RELEASE."""
    from openstore.psp.razorpay_driver import _apply_payment_failed

    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_fail")
    payload = json.loads((GOLDEN / "payment_failed.json").read_text())
    try:
        payment = payload["payload"]["payment"]["entity"]
        payment["notes"] = {"checkout_id": row.id}
    except KeyError:
        pytest.skip("golden payment_failed shape has no payment entity")
    _apply_payment_failed(session, payload)
    session.commit()
    assert outstanding_for_checkout(session, row.id, SKU) == 0
    assert available_qty(session, MERCHANT, SKU) == 5
    assert verify_inventory_balances(session, row.id) is True


def test_refund_restocks(session, config, policy):
    """Core refund on RELEASED restocks committed units; available returns."""
    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_ref")
    _apply_payment_link_paid(session, "payment_link.paid", _paid_payload(row.id))
    session.commit()
    assert available_qty(session, MERCHANT, SKU) == 4
    core_refund(session, row.id, "t", "c", reason="test refund")
    session.commit()
    assert committed_for_checkout(session, row.id, SKU) == 0
    assert available_qty(session, MERCHANT, SKU) == 5
    assert verify_inventory_balances(session, row.id) is True


def test_cancelled_webhook_twice_is_idempotent(session, config, policy):
    from openstore.psp.razorpay_driver import _apply_payment_link_cancelled

    seed_tracked(session, SKU, 5)
    row = _checkout(session, config, policy, "chk_life_cx")
    payload = json.loads((GOLDEN / "payment_link_cancelled.json").read_text())
    payload["payload"]["payment_link"]["entity"]["reference_id"] = row.id
    _apply_payment_link_cancelled(session, payload)
    session.commit()
    _apply_payment_link_cancelled(session, payload)
    session.commit()
    assert outstanding_for_checkout(session, row.id, SKU) == 0
    assert available_qty(session, MERCHANT, SKU) == 5
    assert verify_inventory_balances(session, row.id) is True
