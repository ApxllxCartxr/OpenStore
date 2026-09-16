# tests/stage26/test_race.py
# Stage 26.3: a genuine threaded last-unit race — two OS threads buying the
# final unit through the real checkout path; exactly one winner, the loser is
# refused with inventory.insufficient_stock. Not simulated: each thread owns
# its own engine + BEGIN IMMEDIATE transaction on one shared file database,
# so SQLite serialises the two gates exactly like production.

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from conftest import CART_ONE_VANILLA, MERCHANT
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
from openstore.core.api import CommerceError
from sqlalchemy import text
from sqlmodel import Session, create_engine

RESULTS: dict[str, str] = {}


def _settings(db_path: Path, catalog_path: Path) -> Settings:
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
        database=DatabaseConfig(url=f"sqlite:///{db_path}"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        catalog_path=str(catalog_path),
    )


def _thread_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=10000"))
        conn.commit()
    return engine


def _buyer(
    name: str,
    config: Settings,
    policy_id: str,
    barrier: threading.Barrier,
    checkout_id: str,
) -> None:
    from openstore.core.api import create_checkout_from_policy
    from openstore.models import IntentPolicy
    from sqlmodel import select

    engine = _thread_engine(config.database.url)
    try:
        barrier.wait(timeout=30)
        # BEGIN IMMEDIATE serialises the two gates (INV-11 posture); retry a
        # busy begin rather than flaking the suite under load.
        session = Session(engine)
        try:
            for _ in range(50):
                try:
                    session.execute(text("BEGIN IMMEDIATE"))
                    break
                except Exception as e:
                    if "locked" not in str(e).lower():
                        raise
                    import time

                    time.sleep(0.05)
            policy = session.exec(
                select(IntentPolicy).where(IntentPolicy.id == policy_id)
            ).first()
            assert policy is not None
            try:
                result = create_checkout_from_policy(
                    config=config,
                    session=session,
                    trace_id=f"trace_{checkout_id}",
                    client_id=f"cli_{name}",
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
                RESULTS[name] = "won"
            except CommerceError as e:
                session.rollback()
                RESULTS[name] = e.reason_code
        finally:
            session.close()
    finally:
        engine.dispose()


def test_threaded_last_unit_race(tmp_path: Path):
    import openstore.surfaces.catalog as catalog_mod

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "items:\n"
        "  - sku: gelato_vanilla\n"
        "    name: Vanilla Gelato\n"
        "    unit_minor: 15000\n"
        "    tags: [gelato]\n"
        "    stock: 1\n"
    )
    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()

    db_path = tmp_path / "race.db"
    config = _settings(db_path, catalog)

    import openstore.core.database as db_mod

    db_mod._engine = None
    db_mod._engine_url = None
    from openstore.core.database import get_session, init_database
    from openstore.models import IntentPolicy, InventoryItem

    init_database(config)
    s = get_session(config)
    now = datetime.now(UTC)
    s.add(
        IntentPolicy(
            id="pol_race", merchant_id=MERCHANT, policy_version=2, policy_hash="ph",
            currency="INR", max_spend_per_tx_minor=10_000_000,
            max_spend_total_minor=100_000_000, max_transactions=100,
            allowed_tags=[], tag_mode="all", blocked_skus=[],
            not_before=int(now.timestamp()) - 60,
            expires_at=int(now.timestamp()) + 86400,
            assertion_max_age_seconds=86400, no_human_authority=True,
            fulfilment_mode="all_or_nothing", required_skus=[],
            webauthn_credential_id="cred", webauthn_sign_count=0,
            signed_at=now.replace(tzinfo=None), is_active=True,
        )
    )
    s.add(
        InventoryItem(
            merchant_id=MERCHANT, sku="gelato_vanilla", tracked=True,
            low_stock_threshold=5, last_platform_qty=1, drifted=False,
            low_stock_notified=False, updated_at=now.replace(tzinfo=None),
        )
    )
    s.commit()
    s.close()
    db_mod._engine = None
    db_mod._engine_url = None

    RESULTS.clear()
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(
            target=_buyer, args=(name, config, "pol_race", barrier, f"chk_race_{name}"),
            name=f"buyer-{name}",
        )
        for name in ("a", "b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "buyer thread hung"

    assert sorted(RESULTS.values()) == ["inventory.insufficient_stock", "won"]

    # Exactly one unit reserved across both checkouts — no oversell.
    from openstore.core.inventory import compute_sku_exposure

    check_engine = _thread_engine(config.database.url)
    try:
        with Session(check_engine) as vsession:
            assert compute_sku_exposure(vsession, MERCHANT, "gelato_vanilla") == 1
    finally:
        check_engine.dispose()
