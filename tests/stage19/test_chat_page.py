# tests/stage19/test_chat_page.py
# Stage 19 (DECISION-039) — anonymous storefront-lite page. Same-origin only,
# zero dependencies; checkout stays on the buyer agent and studios.

from __future__ import annotations

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
from openstore.core.health import is_gated
from openstore.server import create_app


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


class TestChatPage:
    def test_serves_html(self, client):
        r = client.get("/chat")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        html = r.text
        # Contract markers the page JS depends on.
        for marker in (
            'id="chat-search"',
            'id="chat-catalog"',
            'id="chat-offers"',
            'id="chat-evidence"',
            "/agent/catalog",
            "/.well-known/agent-campaigns.json",
            "/.well-known/agent-commerce.json",
            "/orders/",
            "/evidence/view",
            "/intent/studio",
            "/campaign/studio",
            "unit_minor",
        ):
            assert marker in html, f"missing marker: {marker}"

    def test_no_mutation_surface(self, client):
        """Slice A takes no payment input: no checkout POST, no token field."""
        html = client.get("/chat").text.lower()
        assert "checkout_initiate" not in html
        assert "client_secret" not in html
        assert "password" not in html

    def test_ungated_discovery_surface(self):
        assert is_gated("/chat") is False
