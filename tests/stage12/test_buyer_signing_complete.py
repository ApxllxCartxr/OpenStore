# tests/stage12/test_buyer_signing_complete.py
# S12 step 8: the buyer process's own POST /internal/signing-complete
# (surfaces/buyer_internal.py). This is the receiving half of the merchant's
# best-effort "some buyer may have finished signing" ping.
#
# SECURITY: the endpoint's whole point is that the request body carries no
# authority — only an independently fetched resolve_policy result (over the
# buyer's own MCP channel) can trigger a resume/DM. These tests exist
# specifically to pin that: a forged notification for a merchant with no
# active policy must produce no resume and no DM, and a body that claims a
# policy_id/caps must be ignored in favor of the real resolve_policy result.

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.agents.buyer_agent import BuyerAgent
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
from openstore.core.database import get_session, init_database
from openstore.core.shopping_session import create_session
from openstore.surfaces import buyer_internal as buyer_internal_module
from openstore.surfaces.buyer_internal import create_buyer_internal_router


def _config() -> Settings:
    # BuyerSettings and Settings share the attributes this endpoint touches
    # (discord, database) — reusing Settings here mirrors buyer_cli.py's own
    # cast(Settings, BuyerSettings) duck-typing, without needing a full
    # BuyerSettings + merchant-origin YAML just for this test.
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="k", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="l", rp_name="x", origin="http://l"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


class _FakeHttpClient:
    """Stands in for HttpMCPClient — records every call and returns a
    caller-controlled resolve_policy result."""

    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool
    ) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return self.response


class _FakeFederatingMCPClient:
    def __init__(self, client: _FakeHttpClient):
        self._client = client

    def client_for(self, merchant_id: str) -> _FakeHttpClient:
        return self._client


@pytest.fixture()
def db(monkeypatch):
    config = _config()
    import openstore.core.database as _db

    _db._engine = None
    init_database(config)
    return config


def _build_app(config: Settings, fake_client: _FakeHttpClient) -> TestClient:
    mcp = _FakeFederatingMCPClient(fake_client)
    agent = BuyerAgent(config, mcp, resume_url="http://127.0.0.1:8765/internal/signing-complete")
    app = FastAPI()
    app.include_router(create_buyer_internal_router(config, agent))
    return TestClient(app), agent


def _park_session(config: Settings, *, chat_user_id: str, chat_channel_id: str) -> None:
    session = get_session(config)
    try:
        create_session(
            session,
            chat_platform="discord",
            chat_user_id=chat_user_id,
            chat_channel_id=chat_channel_id,
            policy_id="FEDERATED",
            trace_id="trace_1",
            goal="get gelato",
            messages=[],
        )
        session.commit()
    finally:
        session.close()


