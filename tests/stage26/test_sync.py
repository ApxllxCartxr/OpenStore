# tests/stage26/test_sync.py
# Stage 26.5: the sync loop overlays platform truth with reservations — drift
# below reservations blocks new sales and alerts (never negative, never
# auto-cancels a paid order); forced write-back failures land in the DLQ with
# an alert; low-stock alerts fire once per dip.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import CART_ONE_VANILLA, MERCHANT, seed_tracked
from openstore.core.api import CommerceError, create_checkout_from_policy
from openstore.core.inventory import (
    available_qty,
    inventory_sync_tick,
)
from openstore.models import (
    Checkout,
    InventoryItem,
    InventoryWriteback,
    InventoryWritebackStatus,
    OrderState,
)
from sqlmodel import select

SKU = "gelato_vanilla"


def _checkout(session, config, policy, checkout_id: str) -> None:
    result = create_checkout_from_policy(
        config=config,
        session=session,
        trace_id=f"trace_{checkout_id}",
        client_id="cli_sync",
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


def _alerts(monkeypatch):
    seen: list[tuple[str, str]] = []

    def _fake(alert_type: str, message: str, details: dict) -> None:
        seen.append((alert_type, message))

    import openstore.notifier as notifier_mod

    monkeypatch.setattr(notifier_mod, "sync_alert", _fake)
    return seen


def _stub_adapter(monkeypatch, config, stock_overrides=None, write_boom=False):
    """Wrap the real YAML adapter: platform truth comes from `overrides`
    (simulating a live platform that moved out-of-band — the row cache must
    never be hand-edited to fake drift, the tick re-reads the adapter), with
    an optionally failing STOCK_WRITE."""
    import openstore.surfaces.catalog as catalog_mod
    from openstore.surfaces.adapters.base import AdapterCapability

    base = catalog_mod.YamlAdapter(config, None)
    overrides = stock_overrides or {}

    class _Stub:
        capabilities = frozenset(
            {AdapterCapability.CATALOG_READ, AdapterCapability.STOCK_READ}
            | ({AdapterCapability.STOCK_WRITE} if write_boom else set())
        )

        def fetch_items(self):
            return base.fetch_items()

        def fetch_stock(self, skus):
            real = base.fetch_stock(skus)
            return {**real, **{k: v for k, v in overrides.items() if k in skus}}

        def write_stock(self, deltas):
            if write_boom:
                raise RuntimeError("platform 502")
            return base.write_stock(deltas)

    import openstore.surfaces.adapters.registry as registry_mod

    monkeypatch.setattr(
        registry_mod, "get_adapter", lambda eff, purpose="catalog": _Stub()
    )


def test_tick_seeds_from_yaml_stock(session, config):
    """YAML stock: 10 vanilla / 3 pistachio / None unmanaged: rows appear for
    the stocked SKUs only — unmanaged stays rowless, never zero."""
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert summary["seeded"] == 2
    vanilla = session.exec(
        select(InventoryItem).where(InventoryItem.sku == "gelato_vanilla")
    ).first()
    assert vanilla is not None and vanilla.last_platform_qty == 10 and vanilla.tracked
    assert (
        session.exec(
            select(InventoryItem).where(InventoryItem.sku == "gelato_unmanaged")
        ).first()
        is None
    )


def test_drift_blocks_sales_alerts_never_negative(session, config, policy, monkeypatch):
    seed_tracked(session, SKU, 5)
    _checkout(session, config, policy, "chk_drift_1")
    _checkout(session, config, policy, "chk_drift_2")
    # External platform sale drops truth below outstanding reservations.
    _stub_adapter(monkeypatch, config, {"gelato_vanilla": 1})
    session.commit()

    seen = _alerts(monkeypatch)
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert summary["drifted"] == [SKU]
    assert any(t == "inventory.drift_below_reservations" for t, _ in seen)

    row = session.exec(select(InventoryItem).where(InventoryItem.sku == SKU)).first()
    assert row is not None and row.drifted is True
    # Never negative…
    assert available_qty(session, MERCHANT, SKU) == 0
    # …and new sales block at the gate.
    with pytest.raises(CommerceError) as exc:
        create_checkout_from_policy(
            config=config,
            session=session,
            trace_id="trace_drift_3",
            client_id="cli_sync",
            merchant_id=MERCHANT,
            cart_items=CART_ONE_VANILLA,
            cart_hash="hash_drift_3",
            cart_version=1,
            policy=policy,
            assertion_verified=True,
            idempotency_key="chk_drift_3",
        )
    assert exc.value.reason_code == "inventory.insufficient_stock"
    session.rollback()


def test_drift_never_cancels_paid_orders(session, config, policy, monkeypatch):
    """A paid order caught in drift stays exactly where it is — drift only
    ever blocks NEW sales."""
    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    _checkout(session, config, policy, "chk_paid_kept")
    golden = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"
    payload = json.loads((golden / "payment_link_paid.json").read_text())
    payload["payload"]["payment_link"]["entity"]["reference_id"] = "chk_paid_kept"
    _apply_payment_link_paid(session, "payment_link.paid", payload)
    session.commit()

    # The platform sells everything out from under the paid order.
    _stub_adapter(monkeypatch, config, {"gelato_vanilla": 0})
    _alerts(monkeypatch)
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert summary["drifted"] == [SKU]
    kept = session.exec(select(Checkout).where(Checkout.id == "chk_paid_kept")).first()
    assert kept is not None and kept.state == OrderState.RELEASED


def test_writeback_failure_lands_in_dlq_with_alert(session, config, policy, monkeypatch):
    """COMMIT queues a write-back intent; a failing adapter flips it to FAILED
    (the DLQ) and raises exactly one alert for the row."""
    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    _checkout(session, config, policy, "chk_wb_fail")
    golden = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"
    payload = json.loads((golden / "payment_link_paid.json").read_text())
    payload["payload"]["payment_link"]["entity"]["reference_id"] = "chk_wb_fail"
    _apply_payment_link_paid(session, "payment_link.paid", payload)
    session.commit()

    intents = session.exec(select(InventoryWriteback)).all()
    assert len(intents) == 1 and intents[0].status == InventoryWritebackStatus.PENDING
    assert intents[0].quantity == -1  # sold delta, not an absolute level

    _stub_adapter(monkeypatch, config, write_boom=True)
    seen = _alerts(monkeypatch)
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert summary["writebacks_failed"] == 1
    dlq = session.exec(select(InventoryWriteback)).all()
    assert len(dlq) == 1
    assert dlq[0].status == InventoryWritebackStatus.FAILED
    assert dlq[0].attempts == 1
    assert "502" in (dlq[0].last_error or "")
    assert [t for t, _ in seen].count("inventory.writeback_failed") == 1


def test_yaml_writeback_skips_without_alert(session, config, policy, monkeypatch):
    """Flat files have no write API: intents SKIP silently, never FAILED."""
    import json
    from pathlib import Path

    from openstore.psp.razorpay_driver import _apply_payment_link_paid

    seed_tracked(session, SKU, 5)
    _checkout(session, config, policy, "chk_wb_skip")
    golden = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"
    payload = json.loads((golden / "payment_link_paid.json").read_text())
    payload["payload"]["payment_link"]["entity"]["reference_id"] = "chk_wb_skip"
    _apply_payment_link_paid(session, "payment_link.paid", payload)
    session.commit()

    seen = _alerts(monkeypatch)
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert summary["writebacks_skipped"] == 1
    assert "inventory.writeback_failed" not in [t for t, _ in seen]
    row = session.exec(select(InventoryWriteback)).all()[0]
    assert row.status == InventoryWritebackStatus.SKIPPED


def test_low_stock_alerts_once_per_dip(session, config, monkeypatch):
    seed_tracked(session, SKU, 10, threshold=5)
    _stub_adapter(monkeypatch, config, {"gelato_vanilla": 2})
    seen = _alerts(monkeypatch)
    summary = inventory_sync_tick(config, session)
    session.commit()
    assert SKU in summary["low_stock"]
    assert len([m for t, m in seen if t == "inventory.low_stock" and SKU in m]) == 1
    # Second tick at the same level: no repeat.
    inventory_sync_tick(config, session)
    session.commit()
    assert len([m for t, m in seen if t == "inventory.low_stock" and SKU in m]) == 1
