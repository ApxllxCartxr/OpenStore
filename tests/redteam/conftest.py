# tests/redteam/conftest.py
# Shared helpers for the adversarial (red-team) suite.

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
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
from openstore.models import Checkout, IntentPolicy
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel


def build_settings(name: str = "Gelateria Milano") -> Settings:
    return Settings(
        merchant=MerchantConfig(name=name, currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1,
            merchant_trace_channel_id=2, money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost:8000"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def settings() -> Settings:
    return build_settings()


@pytest.fixture()
def session(settings: Settings):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    yield s
    s.close()


def seed_policy(
    session: Session,
    policy_id: str = "pol_a",
    merchant_id: str = "gelateria-milano",
    max_spend_per_tx_minor: int = 50000,
    max_spend_total_minor: int = 25000,
    max_transactions: int = 10,
) -> IntentPolicy:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    pol = IntentPolicy(
        id=policy_id,
        merchant_id=merchant_id,
        policy_version=2,
        policy_hash=f"ph_{policy_id}",
        currency="INR",
        max_spend_per_tx_minor=max_spend_per_tx_minor,
        max_spend_total_minor=max_spend_total_minor,
        max_transactions=max_transactions,
        allowed_tags=[],
        tag_mode="all",
        blocked_skus=[],
        not_before=int(now.timestamp()) - 60,
        expires_at=int(now.timestamp()) + 86400,
        assertion_max_age_seconds=86400,
        fulfilment_mode="all_or_nothing",
        required_skus=[],
        webauthn_credential_id=f"cred_{policy_id}",
        webauthn_sign_count=0,
        signed_at=now,
        is_active=True,
    )
    session.add(pol)
    return pol


def seed_checkout(
    session: Session,
    checkout_id: str = "chk_1",
    merchant_id: str = "gelateria-milano",
    amount_minor: int = 40000,
    policy_id: str = "pol_a",
    cart_snapshot: dict | None = None,
) -> Checkout:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    ck = Checkout(
        id=checkout_id,
        trace_id=f"tr_{checkout_id}",
        client_id="cli_1",
        merchant_id=merchant_id,
        cart_hash=f"h_{checkout_id}",
        cart_version=1,
        amount_minor=amount_minor,
        currency="INR",
        state="HELD",
        policy_id=policy_id,
        policy_hash=f"ph_{policy_id}",
        aal_level=2,
        expires_at=now + timedelta(hours=1),
        idempotency_key=f"idem_{checkout_id}",
        cart_snapshot=cart_snapshot or {"items": []},
        created_at=now,
        updated_at=now,
    )
    session.add(ck)
    return ck


@pytest.fixture()
def cat_config(tmp_path: Path) -> Settings:
    """Settings with a tiny catalog (single gelato_vanilla SKU), for campaign
    validation tests that need catalog-sku checks."""
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "items:\n"
        "  - sku: gelato_vanilla\n"
        "    name: Vanilla Gelato\n"
        "    unit_minor: 15000\n"
        "    tags: [gelato]\n"
    )
    cfg = build_settings()
    cfg.catalog_path = str(cat)
    if "openstore.surfaces.catalog" in sys.modules:
        mod = sys.modules["openstore.surfaces.catalog"]
        if hasattr(mod, "CATALOG_CACHE"):
            mod.CATALOG_CACHE = None
    return cfg


def make_campaign(
    session: Session,
    config: Settings,
    title: str = "Summer Gelato",
    rationale: str = "Hot days sell more",
    sku: str = "gelato_vanilla",
    discount_bps: int = 1500,
):
    from openstore.core.campaigns import create_campaign

    now = datetime.now(UTC)
    return create_campaign(
        session, config, "gelateria-milano",
        title=title, rationale=rationale, discount_bps=discount_bps,
        applies_to_skus=[sku],
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=7),
    )
