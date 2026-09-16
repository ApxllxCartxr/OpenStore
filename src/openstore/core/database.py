# OpenStore core — database session management with TOCTOU protection (INV-11)

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, and_, create_engine, func, select, text

from openstore.config import Settings
from openstore.models import Checkout, LedgerEntry, LedgerEntryType, OrderState

_engine: Engine | None = None
_engine_url: str | None = None

# SID-2/SID-3: tracks whether the schema exists (via alembic migration on the
# serve path, or create_all in tests/tooling). health readiness keys off this.
_schema_ready: bool = False


def reset_engines() -> None:
    """Dispose all cached engines (tests + process shutdown).

    Legacy tests reset ``_engine = None`` directly; that path still works via
    the URL check in get_engine. New code should call this helper.
    """
    global _engine, _engine_url
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _engine_url = None


def mark_schema_ready() -> None:
    """Set by apply_migrations (serve) or init_database (tests/tooling)."""
    global _schema_ready
    _schema_ready = True


def schema_ready(config: Settings) -> bool:
    return _schema_ready


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql") or url.startswith("postgres")


def get_engine(config: Settings) -> Engine:
    """Get or create database engine.

    Single-merchant processes hold one engine at a time; when the URL changes
    the old engine is disposed first (prevents cross-DB leakage between
    merchant configs sharing a process in tests). Multi-DB components
    (MerchantBot) build their own engines and must not use this cache.
    """
    global _engine, _engine_url
    url = config.database.url
    if _engine is None or _engine_url != url:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
            _engine = None
        if url.startswith("sqlite"):
            # SQLite with BEGIN IMMEDIATE for INV-11 (TOCTOU protection).
            # StaticPool preserves the historical in-memory test behaviour.
            connect_args = {"check_same_thread": False}
            _engine = create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
                echo=False,
            )
            # Enable WAL mode for better concurrency (SQLite only).
            with _engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=5000"))
                conn.commit()
        elif _is_postgres(url):
            from sqlalchemy.pool import QueuePool

            _engine = create_engine(
                url,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=1800,
                echo=False,
            )
        else:
            _engine = create_engine(url, echo=False)
        _engine_url = url

    assert _engine is not None
    return _engine


def init_database(config: Settings) -> None:
    """Initialize database tables."""
    engine = get_engine(config)
    SQLModel.metadata.create_all(engine)
    mark_schema_ready()


def apply_migrations(config: Settings) -> None:
    """SID-3: run Alembic migrations to head at boot (serve path).

    Fails loud on any migration error (R0.5). After success, marks the schema
    ready so /health/ready and the agent-traffic gate can pass (SID-2).
    """
    try:
        import os

        from alembic import command
        from alembic.config import Config

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        ini_path = os.path.join(repo_root, "alembic.ini")

        if not os.path.exists(ini_path):
            raise FileNotFoundError(f"alembic.ini not found at {ini_path}")

        os.environ["OPENSTORE_DB_URL"] = config.database.url

        cfg = Config(ini_path)
        cfg.set_main_option("script_location", os.path.join(repo_root, "alembic"))
        command.upgrade(cfg, "head")
        mark_schema_ready()
    except Exception as exc:
        raise RuntimeError(f"alembic migrations failed: {exc}") from exc


def get_session(config: Settings) -> Session:
    """Get a new database session."""
    engine = get_engine(config)
    return Session(engine)


@contextmanager
def session_scope(config: Settings) -> Generator[Session, None, None]:
    """
    Context manager for database session with automatic commit/rollback.

    INV-11: SQLite uses BEGIN IMMEDIATE for spend-cap TOCTOU protection.
    Postgres relies on row-level SELECT ... FOR UPDATE via lock_policy_row()
    inside the transaction instead of a whole-DB lock.
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
    On Postgres callers should additionally lock the policy row with
    lock_policy_row() after entering this block.
    """
    session = get_session(config)
    try:
        if config.database.url.startswith("sqlite"):
            session.execute(text("BEGIN IMMEDIATE"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def lock_policy_row(session: Session, config: Settings, policy_id: str) -> None:
    """Row-level spend-cap lock for Postgres (INV-11).

    SQLite serialises via BEGIN IMMEDIATE; Postgres must lock only the policy
    row so concurrent checkouts on different policies do not block each other.
    No-op row read on SQLite (lock already held by the IMMEDIATE transaction).
    """
    from openstore.models import IntentPolicy

    q = select(IntentPolicy).where(IntentPolicy.id == policy_id)
    if _is_postgres(config.database.url):
        q = q.with_for_update()
    session.exec(q).first()


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
    # No new LedgerEntry columns. Exposure (not settled spend) is authoritative:
    # in-flight RESERVE legs for open checkouts under this policy also consume the
    # signed budget, so concurrent checkouts cannot exceed the cap (R0.8, TOCTOU).
    spent_minor = compute_policy_exposure(session, policy_id)
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


def compute_policy_exposure(session: Session, policy_id: str) -> int:
    """
    Settled spend PLUS outstanding in-flight RESERVE legs for a policy.

    Counts the economic exposure of a signed policy: value already captured into
    merchant_revenue (minus REFUND), plus the merchant_pending side of every
    outstanding RESERVE whose checkout is still open (CREATED or HELD) under this
    policy. A RESERVE is dual-entry (customer_hold + merchant_pending rows);
    summing only the merchant_pending side counts each reserve exactly once.
    Recomputes server-side (R0.8); never trusts client-supplied totals.
    """
    settled = compute_policy_spend(session, policy_id)

    open_checkout_ids = select(Checkout.id).where(
        and_(
            Checkout.policy_id == policy_id,
            Checkout.state.in_([OrderState.CREATED, OrderState.HELD]),  # type: ignore[attr-defined]
        )
    )
    outstanding = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            and_(
                LedgerEntry.account == "merchant_pending",
                LedgerEntry.currency == "INR",
                LedgerEntry.entry_type == LedgerEntryType.RESERVE,
                LedgerEntry.reference_id.in_(open_checkout_ids),  # type: ignore[attr-defined]
            )
        )
    ).one()

    return int(settled) + int(outstanding)


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
    webauthn_assertion: dict[str, Any] | None = None,
    decision_transcript: list[dict[str, Any]] | None = None,
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
        # Q-050: written here, in the same transaction that creates the
        # checkout, because this is the last point where the authorizing
        # ceremony and its decision are both still in hand.
        webauthn_assertion=webauthn_assertion,
        decision_transcript=decision_transcript,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
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
    checkout.updated_at = datetime.now(UTC).replace(tzinfo=None)

    if psp_order_id:
        checkout.psp_order_id = psp_order_id
    if psp_payment_link_id:
        checkout.psp_payment_link_id = psp_payment_link_id
    if cancel_token:
        checkout.cancel_token = cancel_token

    if new_state == OrderState.HELD:
        # Moving into HELD does not stamp paid_at: no payment has been captured
        # yet at hold time (the payment link is merely awaiting settlement).
        # paid_at is set only when the webhook observes an actual CAPTURE into
        # RELEASED. Stamping it here would fabricate a settlement that has not
        # occurred (R0.8 — never trust/assume settlement).
        pass
    elif new_state == OrderState.RELEASED:
        checkout.released_at = datetime.now(UTC).replace(tzinfo=None)
    elif new_state == OrderState.CANCELLED:
        checkout.cancelled_at = datetime.now(UTC).replace(tzinfo=None)
    elif new_state == OrderState.REFUNDED:
        checkout.cancelled_at = datetime.now(UTC).replace(tzinfo=None)

    session.add(checkout)
    session.flush()

    return checkout
