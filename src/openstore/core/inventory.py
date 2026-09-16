# OpenStore core — inventory ledger (S26: real stock, no oversell).
#
# The money ledger's discipline transcribed for quantities (DECISION-047's
# soundness core): movements are rows, never in-place decrements, with
# deterministic idempotency keys and a per-checkout balance invariant.
# Units move between STATES, so every movement is a double-entry pair:
#   RESERVE  available -> reserved   (checkout hold)
#   COMMIT   reserved  -> sold       (paid / captured)
#   RELEASE  reserved  -> available  (cancel / failure / expiry)
#   RESTOCK  sold      -> available  (refund)
# Quantities are always positive; the sign is implied by entry_type, exactly
# like amount_minor. verify_inventory_balances mirrors verify_ledger_balances
# (INV-5a shape): `reserved` nets to zero per reference_id at every terminal
# state, `sold` stays non-negative. `available` legs exist for the movement
# audit trail only — platform truth lives on inventory_items (sync-maintained),
# never in per-checkout legs, so `available` carries no per-reference invariant.
#
# Session discipline (plan risk #1): every helper takes the CALLER's session.
# Opening a nested Session here would share (and on close roll back) the
# caller's connection under SQLite's StaticPool — see
# core/settings_overlay.py::effective_settings.

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import (
    InventoryEntryType,
    InventoryItem,
    InventoryLedgerEntry,
    InventoryWriteback,
    InventoryWritebackStatus,
)

#: Alert types raised through notifier.sync_alert (log-only shim, no new codes).
ALERT_DRIFT = "inventory.drift_below_reservations"
ALERT_LOW_STOCK = "inventory.low_stock"
ALERT_WRITEBACK_FAILED = "inventory.writeback_failed"

#: Threshold seeded for SKUs the sync loop newly brings under management.
DEFAULT_LOW_STOCK_THRESHOLD = 5

_RESERVED = "reserved"
_SOLD = "sold"
_AVAILABLE = "available"

#: Entry types that ADD to their account leg (mirrors ledger.py's
#: RESERVE/CAPTURE convention; RELEASE/REFUND subtract there, RELEASE/RESTOCK here).
_ADD_TYPES = (InventoryEntryType.RESERVE, InventoryEntryType.COMMIT)


class InventoryError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _idem(prefix: str, trace_id: str, client_id: str, reference_id: str, sku: str) -> str:
    return f"inv-{prefix}:{trace_id}:{client_id}:{reference_id}:{sku}"


def _get_row(session: Session, merchant_id: str, sku: str) -> InventoryItem | None:
    return session.exec(
        select(InventoryItem).where(
            InventoryItem.merchant_id == merchant_id, InventoryItem.sku == sku
        )
    ).first()


def lock_inventory_row(session: Session, config: Settings, merchant_id: str, sku: str) -> None:
    """Row-level oversell lock for Postgres (race-safety half of the gate).

    Mirrors database.lock_policy_row: SQLite serialises via BEGIN IMMEDIATE
    (the caller runs inside immediate_session / session_scope); Postgres must
    lock only this SKU's row so concurrent checkouts on different SKUs do not
    block each other. No-op row read on SQLite (lock already held).
    """
    q = select(InventoryItem).where(
        InventoryItem.merchant_id == merchant_id, InventoryItem.sku == sku
    )
    if config.database.url.startswith("postgres"):
        q = q.with_for_update()
    session.exec(q).first()


def _add_legs(
    session: Session,
    *,
    trace_id: str,
    client_id: str,
    reference_id: str,
    merchant_id: str,
    sku: str,
    quantity: int,
    entry_type: InventoryEntryType,
    debit_account: str,
    credit_account: str,
    description: str,
) -> None:
    """Write one double-entry movement pair, idempotent on the debit leg."""
    if quantity <= 0:
        raise InventoryError("policy.qty_invalid", "Inventory movement quantity must be positive")
    key = _idem(entry_type.value.lower(), trace_id, client_id, reference_id, sku)
    existing = session.exec(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.idempotency_key == key)
    ).first()
    if existing:
        return
    now = _now()
    session.add(
        InventoryLedgerEntry(
            trace_id=trace_id,
            client_id=client_id,
            entry_type=entry_type,
            quantity=quantity,
            merchant_id=merchant_id,
            sku=sku,
            reference_id=reference_id,
            account=debit_account,
            counterparty_account=credit_account,
            idempotency_key=key,
            description=description,
            created_at=now,
        )
    )
    session.add(
        InventoryLedgerEntry(
            trace_id=trace_id,
            client_id=client_id,
            entry_type=entry_type,
            quantity=quantity,
            merchant_id=merchant_id,
            sku=sku,
            reference_id=reference_id,
            account=credit_account,
            counterparty_account=debit_account,
            idempotency_key=f"{key}:counterparty",
            description=description,
            created_at=now,
        )
    )
    session.flush()


