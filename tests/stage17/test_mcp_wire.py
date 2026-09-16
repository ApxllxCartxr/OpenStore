# tests/stage17/test_mcp_wire.py
# Stage 17 (DECISION-036) — MCP JSON-RPC 2.0 wire conformance on /agent/mcp.
# Hard cutover: only the wire envelope is accepted; the legacy
# {"tool","arguments"} shape is rejected -32600 and never executed.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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
from openstore.core.database import init_database
from openstore.server import create_app
from openstore.surfaces.mcp_server import MCP_PROTOCOL_VERSION, TOOL_NAMES

GOLDEN = Path(__file__).resolve().parents[1] / "GOLDEN"


@pytest.fixture()
def config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Gelateria Milano", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxxxxxxx", key_secret="test"),
        discord=DiscordConfig(
            bot_token="t",
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
def client(config: Settings):
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)
    app = create_app(config)
    return TestClient(app)


def _rpc(client, method, params=None, req_id=1, headers=None):
    envelope: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        envelope["params"] = params
    return client.post("/agent/mcp", json=envelope, headers=headers or {})


def _tool_text(body):
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body
    result = body["result"]
    assert result["content"][0]["type"] == "text"
    return result, json.loads(result["content"][0]["text"])


class TestInitialize:
    def test_initialize_exact_version(self, client):
        r = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["protocolVersion"] == MCP_PROTOCOL_VERSION == "2025-06-18"
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["serverInfo"]["name"] == "openstore"
        assert isinstance(result["serverInfo"]["version"], str)

    def test_initialize_unknown_version_negotiates_down(self, client):
        r = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})
        assert r.status_code == 200
        assert r.json()["result"]["protocolVersion"] == "2025-06-18"

    def test_initialized_notification_accepted(self, client):
        r = client.post("/agent/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202
        assert r.content == b""

    def test_ping(self, client):
        r = _rpc(client, "ping")
        assert r.status_code == 200
        assert r.json()["result"] == {}


class TestToolsList:
    def test_lists_all_twenty_three_tools_with_schemas(self, client):
        r = _rpc(client, "tools/list")
        assert r.status_code == 200
        tools = r.json()["result"]["tools"]
        assert len(tools) == 23
        names = [t["name"] for t in tools]
        assert names == sorted(names)
        assert set(names) == set(TOOL_NAMES)
        for t in tools:
            assert isinstance(t["description"], str) and t["description"]
            assert t["inputSchema"]["type"] == "object"
            assert isinstance(t["inputSchema"]["required"], list)

    def test_list_matches_registry(self, client):
        import json as _json

        registry = _json.loads((GOLDEN.parents[1] / "REGISTRY.json").read_text())
        r = _rpc(client, "tools/list")
        assert {t["name"] for t in r.json()["result"]["tools"]} == set(registry["mcp_tools"])


class TestToolsCall:
    def test_search_products_happy_path(self, client):
        r = _rpc(
            client,
            "tools/call",
            {"name": "search_products", "arguments": {"query": "", "limit": 5}},
        )
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert tool["success"] is True
        assert isinstance(tool["data"]["items"], list)

    def test_unknown_tool_is_business_error_not_envelope_error(self, client):
        r = _rpc(client, "tools/call", {"name": "nope_tool", "arguments": {}})
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is True
        assert tool["success"] is False
        assert tool["error"]["reason_code"] == "auth.unknown_tool"

    def test_missing_identifier_is_business_error(self, client):
        # Q-040: get_product accepts {sku} | {id} | {catalog: {id}}, so {}
        # passes envelope validation and answers isError, not -32602.
        r = _rpc(client, "tools/call", {"name": "get_product", "arguments": {}})
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is True
        assert tool["error"]["reason_code"] == "catalog.sku_not_found"

    def test_scoped_tool_without_scope_is_business_error(self, client):
        """The scope gate raises CommerceError outside the per-tool try — the
        wire maps it to isError content, not an unhandled 500 (Q-036)."""
        r = _rpc(
            client,
            "tools/call",
            {
                "name": "create_cart",
                "arguments": {
                    "merchant_id": "m",
                    "items": [],
                    "policy_id": "p",
                    "cart_hash": "h",
                },
            },
        )
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is True
        assert tool["error"]["reason_code"] == "auth.insufficient_scope"

    def test_call_as_notification_returns_202(self, client):
        r = client.post(
            "/agent/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "search_products", "arguments": {}},
            },
        )
        assert r.status_code == 202


class TestEnvelopeErrors:
    def test_unknown_method(self, client):
        r = _rpc(client, "resources/list")
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32601

    def test_legacy_shape_rejected_and_never_executed(self, client):
        r = client.post(
            "/agent/mcp", json={"tool": "search_products", "arguments": {"query": "x"}}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == -32600

    def test_invalid_json_is_parse_error(self, client):
        r = client.post(
            "/agent/mcp",
            content=b'{"jsonrpc": "2.0",',
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == -32700

    def test_batch_rejected(self, client):
        r = client.post(
            "/agent/mcp",
            json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == -32600

    def test_invalid_bearer_is_401_with_id_echo(self, client):
        r = client.post(
            "/agent/mcp",
            headers={"Authorization": "Bearer bogus"},
            json={"jsonrpc": "2.0", "id": 99, "method": "tools/list"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["id"] == 99
        assert body["error"]["code"] == -32001
