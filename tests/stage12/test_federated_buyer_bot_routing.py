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
        self.views_sent: list[Any] = []

    async def send(self, content: str | None = None, embed: Any = None, view: Any = None) -> None:
        if content is not None:
            self.sent.append(content)
        if embed is not None:
            self.embeds_sent.append(embed)
        if view is not None:
            self.views_sent.append(view)

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


class _FakeOrigin:
    def __init__(self, name: str, merchant_id: str):
        self.name = name
        self.merchant_id = merchant_id


class _FakeMerchantClient:
    def __init__(self, items: list[dict[str, Any]]):
        self._items = items
        self.calls: list[str] = []

    async def search_products(self, query, tags=None, limit=10):
        self.calls.append(query)
        return {"success": True, "data": {"items": self._items}}


class _FakeCatalogMCPClient:
    """!menu fake: N merchants, each with its own tiny catalog and its own
    per-merchant client (so search_products calls are attributable)."""

    def __init__(self, catalogs: dict[str, list[dict[str, Any]]]):
        self._origins = [_FakeOrigin(name, mid) for mid, (name, _) in catalogs.items()]
        self._clients = {mid: _FakeMerchantClient(items) for mid, (_, items) in catalogs.items()}

    def merchants(self) -> list[_FakeOrigin]:
        return self._origins

    def client_for(self, merchant_id: str) -> _FakeMerchantClient:
        return self._clients[merchant_id]


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


class TestCancelRouting:
    """`!cancel` used to require a checkout_id as an argument, but
    build_shop_result_embed deliberately never prints one (a stage07 sentinel
    pins that it must not), so the documented cancel path could not be used
    from chat. A bare `!cancel` now targets the caller's latest open order."""

    async def _route(self, settings, monkeypatch, content: str, *, guild_id: int | None):
        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        calls: list[str | None] = []

        async def _fake_handle_cancel(message, checkout_id):
            calls.append(checkout_id)

        monkeypatch.setattr(bot, "_handle_cancel", _fake_handle_cancel)
        client = _FakeClient()
        bot.register(client)
        await client.handler(_FakeMessage(7, 42, content, guild_id=guild_id))
        return calls

    async def test_bare_bang_cancel_targets_the_latest_order(self, settings, session, monkeypatch):
        assert await self._route(settings, monkeypatch, "!cancel", guild_id=99) == [None]

    async def test_bang_cancel_with_an_id_still_targets_that_id(
        self, settings, session, monkeypatch
    ):
        calls = await self._route(settings, monkeypatch, "!cancel chk_123", guild_id=99)
        assert calls == ["chk_123"]

    async def test_bare_cancel_in_a_dm_is_a_session_abort_not_an_order_cancel(
        self, settings, session, monkeypatch
    ):
        """Bare "cancel" (no bang, no argument) in free-text context keeps its
        existing meaning: abort the pending question, not the order."""
        assert await self._route(settings, monkeypatch, "cancel", guild_id=None) == []