def create_reserve_entries(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    merchant_id: str,
    sku: str,
    quantity: int,
) -> None:
    """RESERVE available -> reserved (checkout hold). Idempotent."""
    _add_legs(
        session,
        trace_id=trace_id,
        client_id=client_id,
        reference_id=checkout_id,
        merchant_id=merchant_id,
        sku=sku,
        quantity=quantity,
        entry_type=InventoryEntryType.RESERVE,
        debit_account=_RESERVED,
        credit_account=_AVAILABLE,
        description=f"Reserve {quantity}x {sku} for checkout {checkout_id}",
    )


def create_commit_entries(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    merchant_id: str,
    sku: str,
    quantity: int,
) -> None:
    """COMMIT reserved -> sold (paid / captured). Idempotent.

    Mirrors the money CAPTURE's reversal discipline: the reservation is
    extinguished with a RELEASE-typed leg (so `reserved` nets to zero per
    reference) and the value lands on `sold` with a COMMIT-typed leg.
    """
    if quantity <= 0:
        raise InventoryError("policy.qty_invalid", "Inventory movement quantity must be positive")
    key = _idem("commit", trace_id, client_id, checkout_id, sku)
    existing = session.exec(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.idempotency_key == key)
    ).first()
    if existing:
        return
    now = _now()
    session.add(
        InventoryLedgerEntry(
            trace_id=trace_id,
            client_id=client_id,
            entry_type=InventoryEntryType.RELEASE,
            quantity=quantity,
            merchant_id=merchant_id,
            sku=sku,
            reference_id=checkout_id,
            account=_RESERVED,
            counterparty_account=_SOLD,
            idempotency_key=key,
            description=f"Commit extinguishes reservation: {quantity}x {sku} for {checkout_id}",
            created_at=now,
        )
    )
    session.add(
        InventoryLedgerEntry(
            trace_id=trace_id,
            client_id=client_id,
            entry_type=InventoryEntryType.COMMIT,
            quantity=quantity,
            merchant_id=merchant_id,
            sku=sku,
            reference_id=checkout_id,
            account=_SOLD,
            counterparty_account=_RESERVED,
            idempotency_key=f"{key}:counterparty",
            description=f"Commit {quantity}x {sku} sold for {checkout_id}",
            created_at=now,
        )
    )
    session.flush()


def create_release_entries(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    merchant_id: str,
    sku: str,
    quantity: int,
) -> None:
    """RELEASE reserved -> available (cancel / failure / expiry). Idempotent."""
    _add_legs(
        session,
        trace_id=trace_id,
        client_id=client_id,
        reference_id=checkout_id,
        merchant_id=merchant_id,
        sku=sku,
        quantity=quantity,
        entry_type=InventoryEntryType.RELEASE,
        debit_account=_RESERVED,
        credit_account=_AVAILABLE,
        description=f"Release {quantity}x {sku} from checkout {checkout_id}",
    )


def create_restock_entries(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    merchant_id: str,
    sku: str,
    quantity: int,
) -> None:
    """RESTOCK sold -> available (refund). Idempotent."""
    _add_legs(
        session,
        trace_id=trace_id,
        client_id=client_id,
        reference_id=checkout_id,
        merchant_id=merchant_id,
        sku=sku,
        quantity=quantity,
        entry_type=InventoryEntryType.RESTOCK,
        debit_account=_SOLD,
        credit_account=_AVAILABLE,
        description=f"Restock {quantity}x {sku} from refunded checkout {checkout_id}",
    )


def _signed_qty(session: Session, merchant_id: str, sku: str, account: str) -> int:
    """Net units on one account for one SKU: ADD-types add, others subtract."""
    rows = session.exec(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.merchant_id == merchant_id,
            InventoryLedgerEntry.sku == sku,
            InventoryLedgerEntry.account == account,
        )
    ).all()
    total = 0
    for row in rows:
        total += row.quantity if row.entry_type in _ADD_TYPES else -row.quantity
    return total


