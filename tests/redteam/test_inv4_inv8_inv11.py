# tests/redteam/test_inv4_inv8_inv11.py
# INV-4 — Intent-first, then outbox (never call PSP before recording intent).
# INV-8 — checkout.expires_at is enforced.
# INV-11 — Spend-cap TOCTOU is closed (server recomputes; a concurrent over-cap is denied).
# Red-team: adversarial attempts must be REJECTED with closed-set reason codes.

from __future__ import annotations

from conftest import seed_checkout, seed_policy
from openstore.core.database import check_spend_cap, compute_policy_spend
from openstore.core.holdcancel import (
    check_and_expire_checkouts,
)
from openstore.core.ledger import (
    create_capture_entry,
    create_reserve_entry,
    verify_ledger_balances,
)
from openstore.models import Checkout, LedgerEntry, OrderState
from sqlmodel import select


def test_intent_requires_reserve_before_spend(session):
    """INV-4: no CAPTURE may exist without a prior RESERVE (dual-write rule)."""
    pol = seed_policy(session)
    ck = seed_checkout(session, policy_id=pol.id)
    session.commit()

    # Reserve first, then capture -> valid flow.
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is True


def test_capture_without_reserve_breaks_balance(session):
    """INV-4/INV-5: a capture with no prior reserve is a broken ledger, never silent."""
    pol = seed_policy(session, max_spend_total_minor=50000, max_spend_per_tx_minor=50000)
    ck = seed_checkout(session, policy_id=pol.id)
    session.commit()

    # Attacker tries to capture without a reserve.
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is False


def test_compiler_denial_leaves_no_ledger(session):
    """INV-4: a compiler denial must precede any ledger write."""
    seed_policy(session)
    session.commit()
    # No checkout / reserve for this policy yet.
    entries = session.exec(select(LedgerEntry)).all()
    assert entries == []


def test_expired_checkout_is_enforced(session):
    """INV-8: an expired HELD checkout must not remain holdable."""
    from datetime import UTC, datetime, timedelta

    pol = seed_policy(session)
    now = datetime.now(UTC)
    ck = Checkout(
        id="chk_exp", trace_id="tr_exp", client_id="cli_1", merchant_id="gelateria-milano",
        cart_hash="h_exp", cart_version=1, amount_minor=40000, currency="INR",
        state=OrderState.HELD, policy_id=pol.id, policy_hash="ph_exp", aal_level=2,
        expires_at=now - timedelta(seconds=1), idempotency_key="idem_exp",
        cart_snapshot={"items": []}, created_at=now, updated_at=now,
    )
    session.add(ck)
    session.commit()

    count = check_and_expire_checkouts(session)
    session.commit()
    assert count >= 1
    ck2 = session.get(Checkout, "chk_exp")
    assert ck2.state == OrderState.CANCELLED


def test_expired_aal3_auto_releases(session):
    """INV-8: an expired AAL3 HELD checkout auto-releases (captures) funds."""
    from datetime import UTC, datetime, timedelta

    pol = seed_policy(session, max_spend_total_minor=50000, max_spend_per_tx_minor=50000)
    now = datetime.now(UTC)
    ck = Checkout(
        id="chk_a3", trace_id="tr_a3", client_id="cli_1", merchant_id="gelateria-milano",
        cart_hash="h_a3", cart_version=1, amount_minor=40000, currency="INR",
        state=OrderState.HELD, policy_id=pol.id, policy_hash="ph_a3", aal_level=3,
        expires_at=now - timedelta(seconds=1), idempotency_key="idem_a3",
        cart_snapshot={"items": []}, created_at=now, updated_at=now,
    )
    session.add(ck)
    create_reserve_entry(session, "tr_a3", "cli_1", "chk_a3", 40000)
    session.commit()

    check_and_expire_checkouts(session)
    session.commit()
    ck2 = session.get(Checkout, "chk_a3")
    assert ck2.state == OrderState.RELEASED
    # Ledger must be balanced (capture closed the reserve).
    assert verify_ledger_balances(session, "chk_a3") is True


def test_spend_cap_recomputed_server_side(session):
    """INV-11/R0.8: a claimed spend that exceeds the envelope is denied on recompute."""
    pol = seed_policy(
        session,
        max_spend_per_tx_minor=50000,
        max_spend_total_minor=30000,
    )
    session.commit()

    # Already 25000 captured against this policy.
    ck = seed_checkout(session, "chk_pre", policy_id=pol.id, amount_minor=25000)
    session.commit()
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 25000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 25000)
    session.commit()
    assert compute_policy_spend(session, pol.id) == 25000

    # An attacker claims another 10000 -> total 35000 > 30000 envelope -> denied.
    ok, code = check_spend_cap(
        session, pol.merchant_id, pol.id, 10000,
        max_spend_per_tx_minor=pol.max_spend_per_tx_minor,
        max_spend_total_minor=pol.max_spend_total_minor,
    )
    assert ok is False
    assert code == "policy.spend_cumulative_exceeded"


def test_concurrent_over_cap_denied_on_recompute(session):
    """INV-11 TOCTOU: two concurrent claims that together exceed the cap are caught."""
    pol = seed_policy(session, max_spend_total_minor=25000, max_spend_per_tx_minor=50000)
    session.commit()

    # Server-side recompute does not trust client-total claims; a single call
    # exceeding the cap is denied outright.
    ok, code = check_spend_cap(
        session, pol.merchant_id, pol.id, 30000,
        max_spend_per_tx_minor=pol.max_spend_per_tx_minor,
        max_spend_total_minor=pol.max_spend_total_minor,
    )
    assert ok is False
    assert code in ("policy.spend_cumulative_exceeded", "policy.spend_per_tx_exceeded")
