# tests/stage06/test_surfaces.py
# Stage 6 — Agent surfaces: MCP tools, well-knowns, catalog

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


class TestAgentCommerceManifest:
    def test_manifest_has_version_0_2(self, client):
        r = client.get("/.well-known/agent-commerce.json")
        assert r.status_code == 200
        data = r.json()
        assert data["version"] == "0.2"

    def test_manifest_has_protocols_array(self, client):
        r = client.get("/.well-known/agent-commerce.json")
        data = r.json()
        assert "protocols" in data
        names = [p["name"] for p in data["protocols"]]
        assert "mcp" in names
        assert "ucp" in names

    def test_manifest_advertises_only_implemented_protocols(self, client):
        """DECISION-026: the manifest carried an `acp` entry at version
        "2024-11-01" — a release ACP never published — pointing at /agent/acp,
        which returns not_implemented. A manifest is a promise; an ACP-aware
        agent that trusted it would fail on contact."""
        data = client.get("/.well-known/agent-commerce.json").json()
        assert "acp" not in [p["name"] for p in data["protocols"]]
        assert client.post("/agent/acp").json()["error"] == "not_implemented"

    def test_ucp_manifest_declares_only_working_capabilities(self, client):
        r = client.get("/.well-known/ucp")
        assert r.status_code == 200
        data = r.json()
        service = data["services"][0]
        assert service["id"] == "dev.ucp.shopping"
        capability_ids = {c["id"] for c in service["capabilities"]}
        assert capability_ids == {
            "dev.ucp.shopping.checkout",
            "dev.ucp.shopping.discount",
            "dev.ucp.shopping.catalog.search",
            "dev.ucp.shopping.catalog.lookup",
        }
        # Fulfilment / order management are NOT implemented and must not appear.
        assert "dev.ucp.shopping.fulfillment" not in capability_ids
        # Every declared operation must be a real MCP tool (R0.2).
        registry = json.loads((Path(__file__).resolve().parents[2] / "REGISTRY.json").read_text())
        for capability in service["capabilities"]:
            for op in capability["operations"]:
                assert op in registry["mcp_tools"], op

    def test_manifest_has_campaigns_block(self, client):
        r = client.get("/.well-known/agent-commerce.json")
        data = r.json()
        assert "campaigns" in data
        assert "feed_endpoint" in data["campaigns"]

    def test_manifest_has_scopes(self, client):
        r = client.get("/.well-known/agent-commerce.json")
        data = r.json()
        assert "catalog:read" in data["auth"]["scopes_supported"]

    def test_merchant_id_derives_from_name(self, client):
        r = client.get("/.well-known/agent-commerce.json")
        data = r.json()
        assert "gelateria" in data["merchant"]["id"].lower()


class TestAgentPolicyManifest:
    def test_policy_manifest_has_hold_seconds(self, client):
        r = client.get("/.well-known/agent-policy.json")
        data = r.json()
        assert "aal_hold_seconds" in data
        assert data["aal_hold_seconds"]["AAL3"] == 0

    def test_policy_manifest_has_evidence_retention(self, client):
        r = client.get("/.well-known/agent-policy.json")
        data = r.json()
        assert "evidence_retention_days" in data
        assert data["evidence_retention_days"] == 540


class TestOAuthAuthorizationServer:
    def test_oauth_manifest_has_required_fields(self, client):
        r = client.get("/.well-known/oauth-authorization-server")
        data = r.json()
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "jwks_uri" in data

    def test_oauth_scopes_match_registry(self, client):
        r = client.get("/.well-known/oauth-authorization-server")
        scopes = r.json()["scopes_supported"]
        for scope in ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"]:
            assert scope in scopes


class TestMCPTools:
    @staticmethod
    def _call(client, name, arguments=None, req_id=1):
        return client.post(
            "/agent/mcp",
            json={
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
        )

    @staticmethod
    def _tool(body):
        assert body["jsonrpc"] == "2.0"
        result = body["result"]
        return result, json.loads(result["content"][0]["text"])

    def test_unknown_tool_returns_error(self, client):
        r = self._call(client, "unknown_tool_xxx")
        assert r.status_code == 200
        result, tool = self._tool(r.json())
        assert result["isError"] is True
        assert tool["success"] is False
        assert "unknown_tool" in tool["error"]["reason_code"]

    def test_list_campaigns_empty(self, client):
        r = self._call(client, "list_campaigns", {"merchant_id": "test"})
        assert r.status_code == 200
        result, tool = self._tool(r.json())
        assert result["isError"] is False
        assert tool["success"] is True
        assert tool["data"]["count"] == 0


class TestCatalogFeed:
    def test_catalog_endpoint_returns_items(self, client, tmp_path):
        import openstore.surfaces.catalog as catalog_mod

        catalog_mod.CATALOG_CACHE = None
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text("""
items:
  - sku: TEST-001
    name: Test Item
    unit_minor: 1000
    tags: [dairy-free]
    description: A test item
""")
        # Verify the endpoint returns the catalog feed
        r = client.get("/agent/catalog")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "currency" in data


class TestJWKSMerkleKeys:
    def test_jwks_returns_es256_key(self, client):
        r = client.get("/.well-known/poai-jwks.json")
        data = r.json()
        assert "keys" in data
        if data["keys"]:
            k = data["keys"][0]
            assert k["kty"] == "EC"
            assert k["crv"] == "P-256"


class TestSignedCampaignFeed:
    def test_signed_feed_returns_empty_when_no_campaigns(self, client):
        r = client.get("/.well-known/agent-campaigns.json")
        data = r.json()
        assert "campaigns" in data
        assert data["merchant_signature"] is None or isinstance(data["merchant_signature"], str)


class TestRouteTableSnapshot:
    EXPECTED_ROUTES = {
        "/healthz",
        "/.well-known/agent-commerce.json",
        "/.well-known/agent-policy.json",
        "/.well-known/agent-card.json",
        "/.well-known/oauth-authorization-server",
        "/.well-known/poai-jwks.json",
        "/.well-known/agent-campaigns.json",
        "/agent/catalog",
        "/agent/mcp",
        "/agent/acp",
        "/agent/campaigns",
        "/campaign/studio",
        "/intent/studio",
        "/",
    }

    def test_route_table_matches_registry(self, client):
        routes = set()
        for route in client.app.routes:
            if hasattr(route, "path"):
                routes.add(route.path)
        # All required routes should exist
        for r in self.EXPECTED_ROUTES:
            assert r in routes, f"Missing route: {r}"
