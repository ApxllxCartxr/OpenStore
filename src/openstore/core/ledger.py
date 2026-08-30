# OpenStore core — double-entry ledger (INV-5)

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from openstore.models import LedgerEntry, LedgerEntryType


class LedgerError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


def generate_idempotency_key(prefix: str, trace_id: str, client_id: str, suffix: str) -> str:
    """Generate deterministic idempotency key for ledger entries."""
    return f"{prefix}:{trace_id}:{client_id}:{suffix}"


def create_reserve_entry(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int,
    currency: str = "INR",
    description: str = "Reserve funds for checkout",
) -> LedgerEntry:
    """
    INV-5: Create RESERVE entry (double-entry: customer_hold <-> merchant_pending).
    """
    if amount_minor <= 0:
        raise LedgerError("qty_invalid", "Reserve amount must be positive")

    idempotency_key = generate_idempotency_key("reserve", trace_id, client_id, checkout_id)

    # Check for existing entry (idempotency)
    existing = session.exec(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    ).first()
    if existing:
        return existing

    now = datetime.utcnow()

    # Debit: customer_hold (money held from customer)
    debit = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RESERVE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="customer_hold",
        counterparty_account="merchant_pending",
        idempotency_key=idempotency_key,
        description=description,
        created_at=now,
    )

    # Credit: merchant_pending (money owed to merchant)
    credit = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RESERVE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="merchant_pending",
        counterparty_account="customer_hold",
        idempotency_key=f"{idempotency_key}:counterparty",
        description=description,
        created_at=now,
    )

    session.add(debit)
    session.add(credit)
    session.flush()

    return debit


def create_capture_entry(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int,
    currency: str = "INR",
    description: str = "Capture reserved funds",
) -> LedgerEntry:
    """
    INV-5: Create CAPTURE entry (double-entry: merchant_pending -> merchant_revenue).
    Reverses RESERVE and creates revenue entry.
    """
    if amount_minor <= 0:
        raise LedgerError("qty_invalid", "Capture amount must be positive")

    idempotency_key = generate_idempotency_key("capture", trace_id, client_id, checkout_id)

    existing = session.exec(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    ).first()
    if existing:
        return existing

    now = datetime.utcnow()

    # Reverse RESERVE: release customer_hold
    reserve_reversal = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RELEASE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="customer_hold",
        counterparty_account="merchant_pending",
        idempotency_key=f"{idempotency_key}:reserve_reversal",
        description=f"Release hold for capture: {description}",
        created_at=now,
    )

    # Reverse RESERVE: release merchant_pending
    pending_reversal = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RELEASE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="merchant_pending",
        counterparty_account="customer_hold",
        idempotency_key=f"{idempotency_key}:pending_reversal",
        description=f"Release pending for capture: {description}",
        created_at=now,
    )

    # CAPTURE: merchant_revenue
    capture = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.CAPTURE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="merchant_revenue",
        counterparty_account="platform",
        idempotency_key=idempotency_key,
        description=description,
        created_at=now,
    )

    # Platform fee (if any) - for now 0, just balancing entry
    platform_entry = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.CAPTURE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="platform",
        counterparty_account="merchant_revenue",
        idempotency_key=f"{idempotency_key}:platform",
        description=f"Platform counterpart for: {description}",
        created_at=now,
    )

    session.add_all([reserve_reversal, pending_reversal, capture, platform_entry])
    session.flush()

    return capture


def create_release_entry(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int,
    currency: str = "INR",
    description: str = "Release held funds",
) -> LedgerEntry:
    """
    INV-5: Create RELEASE entry (reverse RESERVE, no capture).
    Returns funds to customer, cancels merchant_pending.
    """
    if amount_minor <= 0:
        raise LedgerError("qty_invalid", "Release amount must be positive")

    idempotency_key = generate_idempotency_key("release", trace_id, client_id, checkout_id)

    existing = session.exec(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    ).first()
    if existing:
        return existing

    now = datetime.utcnow()

    # Release customer_hold
    customer_release = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RELEASE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="customer_hold",
        counterparty_account="merchant_pending",
        idempotency_key=idempotency_key,
        description=description,
        created_at=now,
    )

    # Release merchant_pending
    merchant_release = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.RELEASE,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="merchant_pending",
        counterparty_account="customer_hold",
        idempotency_key=f"{idempotency_key}:counterparty",
        description=description,
        created_at=now,
    )

    session.add_all([customer_release, merchant_release])
    session.flush()

    return customer_release


def create_refund_entry(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int,
    currency: str = "INR",
    description: str = "Refund captured funds",
) -> LedgerEntry:
    """
    INV-5: Create REFUND entry (reverse CAPTURE).
    Moves from merchant_revenue back to customer.
    """
    if amount_minor <= 0:
        raise LedgerError("qty_invalid", "Refund amount must be positive")

    idempotency_key = generate_idempotency_key("refund", trace_id, client_id, checkout_id)

    existing = session.exec(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    ).first()
    if existing:
        return existing

    now = datetime.utcnow()

    # Reverse merchant_revenue
    revenue_reversal = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.REFUND,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="merchant_revenue",
        counterparty_account="customer_refund",
        idempotency_key=idempotency_key,
        description=description,
        created_at=now,
    )

    # Customer refund
    customer_refund = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.REFUND,
        amount_minor=amount_minor,
        currency=currency,
        reference_id=checkout_id,
        account="customer_refund",
        counterparty_account="merchant_revenue",
        idempotency_key=f"{idempotency_key}:counterparty",
        description=description,
        created_at=now,
    )

    session.add_all([revenue_reversal, customer_refund])
    session.flush()

    return revenue_reversal


def get_ledger_balance(
    session: Session,
    account: str,
    trace_id: str | None = None,
    client_id: str | None = None,
    reference_id: str | None = None,
) -> int:
    """Get current balance for an account (sum of debits - credits)."""
    query = select(LedgerEntry).where(LedgerEntry.account == account)

    if trace_id:
        query = query.where(LedgerEntry.trace_id == trace_id)
    if client_id:
        query = query.where(LedgerEntry.client_id == client_id)
    if reference_id:
        query = query.where(LedgerEntry.reference_id == reference_id)

    entries = session.exec(query).all()

    balance = 0
    for entry in entries:
        if entry.entry_type in (LedgerEntryType.RESERVE, LedgerEntryType.CAPTURE):
            # These are debits for customer_hold, credits for merchant_pending/revenue
            if entry.account in ("customer_hold", "merchant_revenue"):
                balance += entry.amount_minor
            else:
                balance -= entry.amount_minor
        elif entry.entry_type in (LedgerEntryType.RELEASE, LedgerEntryType.REFUND):
            if entry.account in ("customer_hold", "merchant_revenue"):
                balance -= entry.amount_minor
            else:
                balance += entry.amount_minor

    return balance


def verify_ledger_balances(session: Session, reference_id: str) -> bool:
    """
    INV-5: Verify double-entry balances for a checkout.
    Sum of all entries for a reference_id should be zero (balanced).
    """
    entries = session.exec(
        select(LedgerEntry).where(LedgerEntry.reference_id == reference_id)
    ).all()

    account_balances: dict[str, int] = {}
    for entry in entries:
        if entry.account not in account_balances:
            account_balances[entry.account] = 0

        if entry.entry_type in (LedgerEntryType.RESERVE, LedgerEntryType.CAPTURE):
            account_balances[entry.account] += entry.amount_minor
        else:
            account_balances[entry.account] -= entry.amount_minor

    # All accounts should balance to zero
    return all(balance == 0 for balance in account_balances.values())
