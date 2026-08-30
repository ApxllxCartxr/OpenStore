# OpenStore core — database session management with TOCTOU protection (INV-11)

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, and_, create_engine, func, select, text

from openstore.config import Settings
from openstore.models import Checkout, LedgerEntry, LedgerEntryType, OrderState

_engine: Engine | None = None


def get_engine(config: Settings) -> Engine:
    """Get or create database engine."""
    global _engine
    if _engine is None:
        # SQLite with BEGIN IMMEDIATE for INV-11 (TOCTOU protection)
        connect_args = {"check_same_thread": False}
        if config.database.url.startswith("sqlite"):
            # Use StaticPool for SQLite in-memory/testing
            _engine = create_engine(
                config.database.url,
                connect_args=connect_args,
                poolclass=StaticPool,
                echo=False,
            )
        else:
            _engine = create_engine(config.database.url, echo=False)

        # Enable WAL mode for better concurrency
        with _engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=5000"))
            conn.commit()

    return _engine


def init_database(config: Settings) -> None:
    """Initialize database tables."""
    engine = get_engine(config)
    SQLModel.metadata.create_all(engine)


def get_session(config: Settings) -> Session:
    """Get a new database session."""
    engine = get_engine(config)
    return Session(engine)


@contextmanager
def session_scope(config: Settings) -> Generator[Session, None, None]:
    """
    Context manager for database session with automatic commit/rollback.

    INV-11: Uses BEGIN IMMEDIATE for spend-cap TOCTOU protection.
    """
    session = get_session(config)
    try:
        # Start transaction with IMMEDIATE lock for SQLite (INV-11)
        if config.database.url.startswith("sqlite"):
            session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def immediate_session(config: Settings) -> Generator[Session, None, None]:
    """
    Explicit IMMEDIATE transaction for spend-cap operations (INV-11).

    Use this for any operation that checks and updates spend caps.
    """
    session = get_session(config)
    try:
        if config.database.url.startswith("sqlite"):
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            session.begin()
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_spend_cap(
    session: Session,
    merchant_id: str,
    policy_id: str,
    amount_minor: int,
    max_spend_per_tx_minor: int,
    max_spend_total_minor: int,
) -> tuple[bool, str]:
    """
    INV-11: Check spend cap with TOCTOU protection.

    Must be called within an IMMEDIATE transaction.
    Returns (allowed, reason_code).
    """
    # Check per-transaction limit
    if amount_minor > max_spend_per_tx_minor:
        return False, "policy.spend_per_tx_exceeded"

    # Check cumulative spend (only CAPTURE legs, minus REFUND, for this policy).
    # §3.2c (Q-003): scope via LedgerEntry.reference_id -> Checkout.policy_id join,
    # restricted to Checkout rows whose policy_id equals the policy under evaluation.
    # No new LedgerEntry columns.
    spent_minor = compute_policy_spend(session, policy_id)
    if spent_minor + amount_minor > max_spend_total_minor:
        return False, "policy.spend_cumulative_exceeded"

    return True, ""


def compute_policy_spend(session: Session, policy_id: str) -> int:
    """
    §3.2c (Q-003): sum of CAPTURE legs (minus REFUND/RELEASE) for a policy,
    via LedgerEntry.reference_id -> Checkout.policy_id.
    Only CAPTURE moves value into merchant_revenue; RELEASE touches escrow only,
    so it never decreases the economic spend. Doctrine: calculate server-side (R0.8).
    """
    policy_checkout_ids = select(Checkout.id).where(Checkout.policy_id == policy_id)

    captured = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            and_(
                LedgerEntry.account == "merchant_revenue",
                LedgerEntry.currency == "INR",
                LedgerEntry.entry_type == LedgerEntryType.CAPTURE,
                LedgerEntry.reference_id.in_(policy_checkout_ids),  # type: ignore[attr-defined]
            )
        )
    ).one()

    refunded = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            and_(
                LedgerEntry.account == "merchant_revenue",
                LedgerEntry.currency == "INR",
                LedgerEntry.entry_type == LedgerEntryType.REFUND,
                LedgerEntry.reference_id.in_(policy_checkout_ids),  # type: ignore[attr-defined]
            )
        )
    ).one()

    return int(captured) - int(refunded)


def get_or_create_checkout(
    session: Session,
    checkout_id: str,
    trace_id: str,
    client_id: str,
    merchant_id: str,
    cart_hash: str,
    cart_version: int,
    amount_minor: int,
    currency: str,
    policy_id: str | None,
    policy_hash: str | None,
    aal_level: int,
    expires_at: datetime,
    idempotency_key: str,
    cart_snapshot: dict[str, Any],
    agent_plan: dict[str, Any] | None = None,
) -> tuple[Checkout, bool]:
    """
    Get existing checkout or create new one (idempotent).
    Returns (checkout, created).
    """
    existing = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if existing:
        return existing, False

    checkout = Checkout(
        id=checkout_id,
        trace_id=trace_id,
        client_id=client_id,
        merchant_id=merchant_id,
        cart_hash=cart_hash,
        cart_version=cart_version,
        amount_minor=amount_minor,
        currency=currency,
        state=OrderState.CREATED,
        policy_id=policy_id,
        policy_hash=policy_hash,
        aal_level=aal_level,
        expires_at=expires_at,
        idempotency_key=idempotency_key,
        cart_snapshot=cart_snapshot,
        agent_plan=agent_plan,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    session.add(checkout)
    session.flush()

    return checkout, True


def update_checkout_state(
    session: Session,
    checkout_id: str,
    new_state: OrderState,
    psp_order_id: str | None = None,
    psp_payment_link_id: str | None = None,
    cancel_token: str | None = None,
) -> Checkout:
    """Update checkout state with audit trail."""
    checkout = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if not checkout:
        raise ValueError(f"Checkout not found: {checkout_id}")

    checkout.state = new_state
    checkout.updated_at = datetime.utcnow()

    if psp_order_id:
        checkout.psp_order_id = psp_order_id
    if psp_payment_link_id:
        checkout.psp_payment_link_id = psp_payment_link_id
    if cancel_token:
        checkout.cancel_token = cancel_token

    if new_state == OrderState.HELD:
        checkout.paid_at = datetime.utcnow()
    elif new_state == OrderState.RELEASED:
        checkout.released_at = datetime.utcnow()
    elif new_state == OrderState.CANCELLED:
        checkout.cancelled_at = datetime.utcnow()
    elif new_state == OrderState.REFUNDED:
        checkout.cancelled_at = datetime.utcnow()

    session.add(checkout)
    session.flush()

    return checkout
