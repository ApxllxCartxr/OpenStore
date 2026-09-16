# tests/stage27/test_adapter_native.py
# Stage 27.3 / DEF-6: adapter-native cross-sell/up-sell fields flow into
# suggestions with zero merchant configuration — WooCommerce cross_sell_ids /
# upsell_ids (Magento / BigCommerce related_products share the shape).

from __future__ import annotations

from types import SimpleNamespace

import httpx
from conftest import MERCHANT
from openstore.core.merchandising import suggest_for_cart
from openstore.surfaces.adapters.woo_adapter import WooCommerceAdapter


def _woo() -> WooCommerceAdapter:
    source = SimpleNamespace(
        base_url="https://shop.example", consumer_key="ck", consumer_secret="cs",
        page_size=100, max_pages=40,
    )
    return WooCommerceAdapter(source, httpx.Client(), merchant_currency="INR")


def test_woo_native_ids_normalize_with_provenance():
    adapter = _woo()
    id_to_sku = {11: "gelato_vanilla", 12: "cone_waffle", 13: "topping_gold"}
    item = adapter._normalize(
        {
            "id": 11, "sku": "gelato_vanilla", "name": "Vanilla",
            "price": "150.00", "tags": [{"name": "gelato"}],
            "manage_stock": False, "cross_sell_ids": [12], "upsell_ids": [13],
            "short_description": "", "description": "",
        },
        id_to_sku,
    )
    assert item is not None
    assert item["related_skus"] == ["cone_waffle", "topping_gold"]
    assert item["related_source"] == "woocommerce"


def test_native_suggestions_need_zero_config(session, config, monkeypatch):
    """No rule, no merchant-authored related_skus — the adapter's own fields
    still suggest, labelled by provenance (DECISION-049: rule provenance on
    the suggestion, adapter provenance preserved underneath)."""

    native = [
        {"sku": "gelato_vanilla", "name": "Vanilla", "unit_minor": 15000,
         "tags": ["gelato"], "related_skus": ["cone_waffle"],
         "related_source": "woocommerce", "description": "", "offers": [],
         "stock": 10},
        {"sku": "cone_waffle", "name": "Cone", "unit_minor": 3000,
         "tags": ["cone"], "related_skus": [],
         "related_source": "woocommerce", "description": "", "offers": [],
         "stock": 10},
    ]
    # load_catalog is imported into merchandising at call time
    #     (function-level import), so patch it where it lives.
    import openstore.surfaces.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "load_catalog", lambda config, session=None: native)
    got = suggest_for_cart(session, config, MERCHANT, ["gelato_vanilla"])
    assert [s["sku"] for s in got] == ["cone_waffle"]
    assert got[0]["source"] == "adapter"
