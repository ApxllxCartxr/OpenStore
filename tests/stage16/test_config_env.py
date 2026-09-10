# tests/stage16/test_config_env.py
# S16 production fix: Settings.from_yaml must honor DATABASE__URL
# (model_validate alone ignores the environment — the container previously
# migrated/booted SQLite while believing it was on Postgres).

from __future__ import annotations

from openstore.config import load_config


def test_database_url_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "m.yaml"
    cfg.write_text(
        'merchant: {name: "T"}\n'
        'razorpay: {key_id: k, key_secret: s}\n'
        "discord: {bot_token: t, buyer_trace_channel_id: 1,"
        " merchant_trace_channel_id: 2, money_trace_channel_id: 3, alerts_channel_id: 4}\n"
        'webauthn: {rp_id: localhost, rp_name: x, origin: "http://localhost"}\n'
        'database: {url: "sqlite:///local.db"}\n'
    )
    monkeypatch.setenv(
        "DATABASE__URL", "postgresql+psycopg://u:p@db:5432/m"
    )
    assert (
        load_config(cfg).database.url
        == "postgresql+psycopg://u:p@db:5432/m"
    )


def test_database_url_env_empty_fails_loud(tmp_path, monkeypatch):
    import pytest

    cfg = tmp_path / "m.yaml"
    cfg.write_text(
        'merchant: {name: "T"}\n'
        'razorpay: {key_id: k, key_secret: s}\n'
        "discord: {bot_token: t, buyer_trace_channel_id: 1,"
        " merchant_trace_channel_id: 2, money_trace_channel_id: 3, alerts_channel_id: 4}\n"
        'webauthn: {rp_id: localhost, rp_name: x, origin: "http://localhost"}\n'
    )
    monkeypatch.setenv("DATABASE__URL", "   ")
    with pytest.raises(ValueError, match="DATABASE__URL"):
        load_config(cfg)
