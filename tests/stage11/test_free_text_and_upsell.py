# tests/stage11 — S14: free-text Discord routing (DMs + a configured
# shopping channel) and deterministic cross-sell (surfaces.catalog's
# related_skus, no LLM call).

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from openstore.agents.buyer_agent import (
    BuyerAgent,
    BuyerBot,
    build_cart_preview_embed,
    build_upsell_nudge_embed,
)
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
from openstore.surfaces.catalog import suggest_related_items

# ---------------------------------------------------------------------------
# suggest_related_items — pure catalog lookup, no LLM
# ---------------------------------------------------------------------------


@pytest.fixture()
def catalog_settings(tmp_path: Path) -> Settings:
    catalog = [
        {
            "sku": "gelato_vanilla",
            "name": "Vanilla Gelato",
            "price_minor": 15000,
            "tags": ["gelato"],
            "related_skus": ["cone_waffle", "topping_sprinkles"],
        },
        {
            "sku": "cone_waffle",
            "name": "Waffle Cone",
            "price_minor": 3000,
            "tags": ["cone"],
            "related_skus": ["gelato_vanilla"],
        },
        {
            "sku": "topping_sprinkles",
            "name": "Rainbow Sprinkles",
            "price_minor": 2000,
            "tags": ["topping"],
            "related_skus": ["gelato_vanilla"],
        },
        {"sku": "gelato_plain", "name": "Plain Gelato", "price_minor": 12000, "tags": ["gelato"]},
    ]
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(catalog))

    settings = Settings(
        merchant=MerchantConfig(name="Test Merchant"),
        razorpay=RazorpayConfig(key_id="rzp_test", key_secret="secret"),
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
    settings.catalog_path = str(path)
    return settings


class TestSuggestRelatedItems:
    def test_returns_related_items_excluding_cart_contents(self, catalog_settings):
        suggestions = suggest_related_items(catalog_settings, ["gelato_vanilla"])
        assert [s["sku"] for s in suggestions] == ["cone_waffle", "topping_sprinkles"]

    def test_excludes_skus_already_in_cart(self, catalog_settings):
        suggestions = suggest_related_items(catalog_settings, ["gelato_vanilla", "cone_waffle"])
        assert [s["sku"] for s in suggestions] == ["topping_sprinkles"]

    def test_respects_limit(self, catalog_settings):
        suggestions = suggest_related_items(catalog_settings, ["gelato_vanilla"], limit=1)
        assert [s["sku"] for s in suggestions] == ["cone_waffle"]

    def test_empty_when_item_has_no_related_skus(self, catalog_settings):
        assert suggest_related_items(catalog_settings, ["gelato_plain"]) == []

    def test_empty_for_unknown_sku(self, catalog_settings):
        assert suggest_related_items(catalog_settings, ["not_a_real_sku"]) == []


# ---------------------------------------------------------------------------
# Embed builders — pure functions
# ---------------------------------------------------------------------------


class TestCartPreviewAndUpsellEmbeds:
    def test_cart_preview_shows_items_and_total(self):
        cart = [
            {"sku": "gelato_vanilla", "name": "Vanilla Gelato", "qty": 2, "unit_minor": 15000},
        ]
        embed = build_cart_preview_embed(cart, [])
        assert embed["title"] == "Building your cart"
        assert "2 × Vanilla Gelato" in embed["description"]
        assert embed["fields"][0] == {"name": "Total so far", "value": "₹300.00", "inline": True}
        assert len(embed["fields"]) == 1  # no suggestions -> no extra field

    def test_cart_preview_includes_suggestions_field_when_present(self):
        cart = [{"sku": "gelato_vanilla", "name": "Vanilla Gelato", "qty": 1, "unit_minor": 15000}]
        suggestions = [{"sku": "cone_waffle", "name": "Waffle Cone", "unit_minor": 3000}]
        embed = build_cart_preview_embed(cart, suggestions)
        assert any(f["name"] == "You might also like" for f in embed["fields"])

    def test_upsell_nudge_none_when_no_suggestions(self):
        assert build_upsell_nudge_embed([]) is None

    def test_upsell_nudge_lists_suggestions(self):
        suggestions = [{"sku": "cone_waffle", "name": "Waffle Cone", "unit_minor": 3000}]
        embed = build_upsell_nudge_embed(suggestions)
        assert embed is not None
        assert "Waffle Cone" in embed["description"]


# ---------------------------------------------------------------------------
# Free-text routing
# ---------------------------------------------------------------------------


class _FakeTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[str] = []
        self.embeds_sent: list[Any] = []

    async def send(self, content: str | None = None, embed: Any = None) -> None:
        if content is not None:
            self.sent.append(content)
        if embed is not None:
            self.embeds_sent.append(embed)

    def typing(self) -> _FakeTyping:
        return _FakeTyping()


class _FakeGuild:
    def __init__(self, guild_id: int):
        self.id = guild_id


class _FakeAuthor:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeMessage:
    def __init__(self, author_id: int, channel_id: int, content: str, *, guild_id: int | None):
        self.author = _FakeAuthor(author_id)
        self.channel = _FakeChannel(channel_id)
        self.content = content
        self.id = 1
        self.guild = _FakeGuild(guild_id) if guild_id is not None else None


class _FakeClient:
    def __init__(self):
        self.user = object()
        self.handler: Any = None

    def event(self, fn):
        self.handler = fn
        return fn


class _NoopMCPClient:
    async def search_products(self, query, tags=None, limit=10):
        return {"success": True, "data": {"items": []}}

    async def call(self, tool_name, arguments):
        raise AssertionError(f"unexpected tool call in routing test: {tool_name}")


def _bot_with_shopping_channel(settings: Settings, shopping_channel_id: int | None) -> BuyerBot:
    settings.discord.shopping_channel_id = shopping_channel_id
    return BuyerBot(settings, BuyerAgent(settings, _NoopMCPClient()))


class TestFreeTextRouting:
    async def test_dm_free_text_with_no_prefix_starts_a_shop(self, settings, session, monkeypatch):
        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        calls: list[str] = []

        async def _fake_handle_shop(message, goal):
            calls.append(goal)

        monkeypatch.setattr(bot, "_handle_shop", _fake_handle_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(101, 201, "I want some vanilla gelato", guild_id=None)
        await client.handler(message)

        assert calls == ["I want some vanilla gelato"]

    async def test_configured_shopping_channel_free_text_starts_a_shop(
        self, settings, session, monkeypatch
    ):
        bot = _bot_with_shopping_channel(settings, shopping_channel_id=555)
        calls: list[str] = []

        async def _fake_handle_shop(message, goal):
            calls.append(goal)

        monkeypatch.setattr(bot, "_handle_shop", _fake_handle_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(102, 555, "surprise me with something sweet", guild_id=999)
        await client.handler(message)

        assert calls == ["surprise me with something sweet"]

    async def test_other_guild_channel_without_prefix_is_ignored(
        self, settings, session, monkeypatch
    ):
        bot = _bot_with_shopping_channel(settings, shopping_channel_id=555)
        calls: list[str] = []

        async def _fake_handle_shop(message, goal):
            calls.append(goal)

        monkeypatch.setattr(bot, "_handle_shop", _fake_handle_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(103, 42, "just chatting, not shopping", guild_id=999)
        await client.handler(message)

        assert calls == []

    async def test_pending_session_reply_takes_priority_over_fresh_goal(
        self, settings, session, monkeypatch
    ):
        from openstore.core.shopping_session import create_session

        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        create_session(
            session,
            chat_platform="discord",
            chat_user_id="104",
            chat_channel_id="201",
            policy_id="pol_x",
            trace_id="trace_x",
            goal="vanilla gelato",
            messages=[{"role": "user", "content": "vanilla gelato"}],
        )
        session.commit()

        reply_calls: list[str] = []
        shop_calls: list[str] = []

        async def _fake_reply(message, session_id):
            reply_calls.append(session_id)

        async def _fake_handle_shop(message, goal):
            shop_calls.append(goal)

        monkeypatch.setattr(bot, "_handle_conversation_reply", _fake_reply)
        monkeypatch.setattr(bot, "_handle_shop", _fake_handle_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(104, 201, "chocolate please", guild_id=None)
        await client.handler(message)

        assert len(reply_calls) == 1
        assert shop_calls == []
