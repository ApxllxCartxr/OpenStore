# tests/stage12/test_buyer_bot_gating.py
# S12: the buyer agent is moving into its own process. Two merchant configs
# sharing one Discord bot_token (gelateria.yaml, chai.yaml) would otherwise
# each start a BuyerBot on the shared token — two logins, both receiving and
# replying to the same DM. discord.DiscordConfig.buyer_bot_enabled gates
# BuyerBot registration without touching the shared discord.Client startup
# the notifier depends on for trace channels/DMs.

from __future__ import annotations

from unittest.mock import MagicMock

import discord
import pytest
from fastapi.testclient import TestClient
from openstore.agents.buyer_agent import BuyerBot
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


def _config(*, buyer_bot_enabled: bool) -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            # Not "token" (server.py's offline sentinel) and not empty, so the
            # discord.Client startup branch actually runs and the gate has
            # something to gate. discord.Client.start is mocked below so this
            # never attempts a real login.
            bot_token="not-the-offline-sentinel",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
            buyer_bot_enabled=buyer_bot_enabled,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture(autouse=True)
def _no_real_discord_login(monkeypatch):
    """Never attempt a network login; keep the client's asyncio task a no-op
    that just waits to be cancelled at lifespan shutdown."""

    async def _fake_start(self, token, *, reconnect=True):
        import asyncio

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(discord.Client, "start", _fake_start)


def _run_lifespan(config: Settings, monkeypatch) -> MagicMock:
    """Boot the app through its lifespan with BuyerBot.register mocked so we
    can observe whether it was ever called, then shut it down cleanly."""
    import openstore.core.database as db_mod

    db_mod._engine = None
    init_database(config)

    register_mock = MagicMock()
    monkeypatch.setattr(BuyerBot, "register", lambda self, client: register_mock(client))

    app = create_app(config)
    with TestClient(app):
        pass
    return register_mock


class TestBuyerBotGating:
    def test_disabled_never_registers_buyer_bot(self, monkeypatch):
        register_mock = _run_lifespan(_config(buyer_bot_enabled=False), monkeypatch)
        register_mock.assert_not_called()

    def test_default_enabled_still_registers_buyer_bot(self, monkeypatch):
        register_mock = _run_lifespan(_config(buyer_bot_enabled=True), monkeypatch)
        register_mock.assert_called_once()