def _ref_signed_qty(session: Session, reference_id: str, sku: str, account: str) -> int:
    rows = session.exec(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.reference_id == reference_id,
            InventoryLedgerEntry.sku == sku,
            InventoryLedgerEntry.account == account,
        )
    ).all()
    total = 0
    for row in rows:
        total += row.quantity if row.entry_type in _ADD_TYPES else -row.quantity
    return total


def compute_sku_exposure(session: Session, merchant_id: str, sku: str) -> int:
    """Outstanding reserved units for a SKU: RESERVE legs minus the RELEASE /
    COMMIT-extinguishing legs. Terminal checkouts net to zero by construction,
    so no state join is needed (and none is wanted: a missing extinguishing
    leg must stay visible here, not be filtered away)."""
    return _signed_qty(session, merchant_id, sku, _RESERVED)


def outstanding_for_checkout(session: Session, checkout_id: str, sku: str) -> int:
    """Reserved units of one SKU still held by one checkout (guard input for
    the COMMIT / RELEASE hooks: both reconciliation paths may fire, and a
    CREATED-expired checkout never reserved anything — releasing that would
    mint an unbalanced leg, the money path's _apply_payment_failed guard)."""
    return max(0, _ref_signed_qty(session, checkout_id, sku, _RESERVED))


def committed_for_checkout(session: Session, checkout_id: str, sku: str) -> int:
    """Sold units of one SKU from one checkout not yet restocked (guard input
    for the RESTOCK hook)."""
    return max(0, _ref_signed_qty(session, checkout_id, sku, _SOLD))


def available_qty(session: Session, merchant_id: str, sku: str) -> int | None:
    """Sellable units right now, or None when unmanaged.

    available = platform_truth - outstanding_reservations - sold_not_yet_pushed.
    The third term closes the COMMIT-to-platform-update window: a paid unit
    must stay ungatable until the platform itself reflects the sale (via our
    write-back or its own order hook). Unpushed units are derived from the
    write-back queue (PENDING/FAILED deltas), never a counter — delivered
    intents stop counting exactly when the platform truth they produced can
    be observed, so nothing double-counts.

    None covers three distinct unmanaged states — no inventory row at all, a
    tracked=false row, and a tracked row whose platform truth never synced —
    and must NEVER be coerced to 0 (stage-25 stock discipline). A drifted row
    (platform truth below outstanding reservations) reports 0: its truth is
    untrustworthy, so new sales block until the sync clears it.
    """
    row = _get_row(session, merchant_id, sku)
    if row is None or not row.tracked:
        return None
    if row.drifted:
        return 0
    if row.last_platform_qty is None:
        return None
    return max(
        0,
        row.last_platform_qty
        - compute_sku_exposure(session, merchant_id, sku)
        - _unpushed_sold(session, merchant_id, sku),
    )


def _unpushed_sold(session: Session, merchant_id: str, sku: str) -> int:
    """Units sold (COMMIT) or returned (RESTOCK) but not yet platform-pushed:
    minus the sum of PENDING/FAILED write-back deltas (deltas are negative
    for sales, positive for restocks), clamped at zero."""
    intents = session.exec(
        select(InventoryWriteback).where(
            InventoryWriteback.merchant_id == merchant_id,
            InventoryWriteback.sku == sku,
            InventoryWriteback.status.in_(  # type: ignore[attr-defined]
                [InventoryWritebackStatus.PENDING, InventoryWritebackStatus.FAILED]
            ),
        )
    ).all()
    return max(0, -sum(i.quantity for i in intents))


def check_stock_available(session: Session, merchant_id: str, sku: str, quantity: int) -> bool:
    """The gate predicate: True when `quantity` units may be promised now."""
    if quantity <= 0:
        return False
    available = available_qty(session, merchant_id, sku)
    if available is None:
        return True
    return available >= quantity


def aggregate_lines(cart_items: list[dict[str, Any]]) -> dict[str, int]:
    """Sum cart lines by SKU (the pre-compiler gate runs before the
    sku_duplicate check, so two lines sharing a SKU must be gated jointly)."""
    totals: dict[str, int] = {}
    for item in cart_items:
        sku = str(item.get("sku", ""))
        qty = int(item.get("qty", 0) or 0)
        if sku and qty > 0:
            totals[sku] = totals.get(sku, 0) + qty
    return totals


