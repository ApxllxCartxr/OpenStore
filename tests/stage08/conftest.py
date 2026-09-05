# tests/stage08/conftest.py
# Shared campaign fixtures for the Stage 8 suite. test_stage08_campaigns.py
# predates this file and keeps its own identical definitions (the closest
# definition wins), so nothing there changes behaviour.

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

_CATALOG = (
    "items:\n"
    "  - sku: gelato_vanilla\n"
    "    name: Vanilla Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [vegan, gelato]\n"
    "  - sku: gelato_chocolate\n"
    "    name: Chocolate Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [gelato]\n"
    "  - sku: gelato_pistachio\n"
    "    name: Pistachio Gelato\n"
    "    unit_minor: 18000\n"
    "    tags: [pistachio]\n"
)


@pytest.fixture()
def config() -> Settings:
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
    )


@pytest.fixture()
def config_with_catalog(config: Settings, tmp_path: Path) -> Settings:
    cat = tmp_path / "catalog.yaml"
    cat.write_text(_CATALOG)
    config.catalog_path = str(cat)
    import openstore.surfaces.catalog as cat_mod

    cat_mod.CATALOG_CACHE = None
    return config


@pytest.fixture()
def session(config_with_catalog: Settings):
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config_with_catalog)
    s = get_session(config_with_catalog)
    yield s
    s.close()
