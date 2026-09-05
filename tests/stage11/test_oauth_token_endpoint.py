# tests/stage11/test_oauth_token_endpoint.py
# POST /oauth/token — client_credentials grant for out-of-process buyer agents.
# The well-known /.well-known/oauth-authorization-server manifest advertises
# token_endpoint but until this endpoint existed no external process could
# obtain a bearer token (see task recon).

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
from openstore.core.database import init_database, session_scope
from openstore.core.oauth import register_client
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


@pytest.fixture()
def registered_client(config: Settings) -> tuple[str, str]:
    """A buyer OAuth client registered with the full federation scope set."""
    with session_scope(config) as session:
        client_id, client_secret = register_client(
            session,
            client_name="buyer-agent",
            redirect_uris=[],
            grant_types=["client_credentials"],
            scopes=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
        )
    return client_id, client_secret


class TestTokenEndpoint:
    def test_successful_token_exchange(self, client, registered_client):
        client_id, client_secret = registered_client
        r = client.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["token_type"] == "Bearer"
        assert isinstance(data["access_token"], str) and data["access_token"]
        assert data["expires_in"] == 3600
        assert set(data["scope"].split()) == {
            "catalog:read",
            "cart:write",
            "checkout:initiate",
            "checkout:confirm",
        }

    def test_token_accepted_by_mcp_for_scoped_tool(self, client, registered_client):
        client_id, client_secret = registered_client
        token_resp = client.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "cart:write",
            },
        )
        assert token_resp.status_code == 200
        access_token = token_resp.json()["access_token"]

        r = client.post(
            "/agent/mcp",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "tool": "create_cart",
                "arguments": {
                    "merchant_id": "merchant",
                    "items": [],
                    "policy_id": "nonexistent-policy",
                    "cart_hash": "hash",
                    "cart_version": 1,
                },
            },
        )
        assert r.status_code == 200
        data = r.json()
        # Whatever business-logic outcome, the cart:write scope must have been
        # accepted — never rejected as insufficient_scope or invalid_token.
        error = data.get("error", {})
        assert error.get("reason_code") not in ("auth.insufficient_scope", "invalid_token")

    def test_wrong_secret_rejected(self, client, registered_client):
        client_id, _client_secret = registered_client
        r = client.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": "wrong-secret",
            },
        )
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"

    def test_omitted_secret_rejected(self, client, registered_client):
        """Knowing a client_id must not be enough to mint a token.

        validate_client used to gate the comparison on the secret being
        supplied, so omitting it skipped verification entirely — an auth
        bypass onto the money tools once this route became reachable.
        """
        client_id, _client_secret = registered_client
        for body in (
            {"grant_type": "client_credentials", "client_id": client_id},
            {"grant_type": "client_credentials", "client_id": client_id, "client_secret": ""},
            {"grant_type": "client_credentials", "client_id": client_id, "client_secret": None},
        ):
            r = client.post("/oauth/token", json=body)
            assert r.status_code == 401, f"secret-less request was accepted: {body}"
            assert r.json()["error"] == "invalid_client"

    def test_unsupported_grant_type_rejected(self, client, registered_client):
        client_id, client_secret = registered_client
        r = client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"] == "unsupported_grant_type"

    def test_unregistered_scope_not_granted(self, config: Settings, client):
        with session_scope(config) as session:
            client_id, client_secret = register_client(
                session,
                client_name="narrow-buyer",
                redirect_uris=[],
                grant_types=["client_credentials"],
                scopes=["catalog:read"],
            )

        r = client.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "catalog:read cart:write checkout:confirm",
            },
        )
        assert r.status_code == 200
        granted = set(r.json()["scope"].split())
        assert granted == {"catalog:read"}
        assert "cart:write" not in granted
        assert "checkout:confirm" not in granted
