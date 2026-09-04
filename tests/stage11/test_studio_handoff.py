# tests/stage11/test_studio_handoff.py
# S11 Phase 2: the /intent/studio?token=... seam resolves a real handoff (200,
# not the Phase 1 501 stub) and the assertion/complete push + auto-resume path
# consumes the handoff exactly once.

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
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
from openstore.core.database import get_session, init_database
from openstore.core.handoff import create_handoff, resolve_handoff
from openstore.core.webauthn_rp import ChallengeStore
from openstore.models import HandoffKind
from openstore.surfaces import studio as studio_module
from openstore.surfaces.studio import policy_studio_router


@pytest.fixture()
def rp_settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="k", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1, merchant_trace_channel_id=2,
            money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="openstore.test", rp_name="x", origin="https://openstore.test"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session_factory(rp_settings: Settings):
    import openstore.core.database as _db

    prev = _db._engine
    _db._engine = None
    init_database(rp_settings)
    try:
        yield lambda: get_session(rp_settings)
    finally:
        from openstore.core.database import get_engine

        get_engine(rp_settings).dispose()
        _db._engine = prev


@pytest.fixture()
def client(rp_settings: Settings, session_factory) -> TestClient:
    store = ChallengeStore()
    app = FastAPI()
    app.include_router(policy_studio_router(rp_settings, session_factory=session_factory, challenge_store=store))
    with TestClient(app) as c:
        yield c


def test_studio_page_token_path_returns_200(client: TestClient, session_factory):
    session = session_factory()
    try:
        handoff = create_handoff(
            session,
            kind=HandoffKind.POLICY,
            merchant_id="test",
            chat_platform="discord",
            chat_user_id="42",
            chat_channel_id="99",
            request_text="vegan gelato",
        )
        session.commit()
        token = handoff.token
    finally:
        session.close()

    res = client.get(f"/intent/studio?token={token}")
    assert res.status_code == 200
    assert json.dumps("discord:42") in res.text
    assert token in res.text  # embedded for the sign-time handoff_token field


def test_studio_page_token_path_expired_returns_410(client: TestClient, session_factory):
    session = session_factory()
    try:
        handoff = create_handoff(
            session,
            kind=HandoffKind.POLICY,
            merchant_id="test",
            chat_platform="discord",
            chat_user_id="43",
            chat_channel_id="99",
            request_text="pistachio gelato",
            ttl_seconds=-10,
        )
        session.commit()
        token = handoff.token
    finally:
        session.close()

    res = client.get(f"/intent/studio?token={token}")
    assert res.status_code == 410
    assert res.json()["detail"]["reason_code"] == "authority.handoff_expired"


async def test_consume_and_resume_is_single_use(rp_settings: Settings, session_factory, monkeypatch):
    """The push + auto-resume path (studio._consume_and_resume) consumes the
    handoff exactly once; a second attempt against the same token surfaces
    authority.handoff_consumed in the response rather than raising (the
    policy was already signed and must not be undone)."""
    calls: list[str] = []

    async def _fake_resume(
        config, mcp_client, *, chat_user_id, request_text, policy_id, trace_id,
        chat_platform=None, chat_channel_id=None,
    ):
        calls.append(request_text)
        return {"allowed": True, "checkout_id": "chk_fake"}

    async def _fake_dm(config, user_id, message):
        return None

    monkeypatch.setattr(studio_module, "resume_after_signing", _fake_resume)
    monkeypatch.setattr(studio_module, "send_dm", _fake_dm)

    session = session_factory()
    try:
        handoff = create_handoff(
            session,
            kind=HandoffKind.POLICY,
            merchant_id="test",
            chat_platform="discord",
            chat_user_id="44",
            chat_channel_id="99",
            request_text="mango sorbet",
        )
        session.commit()
        token = handoff.token
    finally:
        session.close()

    first = await studio_module._consume_and_resume(rp_settings, session_factory, token, "pol_xyz")
    assert first == {"resumed": True, "shop_result": {"allowed": True, "checkout_id": "chk_fake"}}
    assert calls == ["mango sorbet"]

    second = await studio_module._consume_and_resume(rp_settings, session_factory, token, "pol_xyz")
    assert second == {"resumed": False, "reason_code": "authority.handoff_consumed"}
    assert calls == ["mango sorbet"]  # not called again

    from openstore.core.handoff import HandoffError

    session = session_factory()
    try:
        with pytest.raises(HandoffError):
            resolve_handoff(session, token)
    finally:
        session.close()
