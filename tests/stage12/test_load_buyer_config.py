# tests/stage12/test_load_buyer_config.py
# S12 step 7: load_buyer_config's origin-verification pass — fetches each
# merchant's manifest and asserts merchant_id/mcp_endpoint match the
# whitelist entry (R0.5: a typo'd base_url must never result in silently
# shopping the wrong store). Fakes httpx.get so this needs no live server.

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from openstore.buyer_config import load_buyer_config

CONFIG: dict = {
    "discord": {
        "bot_token": "test-token",
        "buyer_trace_channel_id": 1,
        "merchant_trace_channel_id": 2,
        "money_trace_channel_id": 3,
        "alerts_channel_id": 4,
    },
    "merchants": [
        {
            "name": "Gelateria Milano",
            "base_url": "http://localhost:8000",
            "client_id": "cid",
            "client_secret": "secret",
        },
    ],
}


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "buyer.yaml"
    path.write_text(yaml.safe_dump(CONFIG))
    return path


class TestLoadBuyerConfigVerifiesOrigins:
    def test_matching_manifest_loads_cleanly(self, tmp_path: Path, monkeypatch) -> None:
        def fake_get(url: str, timeout: float = 8.0) -> _FakeResponse:
            assert url == "http://localhost:8000/.well-known/agent-commerce.json"
            return _FakeResponse(
                {
                    "merchant": {"id": "gelateria-milano", "name": "Gelateria Milano"},
                    "mcp_endpoint": "http://localhost:8000/agent/mcp",
                }
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        config = load_buyer_config(_write_config(tmp_path))
        assert config.merchants[0].merchant_id == "gelateria-milano"

    def test_merchant_id_mismatch_fails_loud(self, tmp_path: Path, monkeypatch) -> None:
        def fake_get(url: str, timeout: float = 8.0) -> _FakeResponse:
            return _FakeResponse(
                {
                    "merchant": {"id": "some-other-store", "name": "Wrong"},
                    "mcp_endpoint": "http://localhost:8000/agent/mcp",
                }
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        with pytest.raises(RuntimeError, match="Gelateria Milano"):
            load_buyer_config(_write_config(tmp_path))

    def test_mcp_endpoint_mismatch_fails_loud(self, tmp_path: Path, monkeypatch) -> None:
        def fake_get(url: str, timeout: float = 8.0) -> _FakeResponse:
            return _FakeResponse(
                {
                    "merchant": {"id": "gelateria-milano", "name": "Gelateria Milano"},
                    "mcp_endpoint": "http://evil.example.com/agent/mcp",
                }
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        with pytest.raises(RuntimeError, match="mcp_endpoint"):
            load_buyer_config(_write_config(tmp_path))

    def test_verify_origins_false_skips_the_network_call(self, tmp_path: Path, monkeypatch) -> None:
        def fake_get(*args: Any, **kwargs: Any) -> _FakeResponse:
            raise AssertionError("httpx.get must not be called when verify_origins=False")

        monkeypatch.setattr(httpx, "get", fake_get)
        config = load_buyer_config(_write_config(tmp_path), verify_origins=False)
        assert config.merchants[0].name == "Gelateria Milano"
