# tests/test_ledger_properties.py
# Property-based checks for INV-5a (double-entry ledger). Golden vectors in
# test_ledger_golden.py pin fixed examples; this file checks the invariant
# holds over generated amounts/sequences instead of hand-picked numbers.

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st
from openstore.core.ledger import (
    create_capture_entry,
    create_refund_entry,
    create_release_entry,
    create_reserve_entry,
    get_ledger_balance,
    verify_ledger_balances,
)
from openstore.models import LedgerEntry, LedgerEntryType
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

AMOUNT = st.integers(min_value=1, max_value=10_000_000)


def _fresh_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@given(amount=AMOUNT)
@settings(max_examples=100)
def test_reserve_then_capture_balances_for_any_amount(amount: int):
    session = _fresh_session()
    ref = "ref-capture"
    create_reserve_entry(session, "t", "c", ref, amount)
    create_capture_entry(session, "t", "c", ref, amount)

    assert verify_ledger_balances(session, ref) is True
    assert get_ledger_balance(session, "merchant_revenue", reference_id=ref) == amount
    assert get_ledger_balance(session, "customer_hold", reference_id=ref) == 0
    assert get_ledger_balance(session, "merchant_pending", reference_id=ref) == 0


@given(amount=AMOUNT)
@settings(max_examples=100)
def test_reserve_then_release_balances_for_any_amount(amount: int):
    session = _fresh_session()
    ref = "ref-release"
    create_reserve_entry(session, "t", "c", ref, amount)
    create_release_entry(session, "t", "c", ref, amount)

    assert verify_ledger_balances(session, ref) is True
    assert get_ledger_balance(session, "customer_hold", reference_id=ref) == 0
    assert get_ledger_balance(session, "merchant_pending", reference_id=ref) == 0
    assert get_ledger_balance(session, "merchant_revenue", reference_id=ref) == 0


@given(amount=AMOUNT)
@settings(max_examples=100)
def test_reserve_capture_refund_balances_for_any_amount(amount: int):
    session = _fresh_session()
    ref = "ref-refund"
    create_reserve_entry(session, "t", "c", ref, amount)
    create_capture_entry(session, "t", "c", ref, amount)
    create_refund_entry(session, "t", "c", ref, amount)

    assert verify_ledger_balances(session, ref) is True
    assert get_ledger_balance(session, "merchant_revenue", reference_id=ref) == 0


@given(amount=AMOUNT)
@settings(max_examples=50)
def test_reserve_without_terminal_step_is_unbalanced(amount: int):
    """A hanging RESERVE (no capture/release) must NOT verify — escrow is still open."""
    session = _fresh_session()
    ref = "ref-hanging"
    create_reserve_entry(session, "t", "c", ref, amount)

    assert verify_ledger_balances(session, ref) is False


@given(amount=AMOUNT)
@settings(max_examples=50)
def test_reserve_is_idempotent_under_retry(amount: int):
    """Calling create_reserve_entry twice with the same key must not double the balance."""
    session = _fresh_session()
    ref = "ref-idempotent"
    create_reserve_entry(session, "t", "c", ref, amount)
    create_reserve_entry(session, "t", "c", ref, amount)  # retry, e.g. after a network blip

    assert get_ledger_balance(session, "customer_hold", reference_id=ref) == amount


def test_verify_ledger_balances_checks_customer_hold_independently():
    """
    Each escrow account (customer_hold, merchant_pending) must be checked
    independently — a corruption that zeroes out merchant_pending while
    leaving customer_hold non-zero must still fail verification. (A prior
    mutation-testing pass found this branch could be silently disabled while
    an unrelated leg still happened to catch the simpler "hanging reserve"
    case, masking the gap.)
    """
    session = _fresh_session()
    ref = "ref-asymmetric"
    now = datetime.now(UTC).replace(tzinfo=None)

    # customer_hold is short (money still held, unbalanced); merchant_pending
    # nets to zero on its own — an internally inconsistent, corrupted state.
    session.add(
        LedgerEntry(
            trace_id="t",
            client_id="c",
            entry_type=LedgerEntryType.RESERVE,
            amount_minor=500,
            currency="INR",
            reference_id=ref,
            account="customer_hold",
            counterparty_account="merchant_pending",
            idempotency_key="corrupt:1",
            description="corrupted reserve leg",
            created_at=now,
        )
    )
    session.flush()

    assert verify_ledger_balances(session, ref) is False


@given(amount=st.integers(max_value=0))
@settings(max_examples=25)
def test_non_positive_amounts_are_rejected(amount: int):
    from openstore.core.ledger import LedgerError

    session = _fresh_session()
    try:
        create_reserve_entry(session, "t", "c", "ref-neg", amount)
        raised = False
    except LedgerError:
        raised = True
    assert raised, f"amount_minor={amount} should have been rejected"
