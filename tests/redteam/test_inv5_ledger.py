# tests/redteam/test_inv5_ledger.py
# INV-5 — The spend ledger is double-entry and reverses (RESERVE -> CAPTURE/RELEASE).
# INV-5a — At every terminal order state the ledger balances to zero (Q-002).
# Red-team: malformed sequences must never report a balanced ledger.

from __future__ import annotations

import pytest
from conftest import seed_checkout, seed_policy
from openstore.core.ledger import (
    LedgerError,
    create_capture_entry,
    create_refund_entry,
    create_release_entry,
    create_reserve_entry,
    get_ledger_balance,
    verify_ledger_balances,
)


def _seed(session):
    pol = seed_policy(session, max_spend_total_minor=100000, max_spend_per_tx_minor=100000)
    ck = seed_checkout(session, amount_minor=40000, policy_id=pol.id)
    session.commit()
    return pol, ck


def test_reserve_capture_balances(session):
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is True


def test_reserve_release_balances(session):
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_release_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is True


def test_reserve_capture_refund_balances(session):
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_refund_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is True


def test_reserve_only_is_not_terminal_balanced(session):
    """INV-5a: a dangling reserve fails the balance invariant."""
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is False


def test_non_authorised_amount_breaks_balance(session):
    """INV-5: capturing a different amount than reserved must break the balance."""
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 25000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is False


def test_release_without_reserve_breaks_balance(session):
    pol, ck = _seed(session)
    create_release_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()
    assert verify_ledger_balances(session, ck.id) is False


def test_negative_amount_rejected(session):
    """R0.5: negative money is rejected loudly, not silently."""
    pol, ck = _seed(session)
    with pytest.raises(LedgerError) as ei:
        create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, -1)
    assert ei.value.reason_code == "policy.qty_invalid"


def test_economic_accounts_consistent_at_terminal(session):
    """INV-5a: after capture the economic leg is booked; escrow nets to zero."""
    pol, ck = _seed(session)
    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
    session.commit()

    assert verify_ledger_balances(session, ck.id) is True
    assert get_ledger_balance(session, "merchant_revenue", reference_id=ck.id) == 40000
    # Platform credit is booked with the opposite sign per the +reserve/-credit convention.
    assert get_ledger_balance(session, "platform", reference_id=ck.id) == -40000
    assert get_ledger_balance(session, "customer_hold", reference_id=ck.id) == 0
