# tests/stage20/test_ucp_catalog_aliases.py
# Stage 20 (Q-040 / DECISION-040) — UCP MCP catalog binding aliases.
# search_catalog / lookup_catalog as thin adapters with the UCP {meta, catalog}
# arguments and {ucp, products} envelope; get_product accepts the UCP id shape.

from __future__ import annotations

import json

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

META = {"ucp-agent": {"profile": "https://platform.example/profiles/test.json"}}


@pytest.fixture()
def config(tmp_path):
    import openstore.surfaces.catalog as catalog_mod

    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        """
items:
  - sku: GEL-VAN-500
    name: Madagascar Vanilla 500ml
    unit_minor: 21000
    tags: [vegan, dairy-free]
    description: Slow-churned, cashew-base vanilla.
  - sku: GEL-HAZ-500
    name: Roasted Hazelnut 500ml
    unit_minor: 23000
    tags: [vegan]
    description: Piedmont hazelnuts, dark roast.
"""
    )
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
        catalog_path=str(catalog),
    )


@pytest.fixture()
def client(config: Settings):
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)
    app = create_app(config)
    return TestClient(app)


def _rpc(client, method, params=None, req_id=1):
    envelope: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        envelope["params"] = params
    return client.post("/agent/mcp", json=envelope)


def _tool_text(body):
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body
    result = body["result"]
    assert result["content"][0]["type"] == "text"
    return result, json.loads(result["content"][0]["text"])


def _call(client, name, arguments):
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments})


class TestAliasDiscovery:
    def test_list_advertises_aliases_with_schemas(self, client):
        r = _rpc(client, "tools/list")
        tools = {t["name"]: t for t in r.json()["result"]["tools"]}
        for name in ("search_catalog", "lookup_catalog"):
            assert name in tools
            assert tools[name]["inputSchema"]["type"] == "object"
            assert isinstance(tools[name]["description"], str)
        assert tools["lookup_catalog"]["inputSchema"]["required"] == ["catalog"]
        assert tools["search_catalog"]["inputSchema"]["required"] == []
        assert tools["get_product"]["inputSchema"]["required"] == []


class TestSearchCatalog:
    def test_ucp_shape_returns_envelope(self, client):
        r = _call(
            client,
            "search_catalog",
            {"meta": META, "catalog": {"query": "vanilla"}},
        )
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        data = tool["data"]
        assert data["ucp"]["version"] == "2026-08-25"
        assert "dev.ucp.shopping.catalog.search" in data["ucp"]["capabilities"]
        assert len(data["products"]) == 1
        product = data["products"][0]
        assert product["id"] == "GEL-VAN-500"
        assert product["title"] == "Madagascar Vanilla 500ml"
        assert product["price_range"]["min"] == {"amount": 21000, "currency": "INR"}
        assert product["price_range"]["max"] == {"amount": 21000, "currency": "INR"}

    def test_missing_meta_tolerated_and_flat_shape_works(self, client):
        r = _call(client, "search_catalog", {"query": "hazelnut"})
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert [p["id"] for p in tool["data"]["products"]] == ["GEL-HAZ-500"]

    def test_pagination_limit_honored(self, client):
        r = _call(
            client,
            "search_catalog",
            {"meta": META, "catalog": {"pagination": {"limit": 1}}},
        )
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert len(tool["data"]["products"]) == 1

    def test_context_and_filters_accepted_and_ignored(self, client):
        r = _call(
            client,
            "search_catalog",
            {
                "meta": META,
                "catalog": {
                    "query": "",
                    "context": {"address_country": "IN"},
                    "filters": {"categories": ["Gelato"]},
                },
            },
        )
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert len(tool["data"]["products"]) == 2


class TestLookupCatalog:
    def test_round_trip(self, client):
        r = _call(
            client,
            "lookup_catalog",
            {"meta": META, "catalog": {"ids": ["GEL-VAN-500", "GEL-HAZ-500"]}},
        )
        assert r.status_code == 200
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        data = tool["data"]
        assert data["ucp"]["version"] == "2026-08-25"
        assert "dev.ucp.shopping.catalog.lookup" in data["ucp"]["capabilities"]
        assert {p["id"] for p in data["products"]} == {"GEL-VAN-500", "GEL-HAZ-500"}
        assert "messages" not in data

    def test_partial_miss_is_success_with_messages(self, client):
        r = _call(
            client,
            "lookup_catalog",
            {"meta": META, "catalog": {"ids": ["GEL-VAN-500", "NOPE-001"]}},
        )
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert [p["id"] for p in tool["data"]["products"]] == ["GEL-VAN-500"]
        assert tool["data"]["messages"] == [
            {"type": "info", "code": "not_found", "content": "NOPE-001"}
        ]

    def test_all_miss_is_success_with_empty_products(self, client):
        r = _call(client, "lookup_catalog", {"catalog": {"ids": ["NOPE-001"]}})
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert tool["data"]["products"] == []

    def test_missing_catalog_is_invalid_params(self, client):
        r = _call(client, "lookup_catalog", {"meta": META})
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32602


class TestGetProductShapes:
    def test_ucp_shape(self, client):
        r = _call(client, "get_product", {"meta": META, "catalog": {"id": "GEL-VAN-500"}})
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert tool["data"]["item"]["sku"] == "GEL-VAN-500"
        assert tool["data"]["product"]["id"] == "GEL-VAN-500"
        assert tool["data"]["ucp"]["version"] == "2026-08-25"

    def test_legacy_sku_shape_keeps_working(self, client):
        r = _call(client, "get_product", {"sku": "GEL-HAZ-500"})
        result, tool = _tool_text(r.json())
        assert result["isError"] is False
        assert tool["data"]["item"]["sku"] == "GEL-HAZ-500"
        assert tool["data"]["product"]["id"] == "GEL-HAZ-500"

    def test_unknown_sku_is_business_error(self, client):
        r = _call(client, "get_product", {"sku": "NOPE-001"})
        result, tool = _tool_text(r.json())
        assert result["isError"] is True
        assert tool["error"]["reason_code"] == "catalog.sku_not_found"


class TestUcpManifest:
    def test_manifest_advertises_catalog_capabilities(self, client):
        data = client.get("/.well-known/ucp").json()
        service = data["services"][0]
        by_id = {c["id"]: c for c in service["capabilities"]}
        assert by_id["dev.ucp.shopping.catalog.search"]["operations"] == ["search_catalog"]
        assert by_id["dev.ucp.shopping.catalog.lookup"]["operations"] == [
            "lookup_catalog",
            "get_product",
        ]
