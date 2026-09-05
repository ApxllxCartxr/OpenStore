# tests/stage15/conftest.py
# DECISION-034: fixtures for the autonomous growth-trigger loop + campaign
# outcome feedback. Mirrors tests/stage08/conftest.py's config/catalog/session
# shape (closest definition wins, no shared import needed).

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
from openstore.core.database import get_or_create_checkout, get_session, init_database

_CATALOG = (
    "items:\n"
    "  - sku: gelato_vanilla\n"
    "    name: Vanilla Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [vegan, gelato]\n"
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


@pytest.fixture()
def seed_checkout(session):
    """Factory: (sku, qty, unit_minor, created_at, merchant_id=...) -> Checkout.
    A minimal HELD checkout with one line item, for building sales-history
    fixtures without going through the full checkout flow."""
    counter = {"n": 0}

    def _seed(
        sku: str,
        qty: int,
        unit_minor: int,
        created_at,
        merchant_id: str = "gelateria-milano",
    ):
        counter["n"] += 1
        n = counter["n"]
        checkout, _ = get_or_create_checkout(
            session=session,
            checkout_id=f"chk_seed_{n}",
            trace_id=f"trace_seed_{n}",
            client_id="oc_test",
            merchant_id=merchant_id,
            cart_hash=f"cart_seed_{n}",
            cart_version=1,
            amount_minor=unit_minor * qty,
            currency="INR",
            policy_id=None,
            policy_hash=None,
            aal_level=2,
            expires_at=created_at,
            idempotency_key=f"idem_seed_{n}",
            cart_snapshot={"items": [{"sku": sku, "qty": qty, "unit_minor": unit_minor}]},
        )
        checkout.created_at = created_at
        session.add(checkout)
        session.flush()
        return checkout

    return _seed
