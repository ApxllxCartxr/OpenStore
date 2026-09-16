# tests/stage26/test_stalled_exclusion.py
# Stage 26.6: stalled-SKU detection excludes out-of-stock SKUs, so the growth
# loop stops drafting campaigns for things that cannot ship. Unmanaged SKUs
# stay eligible (unmanaged is distinct from zero).

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import MERCHANT, seed_tracked
from openstore.core.campaigns import detect_stalled_skus, should_auto_trigger
from openstore.core.database import get_or_create_checkout


def _seed_sale(session, sku: str, days_ago: int, qty: int = 5) -> None:
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)
    n = f"{sku}_{days_ago}_{qty}_{id(session) % 1000}"
    checkout, _ = get_or_create_checkout(
        session=session,
        checkout_id=f"chk_stall_{n}",
        trace_id=f"trace_stall_{n}",
        client_id="oc_test",
        merchant_id=MERCHANT,
        cart_hash=f"cart_stall_{n}",
        cart_version=1,
        amount_minor=15000 * qty,
        currency="INR",
        policy_id=None,
        policy_hash=None,
        aal_level=2,
        expires_at=created + timedelta(hours=1),
        idempotency_key=f"idem_stall_{n}",
        cart_snapshot={"items": [{"sku": sku, "qty": qty, "unit_minor": 15000}]},
        agent_plan=None,
    )
    checkout.created_at = created
    session.add(checkout)
    session.commit()


def test_pure_form_excludes_out_of_stock():
    analytics = [
        {"sku": "a", "units_sold_7d": 0, "units_sold_30d": 5},
        {"sku": "b", "units_sold_7d": 0, "units_sold_30d": 5},
    ]
    assert detect_stalled_skus(analytics, 3) == ["a", "b"]
    assert detect_stalled_skus(analytics, 3, out_of_stock=["b"]) == ["a"]


def test_growth_loop_skips_unsellable(session, config):
    _seed_sale(session, "gelato_vanilla", days_ago=20)
    _seed_sale(session, "gelato_pistachio", days_ago=20)
    # Vanilla cannot ship (tracked, zero available); pistachio is unmanaged.
    seed_tracked(session, "gelato_vanilla", 0)
    stalled = should_auto_trigger(session, config, MERCHANT)
    assert "gelato_vanilla" not in stalled
    assert "gelato_pistachio" in stalled


def test_growth_loop_keeps_managed_sellable(session, config):
    _seed_sale(session, "gelato_vanilla", days_ago=20)
    seed_tracked(session, "gelato_vanilla", 10)
    assert should_auto_trigger(session, config, MERCHANT) == ["gelato_vanilla"]
