# tests/stage10/test_sid_integration.py
# Q-010 — Sidecar integration contract, SID-1..7.
#
#   SID-1  manifest/catalog/oauth origins come from public_base_url (subdomain)
#          or the request (same-origin reverse proxy). `openstore init` emits the
#          deployment mode into the config.
#   SID-2  /health/live always alive; /health/ready reflects readiness; agent
#          traffic is refused (503) until ready; discovery stays open.
#   SID-5  WebAuthn RP ID must equal the public_base_url host; CORS origin is
#          restricted to the merchant origin.
#   SID-6  __version__ exposes the package version; manifests carry it.
#   SID-7  /internal/metrics serves Prometheus text exposition with gauges.

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openstore import __version__
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


def _clear() -> None:
    import openstore.core.database as db_mod
    db_mod._engine = None


def _cfg(
    *,
    name: str = "Gelateria Milano",
    key_id: str = "rzp_test_xxx",
    public_base_url: str | None = None,
    rp_id: str = "localhost",
    origin: str = "http://localhost:8000",
) -> Settings:
    return Settings(
        merchant=MerchantConfig(name=name, currency="INR"),
        razorpay=RazorpayConfig(key_id=key_id, key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1, merchant_trace_channel_id=2,
            money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id=rp_id, rp_name="OpenStore", origin=origin),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
        public_base_url=public_base_url,
    )


@pytest.fixture()
def client() -> TestClient:
    _clear()
    cfg = _cfg()
    init_database(cfg)
    return TestClient(create_app(cfg))


# ---------------------------------------------------------------------------
# SID-1 — origin resolution from public_base_url (subdomain) vs request
# ---------------------------------------------------------------------------


class TestSID1Origin:
    def test_subdomain_uses_public_base_url(self):
        _clear()
        cfg = _cfg(
            public_base_url="https://openstore.gelateria.example",
            rp_id="openstore.gelateria.example",
            origin="https://openstore.gelateria.example",
        )
        init_database(cfg)
        c = TestClient(create_app(cfg))
        m = c.get("/.well-known/agent-commerce.json").json()
        assert m["storefront"] == "https://openstore.gelateria.example/"
        assert m["catalog_endpoint"] == "https://openstore.gelateria.example/agent/catalog"
        oauth = c.get("/.well-known/oauth-authorization-server").json()
        # oauth origin reflects public_base_url, not the request host.
        assert oauth["issuer"] == "https://openstore.gelateria.example"

    def test_same_origin_derives_from_request(self, client):
        m = client.get("/.well-known/agent-commerce.json").json()
        # no public_base_url -> origin from the request (http://testserver by TestClient)
        assert m["storefront"].startswith("http://")


# ---------------------------------------------------------------------------
# SID-2 — health/live, health/ready, agent-traffic gate
# ---------------------------------------------------------------------------


class TestSID2Readiness:
    def test_health_live_always_ok(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    def test_health_ready_true_when_configured(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_live_razorpay_keys_are_not_ready(self):
        _clear()
        cfg = _cfg(key_id="rzp_live_ABCDEF")
        init_database(cfg)  # schema present, but LIVE keys
        c = TestClient(create_app(cfg))
        r = c.get("/health/ready")
        assert r.status_code == 503
        assert "razorpay_test_keys" in "\n".join(r.json()["reason_codes"])
        # gated agent traffic refused
        g = c.post("/agent/mcp", json={"tool": "search_products", "arguments": {}})
        assert g.status_code == 503
        # discovery stays open for agents to decide on retry
        assert c.get("/.well-known/agent-card.json").status_code == 200


# ---------------------------------------------------------------------------
# SID-5 — origin security boundary
# ---------------------------------------------------------------------------


class TestSID5OriginSecurity:
    def test_webauthn_rp_id_must_match_public_base_url_host(self):
        _clear()
        cfg = _cfg(public_base_url="https://store.example", rp_id="other.example")
        init_database(cfg)
        with pytest.raises(ValueError, match="SID-5 origin mismatch"):
            create_app(cfg)

    def test_cors_origin_restricted_to_merchant_origin(self):
        _clear()
        cfg = _cfg(public_base_url="https://store.example", rp_id="store.example",
                   origin="https://store.example")
        init_database(cfg)
        c = TestClient(create_app(cfg))
        # friendly origin allowed preflight
        ok = c.options(
            "/agent/mcp",
            headers={
                "Origin": "https://store.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" in ok.headers
        assert ok.headers["access-control-allow-origin"] == "https://store.example"
        # a foreign origin is not allowed
        bad = c.options(
            "/agent/mcp",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------------------
# SID-6 — version reflection
# ---------------------------------------------------------------------------


class TestSID6Version:
    def test_version_importable(self):
        assert isinstance(__version__, str) and __version__

    def test_version_matches_pyproject(self, client):
        import tomllib
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
        )
        assert pyproject["project"]["version"] == __version__ == "0.1.0"

    def test_agent_card_exposes_version(self, client):
        card = client.get("/.well-known/agent-card.json").json()
        assert card["version"] == __version__

    def test_fastapi_app_version(self, client):
        assert client.app.version == __version__


# ---------------------------------------------------------------------------
# SID-7 — metrics
# ---------------------------------------------------------------------------


class TestSID7Metrics:
    def test_metrics_prometheus_text(self, client):
        r = client.get("/internal/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        assert "# HELP openstore_health_ready" in body
        assert "# TYPE openstore_health_ready gauge" in body
        assert "openstore_health_ready 1" in body
        # required metric families are present
        assert "openstore_reconciliation_drift_total" in body

    def test_ledger_balances_emitted_per_account(self):
        _clear()
        cfg = _cfg()
        init_database(cfg)
        # seed a capture so at least one economic account appears
        from openstore.core.database import get_session
        from openstore.core.ledger import create_capture_entry
        s = get_session(cfg)
        create_capture_entry(s, "tr_seed", "cli_seed", "chk_seed", 40000)
        s.commit()
        s.close()
        c = TestClient(create_app(cfg))
        body = c.get("/internal/metrics").text
        assert 'openstore_ledger_balance_minor{label="merchant_revenue"}' in body

    def test_metrics_gate_not_applied_to_metrics(self, client):
        # metrics is an infra route; even if not ready it should be reachable
        _clear()
        cfg = _cfg(key_id="rzp_live_ABC")
        init_database(cfg)
        c = TestClient(create_app(cfg))
        r = c.get("/internal/metrics")
        assert r.status_code == 200
        assert "openstore_health_ready 0" in r.text
