# tests/stage26/test_migration_0012.py
# Stage 26.1: migration 0012 verified upgrade AND downgrade against a database
# stamped at 0011 — not a fresh one (0001's create_all hides missing DDL).

from __future__ import annotations

import os
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

TABLES = ("inventory_items", "inventory_ledger_entries", "inventory_writebacks")


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


def test_upgrade_and_downgrade_from_0011(tmp_path: Path):
    from alembic import command

    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("items: []\n")
    db_path = tmp_path / "m.db"
    url = f"sqlite:///{db_path}"
    cfg = _config(db_path, catalog)
    alembic_cfg = _alembic_config(url)

    # Stamp a database at 0011 the hard way: migrate to head (0001's
    # create_all would already carry the new tables from live metadata),
    # then DROP the 0012 tables so the database genuinely predates them —
    # exactly the state a production database at 0011 is in.
    command.upgrade(alembic_cfg, "0011")
    assert "merchant_sessions" in _tables(db_path)
    with sqlite3.connect(db_path) as conn:
        for table in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    for table in TABLES:
        assert table not in _tables(db_path)

    # Upgrade creates all three with the pinned columns.
    command.upgrade(alembic_cfg, "0012")
    assert all(t in _tables(db_path) for t in TABLES)
    cols = {
        r[1]
        for r in sqlite3.connect(db_path).execute("PRAGMA table_info(inventory_items)")
    }
    assert {
        "id", "merchant_id", "sku", "tracked", "low_stock_threshold",
        "last_platform_qty", "drifted", "low_stock_notified", "updated_at",
    } <= cols
    ledger_cols = {
        r[1]
        for r in sqlite3.connect(db_path).execute(
            "PRAGMA table_info(inventory_ledger_entries)"
        )
    }
    assert {
        "id", "trace_id", "client_id", "entry_type", "quantity", "merchant_id",
        "sku", "reference_id", "account", "counterparty_account",
        "idempotency_key", "description", "created_at",
    } <= ledger_cols

    # Round-trip through the migrated schema.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO inventory_items (merchant_id, sku, tracked,"
            " low_stock_threshold, last_platform_qty, drifted,"
            " low_stock_notified, updated_at)"
            " VALUES ('m1', 'sku1', 1, 5, 7, 0, 0, '2026-09-16 00:00:00')"
        )
        conn.execute(
            "INSERT INTO inventory_ledger_entries (trace_id, client_id,"
            " entry_type, quantity, merchant_id, sku, reference_id, account,"
            " counterparty_account, idempotency_key, description, created_at)"
            " VALUES ('t', 'c', 'RESERVE', 2, 'm1', 'sku1', 'chk1',"
            " 'reserved', 'available', 'k1', 'd', '2026-09-16 00:00:00')"
        )
        conn.commit()
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM inventory_items").fetchone() == (1,)

    # Downgrade removes all three (data goes with them — documented).
    command.downgrade(alembic_cfg, "0011")
    assert all(t not in _tables(db_path) for t in TABLES)

    # And upgrade again is clean (idempotent DDL path).
    command.upgrade(alembic_cfg, "head")
    assert all(t in _tables(db_path) for t in TABLES)
    assert os.environ["OPENSTORE_DB_URL"] == url
    _ = cfg
