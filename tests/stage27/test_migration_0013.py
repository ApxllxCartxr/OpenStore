# tests/stage27/test_migration_0013.py
# Stage 27.1: migration 0013 verified upgrade AND downgrade against a database
# stamped at 0012 — not a fresh one (0001's create_all hides missing DDL).

from __future__ import annotations

import sqlite3
from pathlib import Path

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

TABLE = "merchandising_rules"


def _config(db_path: Path, catalog: Path) -> Settings:
    return Settings(
        merchant=MerchantConfig(name="M", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_x", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1,
            merchant_trace_channel_id=2, money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="T", origin="http://localhost:8000"),
        database=DatabaseConfig(url=f"sqlite:///{db_path}"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        evidence_share_ttl_days=30,
        catalog_path=str(catalog),
    )


def _alembic_config(url: str):
    import os as _os

    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    _os.environ["OPENSTORE_DB_URL"] = url
    return cfg


def _tables(db_path: Path) -> set[str]:
    return {
        r[0]
        for r in sqlite3.connect(db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def test_upgrade_and_downgrade_from_0012(tmp_path: Path):
    from alembic import command

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("items: []\n")
    db_path = tmp_path / "m.db"
    url = f"sqlite:///{db_path}"
    cfg = _config(db_path, catalog)
    _ = cfg
    alembic_cfg = _alembic_config(url)

    # A production database at 0012 predates the table: migrate, then DROP it
    # (0001's create_all would otherwise carry it from live metadata).
    command.upgrade(alembic_cfg, "0012")
    assert "inventory_items" in _tables(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        conn.commit()
    assert TABLE not in _tables(db_path)

    command.upgrade(alembic_cfg, "0013")
    assert TABLE in _tables(db_path)
    cols = {
        r[1] for r in sqlite3.connect(db_path).execute(f"PRAGMA table_info({TABLE})")
    }
    assert {
        "id", "merchant_id", "kind", "title", "rationale", "trigger_skus",
        "suggested_sku", "campaign_id", "source_signals", "draft_digest",
        "state", "approver_credential_id", "approved_at", "webauthn_assertion",
        "created_at", "updated_at",
    } <= cols

    # Round-trip through the migrated schema, incl. the state machine.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (id, merchant_id, kind, title, rationale,"
            " trigger_skus, suggested_sku, campaign_id, source_signals,"
            " draft_digest, state, created_at, updated_at)"
            " VALUES ('mrule_1', 'm1', 'CROSS_SELL', 'T', 'R',"
            " '[\"a\"]', 'b', NULL, '{}', 'd', 'ACTIVE',"
            " '2026-09-16 00:00:00', '2026-09-16 00:00:00')"
        )
        conn.commit()
    assert sqlite3.connect(db_path).execute(
        f"SELECT COUNT(*) FROM {TABLE}"
    ).fetchone() == (1,)

    command.downgrade(alembic_cfg, "0012")
    assert TABLE not in _tables(db_path)

    command.upgrade(alembic_cfg, "head")
    assert TABLE in _tables(db_path)
