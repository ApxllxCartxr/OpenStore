# tests/stage14/test_merchant_bot.py
# DECISION-028: MerchantBot — one-shot classify (campaign_status/exposure/
# recent_orders/unknown), deterministic dispatch, per-instance DB engines
# (not core/database's process-global singleton, which would silently
# point every merchant but the first at the wrong database — see the
# decision entry).

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from openstore.agents.llm import DummyProvider, register_provider
from openstore.agents.merchant_bot import MerchantBot
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
from openstore.models import Campaign, CampaignState, Checkout, IntentPolicy, OrderState
from sqlmodel import Session


def _config(name: str) -> Settings:
    return Settings(
        merchant=MerchantConfig(name=name, currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


def _register_reply(monkeypatch, name: str, response: dict[str, Any] | str) -> None:
    text = response if isinstance(response, str) else json.dumps(response)

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            return text

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


def _register_queued_replies(monkeypatch, name: str, responses: list[dict[str, Any] | str]) -> None:
    """Like _register_reply, but a different response per llm_chat call —
    suggest_campaign makes two: the classify call, then CampaignAgent's own
    draft call."""
    queue = [r if isinstance(r, str) else json.dumps(r) for r in responses]

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            return queue.pop(0)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


def _seed_campaign(
    bot: MerchantBot, merchant_name: str, merchant_id: str, **overrides: Any
) -> None:
    now = datetime.now(UTC)
    engine = bot._engines[merchant_name]
    init_database_for(engine)
    with Session(engine) as session:
        session.add(
            Campaign(
                id=overrides.get("id", "camp_test"),
                merchant_id=merchant_id,
                campaign_version=1,
                title=overrides.get("title", "Weekend Special"),
                rationale="test",
                discount_bps=overrides.get("discount_bps", 1500),
                applies_to_skus=["gelato_vanilla"],
                starts_at=now,
                ends_at=now + timedelta(days=7),
                source_signals={},
                draft_digest="sha256:deadbeef",
                state=overrides.get("state", CampaignState.ACTIVE),
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _seed_policy(
    bot: MerchantBot, merchant_name: str, merchant_id: str, max_spend_total_minor: int
) -> None:
    now = datetime.now(UTC)
    engine = bot._engines[merchant_name]
    with Session(engine) as session:
        session.add(
            IntentPolicy(
                id="pol_test",
                merchant_id=merchant_id,
                policy_hash="h" * 8,
                max_spend_per_tx_minor=max_spend_total_minor,
                max_spend_total_minor=max_spend_total_minor,
                max_transactions=100,
                allowed_tags=[],
                blocked_skus=[],
                required_skus=[],
                not_before=int(now.timestamp()) - 10,
                expires_at=int(now.timestamp()) + 3600,
                webauthn_credential_id="cred_test",
                webauthn_sign_count=1,
                signed_at=now.replace(tzinfo=None),
                is_active=True,
            )
        )
        session.commit()


def _seed_checkout(
    bot: MerchantBot, merchant_name: str, merchant_id: str, checkout_id: str, amount_minor: int
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    engine = bot._engines[merchant_name]
    with Session(engine) as session:
        session.add(
            Checkout(
                id=checkout_id,
                trace_id=f"trace_{checkout_id}",
                client_id="test",
                merchant_id=merchant_id,
                cart_hash="h" * 8,
                cart_version=1,
                amount_minor=amount_minor,
                currency="INR",
                state=OrderState.PAID,
                aal_level=2,
                expires_at=now + timedelta(hours=1),
                idempotency_key=f"idem_{checkout_id}",
                cart_snapshot={"items": []},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def init_database_for(engine: Any) -> None:
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)


class TestMerchantBotDispatch:
    def _bot(self, *names: str) -> MerchantBot:
        configs = {name: _config(name) for name in names}
        bot = MerchantBot(configs)
        for name in names:
            init_database_for(bot._engines[name])
        return bot

    async def test_campaign_status_reports_a_seeded_campaign(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _seed_campaign(bot, "Gelateria Milano", "gelateria-milano", title="Diwali Push")
        _register_reply(monkeypatch, "test_mb_campaign", {"action": "campaign_status"})

        reply = await bot.handle("how's the Diwali campaign doing?")

        assert "Diwali Push" in reply
        assert "ACTIVE" in reply
        assert "15.0%" in reply

    async def test_campaign_status_flags_pending_approval(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _seed_campaign(
            bot, "Gelateria Milano", "gelateria-milano", state=CampaignState.PENDING_APPROVAL
        )
        _register_reply(monkeypatch, "test_mb_pending", {"action": "campaign_status"})

        reply = await bot.handle("any campaigns need my approval?")

        assert "needs your approval" in reply

    async def test_exposure_reports_full_headroom_with_no_spend(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _seed_policy(bot, "Gelateria Milano", "gelateria-milano", max_spend_total_minor=500000)
        _register_reply(monkeypatch, "test_mb_exposure", {"action": "exposure"})

        reply = await bot.handle("what's my exposure right now?")

        assert "5000.00" in reply

    async def test_recent_orders_lists_seeded_checkouts(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _seed_checkout(bot, "Gelateria Milano", "gelateria-milano", "chk_a", 15000)
        _seed_checkout(bot, "Gelateria Milano", "gelateria-milano", "chk_b", 12000)
        _register_reply(monkeypatch, "test_mb_orders", {"action": "recent_orders"})

        reply = await bot.handle("show me recent orders")

        assert "chk_a" in reply
        assert "chk_b" in reply

    async def test_unknown_action_carries_the_models_refusal_message(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _register_reply(
            monkeypatch,
            "test_mb_unknown",
            {"action": "unknown", "message": "I can't approve campaigns — only report on them."},
        )

        reply = await bot.handle("please approve the Diwali campaign")

        assert reply == "I can't approve campaigns — only report on them."

    async def test_malformed_llm_response_gets_a_friendly_fallback(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _register_reply(monkeypatch, "test_mb_malformed", "not json at all")

        reply = await bot.handle("anything")

        assert "didn't follow that" in reply

    async def test_multi_merchant_filter_only_reports_the_named_store(self, monkeypatch):
        bot = self._bot("Gelateria Milano", "Chai House")
        _seed_campaign(bot, "Gelateria Milano", "gelateria-milano", title="Gelato Deal")
        _seed_campaign(bot, "Chai House", "chai-house", title="Chai Deal")
        _register_reply(
            monkeypatch, "test_mb_filter", {"action": "campaign_status", "merchant": "chai"}
        )

        reply = await bot.handle("how's chai house doing?")

        assert "Chai Deal" in reply
        assert "Gelato Deal" not in reply

    async def test_unrecognized_store_name_lists_known_stores(self, monkeypatch):
        bot = self._bot("Gelateria Milano", "Chai House")
        _register_reply(
            monkeypatch, "test_mb_bad_store", {"action": "exposure", "merchant": "nonexistent"}
        )

        reply = await bot.handle("what's the exposure at nonexistent store?")

        assert "Gelateria Milano" in reply
        assert "Chai House" in reply

    async def test_each_merchant_gets_its_own_isolated_database(self, monkeypatch):
        """Regression guard for the core/database global-engine-singleton
        trap: two merchants must never see each other's rows."""
        bot = self._bot("Gelateria Milano", "Chai House")
        _seed_checkout(bot, "Gelateria Milano", "gelateria-milano", "chk_gel", 15000)
        _seed_checkout(bot, "Chai House", "chai-house", "chk_chai", 8000)
        _register_reply(monkeypatch, "test_mb_isolation", {"action": "recent_orders"})

        reply = await bot.handle("show me recent orders")

        assert "chk_gel" in reply
        assert "chk_chai" in reply
        gelateria_line = next(line for line in reply.splitlines() if "chk_gel" in line)
        chai_line = next(line for line in reply.splitlines() if "chk_chai" in line)
        assert "chk_chai" not in gelateria_line
        assert "chk_gel" not in chai_line


class TestSuggestCampaign:
    """suggest_campaign: same ingest -> LLM draft -> validate -> persist
    DRAFT -> PENDING_APPROVAL pipeline as `openstore campaign draft`, just
    triggered by chat instead of the CLI. Never activates anything."""

    def _bot(self, *names: str) -> MerchantBot:
        configs = {name: _config(name) for name in names}
        bot = MerchantBot(configs)
        for name in names:
            init_database_for(bot._engines[name])
        return bot

    async def test_drafts_a_pending_approval_campaign(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _register_queued_replies(
            monkeypatch,
            "test_mb_suggest_ok",
            [
                {"action": "suggest_campaign", "calendar_event": "weekend"},
                {
                    "title": "Weekend Push",
                    "rationale": "test rationale",
                    "discount_bps": 1200,
                    "applies_to_skus": [],
                },
            ],
        )

        reply = await bot.handle("can you suggest a campaign for the weekend?")

        assert "Weekend Push" in reply
        assert "PENDING_APPROVAL" in reply
        assert "12.0%" in reply

        # actually persisted, not just reported
        with Session(bot._engines["Gelateria Milano"]) as session:
            campaigns = list(session.exec(select_campaigns()).all())
        assert len(campaigns) == 1
        assert campaigns[0].state == CampaignState.PENDING_APPROVAL

    async def test_ambiguous_store_asks_which_one(self, monkeypatch):
        bot = self._bot("Gelateria Milano", "Chai House")
        _register_reply(monkeypatch, "test_mb_suggest_ambiguous", {"action": "suggest_campaign"})

        reply = await bot.handle("suggest a campaign for me")

        assert "Gelateria Milano" in reply
        assert "Chai House" in reply
        with Session(bot._engines["Gelateria Milano"]) as session:
            assert list(session.exec(select_campaigns()).all()) == []

    async def test_out_of_bounds_discount_is_rejected_not_persisted(self, monkeypatch):
        bot = self._bot("Gelateria Milano")
        _register_queued_replies(
            monkeypatch,
            "test_mb_suggest_bad",
            [
                {"action": "suggest_campaign"},
                {
                    "title": "Too Generous",
                    "rationale": "test",
                    "discount_bps": 9000,  # above CampaignSettings.max_bps (3000)
                    "applies_to_skus": [],
                },
            ],
        )

        reply = await bot.handle("suggest a campaign")

        assert "couldn't draft" in reply
        with Session(bot._engines["Gelateria Milano"]) as session:
            assert list(session.exec(select_campaigns()).all()) == []


def select_campaigns():
    from sqlmodel import select

    return select(Campaign)
