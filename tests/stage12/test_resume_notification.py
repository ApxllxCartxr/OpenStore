# tests/stage12/test_resume_notification.py
# S12 step 8: studio.py's _consume_and_resume, once a handoff carries a
# resume_url, must (a) still gate the legacy in-process resume_after_signing
# call on config.discord.buyer_bot_enabled — never call it for a federated
# buyer that doesn't host a live conversation in this process — and (b) POST
# a best-effort, minimal notification to resume_url that never fails or
# blocks the signing response even when the POST itself fails.

from __future__ import annotations

import httpx
import pytest
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
from openstore.core.handoff import create_handoff
from openstore.models import HandoffKind
from openstore.surfaces import studio as studio_module


def _settings(*, buyer_bot_enabled: bool) -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="k", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
            buyer_bot_enabled=buyer_bot_enabled,
        ),
        webauthn=WebAuthnConfig(
            rp_id="openstore.test", rp_name="x", origin="https://openstore.test"
        ),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session_factory():
    def _make(rp_settings: Settings):
        import openstore.core.database as _db

        _db._engine = None
        init_database(rp_settings)
        return lambda: get_session(rp_settings)

    return _make


async def _fake_dm(config, user_id, message):
    return None


def _make_handoff(factory, *, resume_url: str | None):
    session = factory()
    try:
        handoff = create_handoff(
            session,
            kind=HandoffKind.POLICY,
            merchant_id="test",
            chat_platform="discord",
            chat_user_id="77",
            chat_channel_id="99",
            request_text="mango sorbet",
            resume_url=resume_url,
        )
        session.commit()
        token = handoff.token
    finally:
        session.close()
    return token


class _RecordingAsyncClient:
    """Records every POST it's asked to make; caller controls the response
    via `responder`."""

    calls: list[dict] = []
    responder = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, json=None, **kwargs):
        type(self).calls.append({"url": url, "json": json})
        request = httpx.Request("POST", url, json=json)
        if type(self).responder is not None:
            return type(self).responder(url, json)
        return httpx.Response(200, json={}, request=request)


class TestResumeUrlAbsent:
    async def test_no_resume_url_legacy_behavior_unchanged(self, session_factory, monkeypatch):
        """resume_url absent = today's behavior exactly: legacy in-process
        resume runs (buyer_bot_enabled=True) and no notification is ever
        sent."""
        rp_settings = _settings(buyer_bot_enabled=True)
        factory = session_factory(rp_settings)
        token = _make_handoff(factory, resume_url=None)

        resumed_calls: list[str] = []

        async def _fake_resume(
            config,
            mcp_client,
            *,
            chat_user_id,
            request_text,
            policy_id,
            trace_id,
            chat_platform=None,
            chat_channel_id=None,
        ):
            resumed_calls.append(request_text)
            return {"allowed": True, "checkout_id": "chk_fake"}

        monkeypatch.setattr(studio_module, "resume_after_signing", _fake_resume)
        monkeypatch.setattr(studio_module, "send_dm", _fake_dm)

        # No resume_url on the handoff -> _notify_resume_url must never be
        # invoked at all; fail loud if it is.
        async def _must_not_be_called(*args, **kwargs):
            raise AssertionError("resume_url notification must not fire when resume_url is absent")

        monkeypatch.setattr(studio_module, "_notify_resume_url", _must_not_be_called)

        result = await studio_module._consume_and_resume(rp_settings, factory, token, "pol_xyz")

        assert result == {
            "resumed": True,
            "shop_result": {"allowed": True, "checkout_id": "chk_fake"},
        }
        assert resumed_calls == ["mango sorbet"]


class TestResumeUrlNotification:
    async def test_resume_url_gets_minimal_no_authority_body(self, session_factory, monkeypatch):
        rp_settings = _settings(buyer_bot_enabled=False)
        factory = session_factory(rp_settings)
        token = _make_handoff(factory, resume_url="http://127.0.0.1:8765/internal/signing-complete")

        legacy_calls: list[str] = []

        async def _fake_resume(*args, **kwargs):
            legacy_calls.append("called")
            return {"allowed": True}

        monkeypatch.setattr(studio_module, "resume_after_signing", _fake_resume)
        monkeypatch.setattr(studio_module, "send_dm", _fake_dm)

        _RecordingAsyncClient.calls = []
        _RecordingAsyncClient.responder = None
        monkeypatch.setattr(studio_module.httpx, "AsyncClient", _RecordingAsyncClient)

        result = await studio_module._consume_and_resume(rp_settings, factory, token, "pol_xyz")

        # buyer_bot_enabled=False (federated buyer, no live conversation in
        # this process) -> legacy resume_after_signing must NOT run.
        assert legacy_calls == []
        assert result == {"resumed": False}

        assert len(_RecordingAsyncClient.calls) == 1
        body = _RecordingAsyncClient.calls[0]["json"]
        # Minimal, no-authority body: exactly these two fields, nothing that
        # could be mistaken for policy material.
        assert set(body.keys()) == {"handoff_token", "merchant_id"}
        assert body["handoff_token"] == token
        assert body["merchant_id"] == "test"

    async def test_notification_failure_never_raises_or_blocks_signing(
        self, session_factory, monkeypatch
    ):
        """A timeout/connection error/non-2xx from the buyer's endpoint is
        logged and swallowed — the signing response the human is looking at
        must never fail because of it (R0.5 doesn't apply to a best-effort
        ping)."""
        rp_settings = _settings(buyer_bot_enabled=False)
        factory = session_factory(rp_settings)
        token = _make_handoff(factory, resume_url="http://127.0.0.1:8765/internal/signing-complete")

        monkeypatch.setattr(studio_module, "send_dm", _fake_dm)

        class _TimeoutAsyncClient(_RecordingAsyncClient):
            async def post(self, url, json=None, **kwargs):
                type(self).calls.append({"url": url, "json": json})
                raise httpx.ConnectTimeout("boom")

        _TimeoutAsyncClient.calls = []
        monkeypatch.setattr(studio_module.httpx, "AsyncClient", _TimeoutAsyncClient)

        # Must not raise.
        result = await studio_module._consume_and_resume(rp_settings, factory, token, "pol_xyz")
        assert result == {"resumed": False}
        assert len(_TimeoutAsyncClient.calls) == 1

    async def test_notification_non_2xx_never_raises(self, session_factory, monkeypatch):
        rp_settings = _settings(buyer_bot_enabled=False)
        factory = session_factory(rp_settings)
        token = _make_handoff(factory, resume_url="http://127.0.0.1:8765/internal/signing-complete")

        monkeypatch.setattr(studio_module, "send_dm", _fake_dm)

        class _500AsyncClient(_RecordingAsyncClient):
            responder = staticmethod(
                lambda url, json: httpx.Response(
                    500, json={}, request=httpx.Request("POST", url, json=json)
                )
            )

        _500AsyncClient.calls = []
        monkeypatch.setattr(studio_module.httpx, "AsyncClient", _500AsyncClient)

        result = await studio_module._consume_and_resume(rp_settings, factory, token, "pol_xyz")
        assert result == {"resumed": False}
        assert len(_500AsyncClient.calls) == 1
