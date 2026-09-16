# tests/stage26/conftest.py
# Stage 26: fixtures for inventory + oversell gate. Mirrors tests/stage25's
# config/catalog/session shape (closest definition wins, no shared import).

from __future__ import annotations

from datetime import UTC, datetime
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
from openstore.models import IntentPolicy, InventoryItem

CATALOG_YAML = (
    "items:\n"
    "  - sku: gelato_vanilla\n"
    "    name: Vanilla Gelato\n"
    "    unit_minor: 15000\n"
    "    tags: [vegan, gelato]\n"
    "    stock: 10\n"
    "  - sku: gelato_pistachio\n"
    "    name: Pistachio Gelato\n"
    "    unit_minor: 18000\n"
    "    tags: [pistachio]\n"
    "    stock: 3\n"
    "  - sku: gelato_unmanaged\n"
    "    name: Unmanaged Gelato\n"
    "    unit_minor: 12000\n"
    "    tags: [gelato]\n"
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


@pytest.fixture()
def policy(session):
    """Autonomous (no_human_authority) policy with generous caps — the gate,
    not the compiler, is what these tests exercise."""
    now = datetime.now(UTC)
    pol = IntentPolicy(
        id="pol_inv",
        merchant_id=MERCHANT,
        policy_version=2,
        policy_hash="ph_inv",
        currency="INR",
        max_spend_per_tx_minor=10_000_000,
        max_spend_total_minor=100_000_000,
        max_transactions=100,
        allowed_tags=[],
        tag_mode="all",
        blocked_skus=[],
        not_before=int(now.timestamp()) - 60,
        expires_at=int(now.timestamp()) + 86400,
        assertion_max_age_seconds=86400,
        no_human_authority=True,
        fulfilment_mode="all_or_nothing",
        required_skus=[],
        webauthn_credential_id="cred_inv",
        webauthn_sign_count=0,
        signed_at=now.replace(tzinfo=None),
        is_active=True,
    )
    session.add(pol)
    session.commit()
    return pol


def seed_tracked(
    session,
    sku: str,
    qty: int | None,
    *,
    merchant_id: str = MERCHANT,
    tracked: bool = True,
    threshold: int = 5,
) -> InventoryItem:
    row = InventoryItem(
        merchant_id=merchant_id,
        sku=sku,
        tracked=tracked,
        low_stock_threshold=threshold,
        last_platform_qty=qty,
        drifted=False,
        low_stock_notified=False,
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    session.commit()
    return row


CART_ONE_VANILLA = [{"sku": "gelato_vanilla", "qty": 1, "unit_minor": 15000,
                     "tags": ["vegan", "gelato"]}]
