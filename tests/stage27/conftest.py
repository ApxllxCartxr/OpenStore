# tests/stage27/conftest.py
# Stage 27: fixtures for merchandising rules + deterministic suggestions.

from __future__ import annotations

from pathlib import Path

import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import get_session, init_database

CATALOG_YAML = (
    "items:\n"
    "  - sku: gelato_vanilla\n"
    "    name: Vanilla Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [vegan, gelato]\n"
    "    related_skus: [cone_waffle]\n"
    "    related_source: yaml\n"
    "    stock: 10\n"
    "  - sku: gelato_pistachio\n"
    "    name: Pistachio Gelato\n"
    "    unit_minor: 18000\n"
    "    tags: [gelato]\n"
    "    stock: 10\n"
    "  - sku: cone_waffle\n"
    "    name: Waffle Cone\n"
    "    unit_minor: 3000\n"
    "    tags: [cone]\n"
    "    stock: 10\n"
    "  - sku: topping_gold\n"
    "    name: Gold Topping\n"
    "    unit_minor: 50000\n"
    "    tags: [gelato, luxury]\n"
    "    stock: 10\n"
    "  - sku: gelato_soldout\n"
    "    name: Soldout Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [gelato]\n"
    "    stock: 5\n"
)

MERCHANT = "gelateria-milano"


@pytest.fixture()
def config(tmp_path: Path) -> Settings:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML)
    import openstore.surfaces.catalog as catalog_mod

    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(
            rp_id="localhost", rp_name="OpenStore", origin="http://localhost:8000"
        ),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        catalog_path=str(catalog),
    )


@pytest.fixture()
def session(config: Settings):
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)
    s = get_session(config)
    try:
        yield s
    finally:
        s.close()
