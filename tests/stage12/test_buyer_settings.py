# tests/stage12/test_buyer_settings.py
# S12 step 7: BuyerSettings (buyer_config.py) — the out-of-process buyer
# agent's own config. Same extra="forbid" contract as config.py's Settings
# (R0.3: a typo'd key must fail loud, not silently be ignored).

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from openstore.buyer_config import BuyerSettings, MerchantOrigin
from pydantic import ValidationError

VALID_CONFIG: dict = {
    "discord": {
        "bot_token": "test-token",
        "buyer_trace_channel_id": 1,
        "merchant_trace_channel_id": 2,
        "money_trace_channel_id": 3,
        "alerts_channel_id": 4,
    },
    "database": {"url": "sqlite:///buyer.db"},
    "llm": {"model": "gpt-4o-mini", "temperature": 0.2},
    "merchants": [
        {
            "name": "Gelateria Milano",
            "base_url": "http://localhost:8000",
            "client_id": "cid_gelato",
            "client_secret": "secret_gelato",
        },
        {
            "name": "Chai House",
            "base_url": "http://localhost:8001",
            "client_id": "cid_chai",
            "client_secret": "secret_chai",
        },
    ],
}


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "buyer.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


class TestBuyerSettingsLoadsYaml:
    def test_loads_two_merchants(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, VALID_CONFIG)
        config = BuyerSettings.from_yaml(path)

        assert len(config.merchants) == 2
        assert config.merchants[0] == MerchantOrigin(
            name="Gelateria Milano",
            base_url="http://localhost:8000",
            client_id="cid_gelato",
            client_secret="secret_gelato",
        )
        assert config.merchants[1].merchant_id == "chai-house"
        assert config.discord.bot_token == "test-token"
        assert config.database.url == "sqlite:///buyer.db"

    def test_database_defaults_to_buyer_db(self, tmp_path: Path) -> None:
        data = {k: v for k, v in VALID_CONFIG.items() if k != "database"}
        path = _write_config(tmp_path, data)
        config = BuyerSettings.from_yaml(path)
        assert config.database.url == "sqlite:///buyer.db"

    def test_rejects_unknown_top_level_key(self, tmp_path: Path) -> None:
        data = {**VALID_CONFIG, "bogus_section": {"foo": "bar"}}
        path = _write_config(tmp_path, data)
        with pytest.raises(ValidationError):
            BuyerSettings.from_yaml(path)

    def test_env_var_interpolation(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TEST_BUYER_BOT_TOKEN", "interpolated-token")
        data = {
            **VALID_CONFIG,
            "discord": {**VALID_CONFIG["discord"], "bot_token": "${TEST_BUYER_BOT_TOKEN}"},
        }
        path = _write_config(tmp_path, data)
        config = BuyerSettings.from_yaml(path)
        assert config.discord.bot_token == "interpolated-token"

    def test_undefined_env_var_fails_loud(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TOTALLY_UNDEFINED_BUYER_VAR", raising=False)
        data = {
            **VALID_CONFIG,
            "discord": {
                **VALID_CONFIG["discord"],
                "bot_token": "${TOTALLY_UNDEFINED_BUYER_VAR}",
            },
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(ValueError, match="TOTALLY_UNDEFINED_BUYER_VAR"):
            BuyerSettings.from_yaml(path)