def gate_checkout_stock(
    session: Session,
    config: Settings,
    merchant_id: str,
    cart_items: list[dict[str, Any]],
) -> None:
    """Pre-compiler oversell gate (DECISION-047). Raises CommerceError
    (inventory.insufficient_stock) before the CompilerContext is constructed.

    Runs in the caller's transaction (same session as initiate_hold's
    immediate_session downstream); on Postgres each tracked SKU row is locked
    FOR UPDATE so two concurrent gates serialize on the row, not on each
    other's reads.
    """
    from openstore.core.api import CommerceError

    for sku, qty in aggregate_lines(cart_items).items():
        lock_inventory_row(session, config, merchant_id, sku)
        if not check_stock_available(session, merchant_id, sku, qty):
            raise CommerceError(
                "inventory.insufficient_stock",
                f"Insufficient stock for SKU {sku!r}: requested {qty}",
                400,
            )


def reserve_checkout_stock(
    session: Session,
    config: Settings,
    merchant_id: str,
    cart_items: list[dict[str, Any]],
    trace_id: str,
    client_id: str,
    checkout_id: str,
) -> None:
    """RESERVE every gated line inside the same transaction as initiate_hold.

    Re-checks availability under the row lock immediately before writing (the
    pre-compiler gate and this write are separate statements; TOCTOU between
    them closes here, not at the gate). Unmanaged SKUs write no legs at all —
    no inventory mutation happens outside a tracked SKU's ledger entries.
    """
    from openstore.core.api import CommerceError

    for sku, qty in aggregate_lines(cart_items).items():
        lock_inventory_row(session, config, merchant_id, sku)
        row = _get_row(session, merchant_id, sku)
        if row is None or not row.tracked:
            continue
        if not check_stock_available(session, merchant_id, sku, qty):
            raise CommerceError(
                "inventory.insufficient_stock",
                f"Insufficient stock for SKU {sku!r}: requested {qty}",
                400,
            )
        create_reserve_entries(session, trace_id, client_id, checkout_id, merchant_id, sku, qty)


def commit_checkout_stock(
    session: Session,
    merchant_id: str,
    checkout_id: str,
    cart_items: list[dict[str, Any]],
    trace_id: str,
    client_id: str,
) -> None:
    """COMMIT beside every money CAPTURE. Commits only the outstanding
    reservation per line (both reconciliation paths may fire; the second is a
    no-op), then queues one platform write-back intent per line (negative
    delta: these units are sold but not yet platform-pushed)."""
    for sku in aggregate_lines(cart_items):
        outstanding = outstanding_for_checkout(session, checkout_id, sku)
        if outstanding <= 0:
            continue
        create_commit_entries(session, trace_id, client_id, checkout_id, merchant_id, sku, outstanding)
        _queue_writeback(session, merchant_id, checkout_id, sku, -outstanding)


def release_checkout_stock(
    session: Session,
    merchant_id: str,
    checkout_id: str,
    cart_items: list[dict[str, Any]],
    trace_id: str,
    client_id: str,
) -> None:
    """RELEASE on cancel / failure / expiry. Releases only the outstanding
    reservation per line (CREATED-expired checkouts hold nothing; the second
    reconciliation path finds nothing left to release)."""
    for sku in aggregate_lines(cart_items):
        outstanding = outstanding_for_checkout(session, checkout_id, sku)
        if outstanding <= 0:
            continue
        create_release_entries(
            session, trace_id, client_id, checkout_id, merchant_id, sku, outstanding
        )


def restock_checkout_stock(
    session: Session,
    merchant_id: str,
    checkout_id: str,
    cart_items: list[dict[str, Any]],
    trace_id: str,
    client_id: str,
) -> None:
    """RESTOCK on refund. Restocks only committed-but-unrestocked units, and
    queues the compensating positive-delta write-back (the platform must hear
    about the return exactly like it heard about the sale)."""
    for sku in aggregate_lines(cart_items):
        committed = committed_for_checkout(session, checkout_id, sku)
        if committed <= 0:
            continue
        create_restock_entries(
            session, trace_id, client_id, checkout_id, merchant_id, sku, committed
        )
        _queue_writeback(session, merchant_id, checkout_id, sku, committed)