class TestForgedOrStaleNotification:
    def test_unknown_token_no_resume_no_dm(self, db, monkeypatch):
        """The most important case: a token this buyer never minted (forged
        or replayed) resolves to nothing — no MCP call, no DM."""
        config = db
        fake_client = _FakeHttpClient({"success": True, "data": {"policy_id": "pol_1"}})
        client, agent = _build_app(config, fake_client)

        dm_calls: list[Any] = []
        monkeypatch.setattr(
            buyer_internal_module,
            "send_dm",
            lambda *a, **k: dm_calls.append((a, k)) or _noop_coro(),
        )

        resp = client.post(
            "/internal/signing-complete",
            json={"handoff_token": "never-issued", "merchant_id": "gelateria-milano"},
        )
        assert resp.status_code == 202
        assert fake_client.calls == []  # never even asked resolve_policy
        assert dm_calls == []

    def test_no_active_policy_no_resume_no_dm(self, db, monkeypatch):
        """A real token, but resolve_policy comes back with no active
        policy: no resume, no DM."""
        config = db
        fake_client = _FakeHttpClient(
            {"success": False, "error": {"reason_code": "authority.policy_unsigned"}}
        )
        client, agent = _build_app(config, fake_client)
        agent.record_pending_handoff(
            "tok_1",
            merchant_id="gelateria-milano",
            chat_platform="discord",
            chat_user_id="u1",
            chat_channel_id="c1",
        )
        _park_session(config, chat_user_id="u1", chat_channel_id="c1")

        dm_calls: list[Any] = []
        monkeypatch.setattr(
            buyer_internal_module,
            "send_dm",
            lambda *a, **k: dm_calls.append((a, k)) or _noop_coro(),
        )

        resp = client.post(
            "/internal/signing-complete",
            json={"handoff_token": "tok_1", "merchant_id": "gelateria-milano"},
        )
        assert resp.status_code == 202
        assert fake_client.calls == [("resolve_policy", {"user_id": "discord:u1"})]
        assert dm_calls == []

    def test_body_claiming_policy_is_ignored(self, db, monkeypatch):
        """A body that adds policy_id/caps fields is not even a schema this
        endpoint accepts beyond handoff_token/merchant_id — and even with a
        real token, only the resolve_policy RESULT decides the outcome, not
        anything asserted in the body."""
        config = db
        fake_client = _FakeHttpClient(
            {"success": False, "error": {"reason_code": "authority.policy_unsigned"}}
        )
        client, agent = _build_app(config, fake_client)
        agent.record_pending_handoff(
            "tok_2",
            merchant_id="gelateria-milano",
            chat_platform="discord",
            chat_user_id="u2",
            chat_channel_id="c2",
        )
        _park_session(config, chat_user_id="u2", chat_channel_id="c2")

        dm_calls: list[Any] = []
        monkeypatch.setattr(
            buyer_internal_module,
            "send_dm",
            lambda *a, **k: dm_calls.append((a, k)) or _noop_coro(),
        )

        resp = client.post(
            "/internal/signing-complete",
            json={
                "handoff_token": "tok_2",
                "merchant_id": "gelateria-milano",
                "policy_id": "pol_evil",
                "max_spend_per_tx_minor": 999_999_999,
            },
        )
        assert resp.status_code == 202
        # The forged extra fields changed nothing: resolve_policy still says
        # unsigned, so still no DM.
        assert dm_calls == []


class TestVerifiedActivePolicy:
    def test_active_policy_resumes_and_calls_resolve_policy(self, db, monkeypatch):
        """A real token AND an active policy (as independently verified via
        resolve_policy) -> the buyer is DM'd. The MCP call actually
        happened — this is not just checking the outcome."""
        config = db
        fake_client = _FakeHttpClient(
            {"success": True, "data": {"policy_id": "pol_real", "allowed_tags": []}}
        )
        client, agent = _build_app(config, fake_client)
        agent.record_pending_handoff(
            "tok_3",
            merchant_id="gelateria-milano",
            chat_platform="discord",
            chat_user_id="u3",
            chat_channel_id="c3",
        )
        _park_session(config, chat_user_id="u3", chat_channel_id="c3")

        dm_calls: list[Any] = []

        async def _fake_send_dm(cfg, user_id, message):
            dm_calls.append((user_id, message))

        monkeypatch.setattr(buyer_internal_module, "send_dm", _fake_send_dm)

        resp = client.post(
            "/internal/signing-complete",
            json={"handoff_token": "tok_3", "merchant_id": "gelateria-milano"},
        )
        assert resp.status_code == 202

        # The verification actually happened, with the right identity.
        assert fake_client.calls == [("resolve_policy", {"user_id": "discord:u3"})]
        assert len(dm_calls) == 1
        assert dm_calls[0][0] == "u3"

    def test_active_policy_but_no_parked_session_no_dm(self, db, monkeypatch):
        """Active policy but nothing parked to resume — do nothing, no DM,
        no crash."""
        config = db
        fake_client = _FakeHttpClient({"success": True, "data": {"policy_id": "pol_real"}})
        client, agent = _build_app(config, fake_client)
        agent.record_pending_handoff(
            "tok_4",
            merchant_id="gelateria-milano",
            chat_platform="discord",
            chat_user_id="u4",
            chat_channel_id="c4",
        )
        # No _park_session call this time.

        dm_calls: list[Any] = []
        monkeypatch.setattr(
            buyer_internal_module,
            "send_dm",
            lambda *a, **k: dm_calls.append((a, k)) or _noop_coro(),
        )

        resp = client.post(
            "/internal/signing-complete",
            json={"handoff_token": "tok_4", "merchant_id": "gelateria-milano"},
        )
        assert resp.status_code == 202
        assert dm_calls == []


async def _noop_coro() -> None:
    return None