class TestConversationTurnCapFastPath:
    """Regression: MAX_CONVERSATION_TURNS used to be checked BEFORE looking
    at whether the reply was a free confirmation of an already-built cart.
    A "go ahead" arriving on the last available turn was killed by the cap
    before continue_shop() ever got a chance to fast-path it — the buyer saw
    "Let's start over" in response to a plain confirmation."""

    async def test_affirmative_reply_on_the_last_turn_is_not_killed_by_the_cap(
        self, settings, session, monkeypatch
    ):
        import json

        from openstore.agents.buyer_agent import MAX_CONVERSATION_TURNS
        from openstore.core.shopping_session import create_session

        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        cart = [{"merchant_id": "store-a", "sku": "sku-a", "qty": 1}]
        cart_ready = {
            "role": "user",
            "content": json.dumps({"tool_result": "cart_ready", "cart": cart}),
        }
        sess = create_session(
            session,
            chat_platform="discord",
            chat_user_id="200",
            chat_channel_id="300",
            policy_id="federated",
            trace_id="trace_y",
            goal="chocolate gelato",
            messages=[cart_ready],
        )
        sess.turns_used = MAX_CONVERSATION_TURNS - 1  # one reply away from the cap
        session.add(sess)
        session.commit()

        async def _fake_continue_shop(messages, reply_text, *args, **kwargs):
            return {"allowed": True, "cart": cart, "amount_minor": 500}

        monkeypatch.setattr(bot.agent, "continue_shop", _fake_continue_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(200, 300, "go ahead", guild_id=None)
        await client.handler(message)

        assert not any("start over" in s for s in message.channel.sent)

    async def test_non_confirming_reply_on_the_last_turn_still_hits_the_cap(
        self, settings, session, monkeypatch
    ):
        """The bypass is specific to a recognized confirmation of a pending
        cart — an ordinary reply must still respect the cap."""
        import json

        from openstore.agents.buyer_agent import MAX_CONVERSATION_TURNS
        from openstore.core.shopping_session import create_session

        bot = _bot_with_shopping_channel(settings, shopping_channel_id=None)
        cart = [{"merchant_id": "store-a", "sku": "sku-a", "qty": 1}]
        cart_ready = {
            "role": "user",
            "content": json.dumps({"tool_result": "cart_ready", "cart": cart}),
        }
        sess = create_session(
            session,
            chat_platform="discord",
            chat_user_id="201",
            chat_channel_id="301",
            policy_id="federated",
            trace_id="trace_z",
            goal="chocolate gelato",
            messages=[cart_ready],
        )
        sess.turns_used = MAX_CONVERSATION_TURNS - 1
        session.add(sess)
        session.commit()

        async def _fail_continue_shop(*args, **kwargs):
            raise AssertionError("continue_shop must not be called past the cap")

        monkeypatch.setattr(bot.agent, "continue_shop", _fail_continue_shop)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(201, 301, "actually swap it for tea", guild_id=None)
        await client.handler(message)

        assert any("start over" in s for s in message.channel.sent)


class TestMenuCommand:
    """!menu [store] — deterministic catalog read (search_products with an
    empty query), no LLM call, never touches a parked ShoppingSession."""

    def _bot(self, settings, catalogs: dict[str, tuple[str, list[dict[str, Any]]]]):
        return FederatedBuyerBot(settings, BuyerAgent(settings, _FakeCatalogMCPClient(catalogs)))

    async def test_bare_menu_pages_through_every_merchant(self, settings, session):
        catalogs = {
            "store-a": ("Store A", [{"name": "Widget", "unit_minor": 10000, "tags": []}]),
            "store-b": ("Store B", [{"name": "Gadget", "unit_minor": 20000, "tags": []}]),
        }
        bot = self._bot(settings, catalogs)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(300, 400, "!menu", guild_id=None)
        await client.handler(message)

        assert len(message.channel.embeds_sent) == 1  # first page sent...
        assert len(message.channel.views_sent) == 1  # ...with Prev/Next attached

    async def test_menu_with_a_store_name_filter_shows_only_that_store(self, settings, session):
        catalogs = {
            "store-a": ("Store A", [{"name": "Widget", "unit_minor": 10000, "tags": []}]),
            "store-b": ("Store B", [{"name": "Gadget", "unit_minor": 20000, "tags": []}]),
        }
        bot = self._bot(settings, catalogs)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(301, 401, "!menu store b", guild_id=None)
        await client.handler(message)

        assert len(message.channel.embeds_sent) == 1
        assert message.channel.views_sent == []  # single page — no pagination needed
        assert message.channel.embeds_sent[0].title == "Store B menu"
        # store-a's client was never even called
        assert bot.agent.mcp.client_for("store-a").calls == []

    async def test_menu_with_an_unknown_store_lists_known_stores(self, settings, session):
        catalogs = {"store-a": ("Store A", [])}
        bot = self._bot(settings, catalogs)
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(302, 402, "!menu nonexistent", guild_id=None)
        await client.handler(message)

        assert message.channel.embeds_sent == []
        assert any("Store A" in s for s in message.channel.sent)


class TestCartCommand:
    """!cart — read-only lookup of the currently parked cart. Must not
    disturb the ShoppingSession it reads from."""

    async def test_cart_shows_the_pending_cart(self, settings, session):
        import json

        from openstore.core.shopping_session import create_session
        from openstore.models import ShoppingSessionState

        bot = FederatedBuyerBot(settings, BuyerAgent(settings, _NoopFederatingMCPClient()))
        cart = [
            {
                "merchant_id": "store-a",
                "sku": "sku-a",
                "qty": 1,
                "name": "Widget",
                "unit_minor": 10000,
            }
        ]
        cart_ready = {
            "role": "user",
            "content": json.dumps({"tool_result": "cart_ready", "cart": cart}),
        }
        sess = create_session(
            session,
            chat_platform="discord",
            chat_user_id="500",
            chat_channel_id="600",
            policy_id="federated",
            trace_id="trace_cart",
            goal="widgets",
            messages=[cart_ready],
        )
        session.commit()

        client = _FakeClient()
        bot.register(client)
        message = _FakeMessage(500, 600, "!cart", guild_id=None)
        await client.handler(message)

        assert len(message.channel.embeds_sent) == 1
        # read-only: the session is untouched, still parked awaiting a reply
        session.refresh(sess)
        assert sess.state == ShoppingSessionState.AWAITING_REPLY

    async def test_cart_with_no_active_session_says_so(self, settings, session):
        bot = FederatedBuyerBot(settings, BuyerAgent(settings, _NoopFederatingMCPClient()))
        client = _FakeClient()
        bot.register(client)
        message = _FakeMessage(501, 601, "!cart", guild_id=None)
        await client.handler(message)

        assert message.channel.embeds_sent == []
        assert any("Nothing's in your cart" in s for s in message.channel.sent)


class TestFreeTextMenuAndCartCallbacks:
    """S18: "what's on the menu"/"what's in my cart" work as free text, not
    just !menu/!cart — BuyerBot/FederatedBuyerBot must hand BuyerAgent real
    on_menu_ready/on_cart_shown callbacks that route to the same rendering
    used by the bang commands. The LLM's own action-picking is already
    covered directly against BuyerGraph (tests/stage07/test_agents.py); this
    only proves the plumbing between the bot and BuyerAgent.start_shop/
    continue_shop is wired, by capturing the callbacks it was given."""

    async def test_a_fresh_free_text_goal_passes_working_menu_and_cart_callbacks(
        self, settings, session
    ):
        catalogs = {"store-a": ("Store A", [{"name": "Widget", "unit_minor": 10000, "tags": []}])}
        bot = FederatedBuyerBot(settings, BuyerAgent(settings, _FakeCatalogMCPClient(catalogs)))
        captured: dict[str, Any] = {}

        async def _fake_start_shop(goal, *args, **kwargs):
            captured.update(kwargs)
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": "t"}

        bot.agent.start_shop = _fake_start_shop  # type: ignore[method-assign]
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(700, 800, "what's on the menu?", guild_id=None)
        await client.handler(message)

        assert "on_menu_ready" in captured and "on_cart_shown" in captured
        # _fake_start_shop's "no_cart" result already sent one shop-result
        # embed before we get here — check the callback ADDS one, not the
        # total count.
        before = len(message.channel.embeds_sent)
        await captured["on_menu_ready"](None)
        assert len(message.channel.embeds_sent) == before + 1
        assert message.channel.embeds_sent[-1].title == "Store A menu"

        await captured["on_cart_shown"]([])
        assert any("Nothing's in your cart" in s for s in message.channel.sent)

    async def test_a_conversation_reply_passes_working_menu_and_cart_callbacks(
        self, settings, session
    ):
        from openstore.core.shopping_session import create_session

        catalogs = {"store-a": ("Store A", [{"name": "Widget", "unit_minor": 10000, "tags": []}])}
        bot = FederatedBuyerBot(settings, BuyerAgent(settings, _FakeCatalogMCPClient(catalogs)))
        create_session(
            session,
            chat_platform="discord",
            chat_user_id="701",
            chat_channel_id="801",
            policy_id="federated",
            trace_id="trace_menu_reply",
            goal="something",
            messages=[{"role": "user", "content": "something"}],
        )
        session.commit()

        captured: dict[str, Any] = {}

        async def _fake_continue_shop(messages, reply_text, *args, **kwargs):
            captured.update(kwargs)
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": "t"}

        bot.agent.continue_shop = _fake_continue_shop  # type: ignore[method-assign]
        client = _FakeClient()
        bot.register(client)

        message = _FakeMessage(701, 801, "what's on the menu?", guild_id=None)
        await client.handler(message)

        assert "on_menu_ready" in captured and "on_cart_shown" in captured
        await captured["on_menu_ready"]("store a")
        assert message.channel.embeds_sent[-1].title == "Store A menu"