def _queue_writeback(
    session: Session, merchant_id: str, checkout_id: str, sku: str, delta: int
) -> None:
    """One write-back intent per (checkout, SKU, delta), same transaction as
    the COMMIT/RESTOCK (exactly-once intent). Negative delta = sold units the
    platform has not observed yet; positive = refunded units to return.
    Delivery resolves the absolute platform quantity from fresh truth plus
    all pending deltas, so concurrent intents batch without double-applying.
    Rows are inert until the sync loop delivers them — no truth, no inventing
    one: a tracked row with NULL truth skips the queue (unmanaged-equivalent
    for push purposes)."""
    if delta == 0:
        return
    row = _get_row(session, merchant_id, sku)
    if row is None or row.last_platform_qty is None:
        return
    key = f"inv-wb:{checkout_id}:{sku}:{delta}"
    existing = session.exec(
        select(InventoryWriteback).where(InventoryWriteback.idempotency_key == key)
    ).first()
    if existing:
        return
    session.add(
        InventoryWriteback(
            merchant_id=merchant_id,
            sku=sku,
            quantity=delta,
            status=InventoryWritebackStatus.PENDING,
            attempts=0,
            last_error=None,
            idempotency_key=key,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    session.flush()


def verify_inventory_balances(session: Session, reference_id: str) -> bool:
    """INV-5a transcribed: at every terminal state the `reserved` account MUST
    net to zero per reference_id, and the `sold` account MUST be non-negative.
    Mirrors verify_ledger_balances' escrow-zero / economic-non-negative split;
    `available` legs are flow-only and carry no per-reference invariant."""
    entries = session.exec(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.reference_id == reference_id)
    ).all()
    balances: dict[str, int] = {}
    for entry in entries:
        balances.setdefault(entry.account, 0)
        if entry.entry_type in _ADD_TYPES:
            balances[entry.account] += entry.quantity
        else:
            balances[entry.account] -= entry.quantity
    if balances.get(_RESERVED, 0) != 0:
        return False
    if balances.get(_SOLD, 0) < 0:
        return False
    return True


def is_sellable(session: Session, merchant_id: str, sku: str) -> bool:
    """One-unit sellability for suggestion filtering (stage 27): unmanaged is
    sellable (distinct from zero, always), tracked needs one available unit."""
    available = available_qty(session, merchant_id, sku)
    if available is None:
        return True
    return available >= 1


# ---------------------------------------------------------------------------
# Sync loop tick (server.py drives this on a timer, mirroring
# _campaign_expiry_loop: tick on a timer, log-and-continue on failure, never
# raise past its own tick; the caller owns the transaction).
# ---------------------------------------------------------------------------


def inventory_sync_tick(config: Settings, session: Session) -> dict[str, Any]:
    """One sync pass: seed tracked rows from the stock adapter, overlay
    outstanding reservations, flag drift (blocks sales, alerts), flag
    low stock (alerts once per dip), and deliver pending write-backs.

    Never goes negative (available clamps at 0), never touches a checkout row
    (a paid order is never auto-cancelled — drift only blocks NEW sales via
    the gate), and never raises for a missing adapter (unmanaged catalogs
    simply have nothing to sync).
    """
    from openstore.core.settings_overlay import effective_settings

    summary: dict[str, Any] = {
        "seeded": 0,
        "drifted": [],
        "low_stock": [],
        "writebacks_delivered": 0,
        "writebacks_failed": 0,
        "writebacks_skipped": 0,
    }
    try:
        from openstore.config import merchant_id as merchant_id_of
        from openstore.surfaces.adapters.registry import get_adapter

        merchant = merchant_id_of(config)
        eff = effective_settings(config, session)
        adapter = get_adapter(eff, purpose="stock")
    except Exception:
        return {**summary, "skipped": True}

    try:
        catalog_items = _catalog_universe(config, session)
    except Exception:
        catalog_items = []
    platform_stock: dict[str, int | None] = {}
    try:
        fetched: dict[str, int] = adapter.fetch_stock([i["sku"] for i in catalog_items])
    except Exception:
        fetched = {}
    platform_stock.update(fetched)
    stock_by_sku = {i["sku"]: i.get("stock") for i in catalog_items}

    for item in catalog_items:
        sku = item["sku"]
        platform_qty = platform_stock.get(sku, stock_by_sku.get(sku))
        row = _get_row(session, merchant, sku)
        if row is None:
            if platform_qty is None:
                continue
            row = InventoryItem(
                merchant_id=merchant,
                sku=sku,
                tracked=True,
                low_stock_threshold=DEFAULT_LOW_STOCK_THRESHOLD,
                last_platform_qty=platform_qty,
                drifted=False,
                low_stock_notified=False,
                updated_at=_now(),
            )
            session.add(row)
            session.flush()
            summary["seeded"] += 1
        elif platform_qty is not None:
            row.last_platform_qty = platform_qty
            row.updated_at = _now()
            session.add(row)
        else:
            continue
        if not row.tracked:
            continue
        outstanding = compute_sku_exposure(session, merchant, sku)
        unpushed = _unpushed_sold(session, merchant, sku)
        truth = row.last_platform_qty or 0
        was_drifted = row.drifted
        row.drifted = truth < outstanding + unpushed
        if row.drifted and not was_drifted:
            from openstore.notifier import sync_alert

            sync_alert(
                ALERT_DRIFT,
                f"Stock drift for {sku}: platform holds {truth}, "
                f"{outstanding} reserved + {unpushed} sold-unpushed — new sales blocked",
                {"merchant_id": merchant, "sku": sku, "platform_qty": truth,
                 "outstanding": outstanding, "unpushed": unpushed},
            )
            summary["drifted"].append(sku)
        available = max(0, truth - outstanding - unpushed)
        if available <= row.low_stock_threshold:
            if not row.low_stock_notified:
                from openstore.notifier import sync_alert

                sync_alert(
                    ALERT_LOW_STOCK,
                    f"Low stock for {sku}: {available} available "
                    f"(threshold {row.low_stock_threshold})",
                    {"merchant_id": merchant, "sku": sku, "available": available},
                )
                row.low_stock_notified = True
                summary["low_stock"].append(sku)
        elif row.low_stock_notified:
            row.low_stock_notified = False
        session.add(row)
    session.flush()

    _deliver_writebacks(session, adapter, merchant, summary)
    session.flush()
    return summary


def _catalog_universe(config: Settings, session: Session) -> list[dict[str, Any]]:
    """SKU universe for seeding: the serving catalog (caller's session —
    load_catalog resolves the DB overlay, and a second session would roll the
    caller back under SQLite's StaticPool)."""
    from openstore.surfaces.catalog import load_catalog

    return load_catalog(config, session)


def _deliver_writebacks(
    session: Session, adapter: Any, merchant: str, summary: dict[str, Any]
) -> None:
    """Deliver PENDING + FAILED intents via STOCK_WRITE, batched per SKU:
    absolute = max(0, fresh_platform_truth + all_pending_deltas). Batching is
    what keeps two intents for one SKU from double-applying, and resolving
    against fresh truth (falling back to the cached truth when the platform
    will not state one) is what keeps external edits from being clobbered
    beyond last-writer-wins. Adapters without the capability mark rows
    SKIPPED (never a failure); delivery errors flip rows to FAILED (the DLQ)
    with an alert on the first failure per row."""
    from openstore.surfaces.adapters.base import AdapterCapability

    intents = session.exec(
        select(InventoryWriteback).where(
            InventoryWriteback.merchant_id == merchant,
            InventoryWriteback.status.in_(  # type: ignore[attr-defined]
                [InventoryWritebackStatus.PENDING, InventoryWritebackStatus.FAILED]
            ),
        )
    ).all()
    if not intents:
        return
    if AdapterCapability.STOCK_WRITE not in adapter.capabilities:
        for intent in intents:
            intent.status = InventoryWritebackStatus.SKIPPED
            intent.updated_at = _now()
            session.add(intent)
            summary["writebacks_skipped"] += 1
        return
    by_sku: dict[str, list[InventoryWriteback]] = {}
    for intent in intents:
        by_sku.setdefault(intent.sku, []).append(intent)
    for sku, rows in sorted(by_sku.items()):
        first_failure = any(
            i.attempts == 0 and i.status == InventoryWritebackStatus.PENDING for i in rows
        )
        try:
            base: int | None = None
            try:
                base = adapter.fetch_stock([sku]).get(sku)
            except Exception:
                base = None
            if base is None:
                cached = _get_row(session, merchant, sku)
                base = cached.last_platform_qty if cached and cached.last_platform_qty is not None else 0
            adapter.write_stock({sku: max(0, base + sum(i.quantity for i in rows))})
        except Exception as e:
            for intent in rows:
                intent.attempts += 1
                intent.status = InventoryWritebackStatus.FAILED
                intent.last_error = str(e)[:1024]
                intent.updated_at = _now()
                session.add(intent)
            summary["writebacks_failed"] += len(rows)
            if first_failure:
                from openstore.notifier import sync_alert

                sync_alert(
                    ALERT_WRITEBACK_FAILED,
                    f"Stock write-back failed for {sku}: {e}",
                    {"merchant_id": merchant, "sku": sku,
                     "delta": sum(i.quantity for i in rows)},
                )
        else:
            for intent in rows:
                intent.status = InventoryWritebackStatus.DELIVERED
                intent.last_error = None
                intent.updated_at = _now()
                session.add(intent)
            summary["writebacks_delivered"] += len(rows)
