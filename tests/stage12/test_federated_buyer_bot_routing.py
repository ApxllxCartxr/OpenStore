# tests/stage12/test_federated_buyer_bot_routing.py
# S12 step 7: FederatedBuyerBot inherits BuyerBot.register() verbatim, so its
# Discord routing (prefix commands, free-text DM/shopping-channel, active-
# session-reply priority) must match BuyerBot's for every shared case. Fakes
# follow tests/stage11/test_free_text_and_upsell.py's conventions
# (_FakeChannel.typing() is an async context manager).

from __future__ import annotations

from typing import Any

from openstore.agents.buyer_agent import BuyerAgent, FederatedBuyerBot


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


class _NoopFederatingMCPClient:
    def client_for(self, merchant_id: str) -> Any:
        raise AssertionError("not exercised in routing tests")

    async def search_products(self, query, tags=None, limit=10):
        return {"success": True, "data": {"items": []}}


def _bot_with_shopping_channel(settings, shopping_channel_id: int | None) -> FederatedBuyerBot:
    settings.discord.shopping_channel_id = shopping_channel_id
    return FederatedBuyerBot(settings, BuyerAgent(settings, _NoopFederatingMCPClient()))


class TestFederatedBuyerBotRouting:
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
            policy_id="federated",
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

    async def test_bang_shop_prefix_works_anywhere(self, settings, session, monkeypatch):
        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        calls: list[str] = []

        async def _fake_handle_shop(message, goal):
            calls.append(goal)

        monkeypatch.setattr(bot, "_handle_shop", _fake_handle_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(105, 42, "!shop chai please", guild_id=999)
        await client.handler(message)

        assert calls == ["chai please"]
