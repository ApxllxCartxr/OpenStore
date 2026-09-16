# tests/stage27/test_suggest.py
# Stage 27.2: suggest_for_cart is deterministic (golden-pinned order),
# sellability-filtered, and never suggests in-cart or hallucinated SKUs.

from __future__ import annotations

import json
from pathlib import Path

from conftest import MERCHANT
from openstore.core.merchandising import (
    create_rule,
    suggest_for_cart,
)
from openstore.models import InventoryItem, MerchandisingKind

GOLDEN = Path(__file__).resolve().parent / "suggest_golden.json"


def _activate(session, config, rule_id: str):
    """Bypass the passkey ceremony for selection tests (the ceremony has its
    own test); lifecycle states are what selection reads."""
    from openstore.models import CampaignState, MerchandisingRule
    from sqlmodel import select

    rule = session.exec(select(MerchandisingRule).where(MerchandisingRule.id == rule_id)).first()
    assert rule is not None
    rule.state = CampaignState.ACTIVE
    session.add(rule)
    session.commit()


def _seed_rules(session, config):
    r1 = create_rule(
        session, config, MERCHANT, MerchandisingKind.CROSS_SELL,
        "Pistachio with vanilla", "merchant-authored",
        ["gelato_vanilla"], "gelato_pistachio",
    )
    r2 = create_rule(
        session, config, MERCHANT, MerchandisingKind.UPGRADE,
        "Gold over vanilla", "merchant-authored",
        ["gelato_vanilla"], "topping_gold",
    )
    session.commit()
    _activate(session, config, r1.id)
    _activate(session, config, r2.id)
    return r1, r2


def test_golden_order(session, config):
    """ACTIVE rules first (by creation order), then adapter-native/related —
    byte-pinned so any selection drift breaks the build deliberately."""
    r1, r2 = _seed_rules(session, config)
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert [s["sku"] for s in got] == ["gelato_pistachio", "topping_gold", "cone_waffle"]
    assert got[0]["source"] == "rule" and got[0]["rule_id"] == r1.id
    assert got[1]["source"] == "rule" and got[1]["rule_id"] == r2.id
    assert got[1]["price_delta_minor"] == 50000 - 15000
    assert got[2]["source"] == "related"
    if GOLDEN.exists():
        expected = json.loads(GOLDEN.read_text())
        assert [
            {k: s[k] for k in ("sku", "source", "kind") if k in s}
            for s in got
        ] == expected
    else:
        GOLDEN.write_text(json.dumps(
            [
                {k: s[k] for k in ("sku", "source", "kind") if k in s}
                for s in got
            ],
            indent=2,
            sort_keys=True,
        ))


def test_in_cart_and_duplicates_excluded(session, config):
    _seed_rules(session, config)
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla", "cone_waffle"])
    skus = [s["sku"] for s in got]
    assert "gelato_vanilla" not in skus and "cone_waffle" not in skus
    assert len(skus) == len(set(skus))


def test_out_of_stock_never_suggested(session, config):
    from datetime import UTC, datetime

    _seed_rules(session, config)
    session.add(
        InventoryItem(
            merchant_id=MERCHANT, sku="gelato_pistachio", tracked=True,
            low_stock_threshold=5, last_platform_qty=0, drifted=False,
            low_stock_notified=False,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    session.commit()
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert "gelato_pistachio" not in [s["sku"] for s in got]
    # The rule still exists — it is filtered, not deleted.
    assert "topping_gold" in [s["sku"] for s in got]


def test_limit_caps_results(session, config):
    _seed_rules(session, config)
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"], limit=1)
    assert [s["sku"] for s in got] == ["gelato_pistachio"]


def test_no_rules_falls_back_to_related(session, config):
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert [s["sku"] for s in got] == ["cone_waffle"]


def test_llm_free_determinism(session, config):
    """Same cart + same state = same ordered list, twice in a row."""
    _seed_rules(session, config)
    first = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    second = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert first == second
