# tests/stage11/test_phase4_negotiation.py
# S11 Phase 4: negotiation wired into BuyerAgent.shop()'s DENY branch, plus
# the apply_cart_delta executor (plan items #23-24).

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from typing import Any

import pytest
from openstore.agents.buyer_agent import BuyerAgent, BuyerBot
from openstore.agents.llm import DummyProvider, register_provider
from openstore.agents.merchant_agent import apply_cart_delta
from openstore.models import IntentPolicy, WebAuthnCredential

# BuyerGraph's tool-calling loop and negotiate() both reason via an LLM call
# (S12/S13: agentic buyer/merchant agents). These tests exercise
# BuyerAgent.start_shop()'s planning + negotiation control flow, not LLM
# reasoning quality — so a single scripted provider stands in for both call
# shapes, replicating the pre-agentic behavior these tests were written
# against: planning searches once then selects every result it found (old
# mechanical dump), negotiate() maps reason_code -> action via the same
# table the old deterministic branches used.
_REASON_TO_ACTION = {
    "policy.tag_violation": "remove_violating_tags",
    "policy.sku_blocked": "swap_sku",
    "policy.spend_per_tx_exceeded": "reduce_qty",
}


class _ScriptedAgentProvider(DummyProvider):
    def chat(self, messages, **kwargs):
        last_content = messages[-1]["content"]
        try:
            user_payload = _json.loads(last_content)
        except _json.JSONDecodeError:
            user_payload = None

        if user_payload is None:
            # First planning turn: last_content is the raw goal text (not
            # JSON) — kick off with a search. The fake MCP clients in this
            # file ignore the query and always return their fixed catalog.
            return _json.dumps({"action": "search", "query": last_content})

        if isinstance(user_payload, dict) and user_payload.get("tool_result") == "search":
            # BuyerGraph.run_search's tool-result turn: select every item
            # found across every query term (S17: "results" is keyed by
            # query term now), matching the old plan()'s mechanical dump
            # these tests were written against.
            seen: dict[str, str] = {}
            for items in user_payload.get("results", {}).values():
                for item in items:
                    seen[item["sku"]] = item["merchant_id"]
            selections = [
                {"sku": sku, "merchant_id": merchant_id, "qty": 1}
                for sku, merchant_id in seen.items()
            ]
            return _json.dumps({"action": "answer", "selections": selections})

        reason_code = user_payload.get("reason_code")
        action = _REASON_TO_ACTION.get(reason_code, "no_compliant_path")
        return _json.dumps({"action": action, "rationale": f"resolves {reason_code}"})


@pytest.fixture(autouse=True)
def _mock_negotiate_llm(monkeypatch):
    register_provider("test_phase4_negotiate", _ScriptedAgentProvider)
    monkeypatch.setenv("LLM_PROVIDER", "test_phase4_negotiate")


def _make_policy(session, *, policy_id, credential_id, user_handle, **overrides):
    cred = WebAuthnCredential(
        credential_id=credential_id,
        user_handle=user_handle,
        public_key=b"\x00" * 32,
        sign_count=1,
    )
    session.add(cred)
    session.flush()

    now = int(datetime.now(UTC).timestamp())
    defaults = dict(
        id=policy_id,
        merchant_id="gelateria",
        policy_hash="h" * 64,
        max_spend_per_tx_minor=1_000_000,
        max_spend_total_minor=10_000_000,
        max_transactions=100,
        allowed_tags=[],
        blocked_skus=[],
        required_skus=[],
        not_before=now - 10,
        expires_at=now + 3600,
        webauthn_credential_id=credential_id,
        webauthn_sign_count=1,
        signed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    defaults.update(overrides)
    policy = IntentPolicy(**defaults)
    session.add(policy)
    session.commit()
    return policy


class _FakeTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[str] = []
        self.embeds_sent: list[dict] = []

    async def send(self, content: str | None = None, embed: Any = None) -> None:
        if content is not None:
            self.sent.append(content)
        if embed is not None:
            self.embeds_sent.append(embed)

    def typing(self) -> _FakeTyping:
        return _FakeTyping()


class _FakeAuthor:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeMessage:
    def __init__(self, author_id: int, channel_id: int, msg_id: int = 1):
        self.author = _FakeAuthor(author_id)
        self.channel = _FakeChannel(channel_id)
        self.id = msg_id
        self.guild = None


class _CartMCPClient:
    """Fake MCP client whose create_cart mimics the two negotiable checks
    (tag_violation, spend_per_tx_exceeded) this test drives, so apply_cart_delta's
    real filtering logic is exercised end-to-end through BuyerAgent.shop()."""

    def __init__(self, catalog_items, *, allowed_tags, tag_mode, cap_minor):
        self.catalog_items = catalog_items
        self.allowed_tags = set(allowed_tags)
        self.tag_mode = tag_mode
        self.cap_minor = cap_minor
        self.create_cart_calls: list[list[dict]] = []

    async def search_products(self, query, tags=None, limit=10):
        return {"success": True, "data": {"items": self.catalog_items}}

    async def call(self, tool_name, arguments):
        if tool_name == "create_cart":
            items = arguments["items"]
            self.create_cart_calls.append(items)

            for item in items:
                tags = set(item.get("tags", []))
                if self.tag_mode == "all":
                    ok = tags.issubset(self.allowed_tags) if self.allowed_tags else True
                else:
                    ok = bool(tags & self.allowed_tags) if self.allowed_tags else True
                if not ok:
                    return {
                        "success": True,
                        "data": {
                            "allowed": False,
                            "reason_code": "policy.tag_violation",
                            "transcript": [],
                            "checkout_id": None,
                        },
                    }

            total = sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in items)
            if total > self.cap_minor:
                return {
                    "success": True,
                    "data": {
                        "allowed": False,
                        "reason_code": "policy.spend_per_tx_exceeded",
                        "transcript": [],
                        "checkout_id": None,
                    },
                }

            return {
                "success": True,
                "data": {
                    "allowed": True,
                    "reason_code": None,
                    "checkout_id": "chk_neg",
                    "aal_level": 0,
                    "effective_amount_minor": total,
                    "transcript": [],
                },
            }
        if tool_name == "checkout_initiate":
            return {
                "success": True,
                "data": {
                    "checkout_id": "chk_neg",
                    "state": "HELD",
                    "amount_minor": 1,
                    "currency": "INR",
                    "short_url": "https://pay.example/x",
                    "cancel_token": "tok",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            }
        raise AssertionError(f"unexpected tool: {tool_name}")


class _CascadingMCPClient:
    """Mimics compile_decision's check ORDER (blocked_sku, then tag, then
    spend_per_tx — PRD checks 7/8/9) so each negotiation round's fix reveals a
    genuinely NEW violation rather than resolving everything in one shot.
    Drives the round-cap exhaustion test: 3 real, incrementally-progressing
    rounds, never a repeated identical cart, never a silent hang (R0.5)."""

    def __init__(self, catalog_items, *, blocked_skus, allowed_tags, tag_mode, cap_minor):
        self.catalog_items = catalog_items
        self.blocked_skus = set(blocked_skus)
        self.allowed_tags = set(allowed_tags)
        self.tag_mode = tag_mode
        self.cap_minor = cap_minor
        self.create_cart_calls: list[list[dict]] = []

    async def search_products(self, query, tags=None, limit=10):
        return {"success": True, "data": {"items": self.catalog_items}}

    async def call(self, tool_name, arguments):
        assert tool_name == "create_cart"
        items = arguments["items"]
        self.create_cart_calls.append(items)

        if any(i.get("sku") in self.blocked_skus for i in items):
            return {
                "success": True,
                "data": {
                    "allowed": False,
                    "reason_code": "policy.sku_blocked",
                    "transcript": [],
                    "checkout_id": None,
                },
            }
        for item in items:
            tags = set(item.get("tags", []))
            ok = (
                tags.issubset(self.allowed_tags)
                if self.tag_mode == "all"
                else bool(tags & self.allowed_tags)
            )
            if not ok:
                return {
                    "success": True,
                    "data": {
                        "allowed": False,
                        "reason_code": "policy.tag_violation",
                        "transcript": [],
                        "checkout_id": None,
                    },
                }
        total = sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in items)
        if total > self.cap_minor:
            return {
                "success": True,
                "data": {
                    "allowed": False,
                    "reason_code": "policy.spend_per_tx_exceeded",
                    "transcript": [],
                    "checkout_id": None,
                },
            }
        return {
            "success": True,
            "data": {
                "allowed": True,
                "reason_code": None,
                "checkout_id": "chk_neg",
                "aal_level": 0,
                "effective_amount_minor": total,
                "transcript": [],
            },
        }


class TestApplyCartDelta:
    def test_remove_violating_tags_drops_non_compliant_items(self):
        cart = [
            {"sku": "vanilla", "qty": 1, "unit_minor": 500, "tags": ["vegan"]},
            {"sku": "cream", "qty": 1, "unit_minor": 500, "tags": ["dairy"]},
        ]
        policy = {"allowed_tags": ["vegan"], "tag_mode": "all"}
        result = apply_cart_delta(cart, {"remove_violating_tags": True}, policy)
        assert [i["sku"] for i in result] == ["vanilla"]
        # original cart untouched
        assert len(cart) == 2

    def test_swap_sku_drops_blocked_line_item(self):
        cart = [
            {"sku": "vanilla", "qty": 1, "unit_minor": 500},
            {"sku": "banned", "qty": 1, "unit_minor": 500},
        ]
        policy = {"blocked_skus": ["banned"]}
        result = apply_cart_delta(cart, {"swap_sku": True}, policy)
        assert [i["sku"] for i in result] == ["vanilla"]

    def test_reduce_qty_scales_down_to_fit_cap(self):
        cart = [
            {"sku": "a", "qty": 1, "unit_minor": 10_000},
            {"sku": "b", "qty": 1, "unit_minor": 10_000},
            {"sku": "c", "qty": 1, "unit_minor": 10_000},
        ]
        policy = {"max_spend_per_tx_minor": 15_000}
        result = apply_cart_delta(cart, {"reduce_qty": True}, policy)
        assert sum(i["qty"] * i["unit_minor"] for i in result) <= 15_000

    def test_reduce_qty_drops_single_unit_items_when_still_over_cap(self):
        cart = [{"sku": "a", "qty": 1, "unit_minor": 100}]
        policy = {"max_spend_per_tx_minor": 50}
        result = apply_cart_delta(cart, {"reduce_qty": True}, policy)
        assert result == []

    def test_unknown_flag_is_a_noop(self):
        cart = [{"sku": "a", "qty": 1, "unit_minor": 100}]
        result = apply_cart_delta(cart, {}, {})
        assert result == cart


class TestTechnicalDetailRoutedToTraceChannels:
    """Reason codes/cart deltas/checkout internals must reach the buyer/
    merchant-trace Discord channels (already configured via DiscordConfig),
    not the buyer's own DM/channel — see the DiscordNotifier calls added to
    BuyerAgent.shop()/BuyerGraph.plan()/BuyerBot."""

    async def test_shop_pushes_buyer_and_merchant_trace(self, settings, session, monkeypatch):
        import openstore.notifier as notifier_module

        _make_policy(
            session,
            policy_id="pol_trace_route",
            credential_id="cred_trace_route",
            user_handle="discord:99",
            allowed_tags=["vegan"],
            tag_mode="all",
        )
        mcp = _CartMCPClient(
            [
                {"sku": "vanilla", "unit_minor": 500, "tags": ["vegan"]},
                {"sku": "cream", "unit_minor": 500, "tags": ["dairy"]},
            ],
            allowed_tags=["vegan"],
            tag_mode="all",
            cap_minor=1_000_000,
        )
        agent = BuyerAgent(settings, mcp)

        buyer_trace_calls: list[tuple[str, dict]] = []
        merchant_trace_calls: list[tuple[str, dict]] = []

        async def _fake_buyer_trace(self, trace_id, action, details):
            buyer_trace_calls.append((action, details))

        async def _fake_merchant_trace(self, trace_id, action, details):
            merchant_trace_calls.append((action, details))

        monkeypatch.setattr(notifier_module.DiscordNotifier, "buyer_trace", _fake_buyer_trace)
        monkeypatch.setattr(notifier_module.DiscordNotifier, "merchant_trace", _fake_merchant_trace)

        pending = await agent.shop("vegan gelato", "pol_trace_route", "trace_route")
        assert pending["awaiting_reply"] is True
        result = await agent.continue_shop(
            pending["messages"], "yes", "pol_trace_route", "trace_route"
        )

        assert result["allowed"] is True
        # the technical cart_compiled payload (reason codes, transcript) went
        # to buyer-trace, not into the buyer's own reply
        assert any(action == "cart_compiled" for action, _ in buyer_trace_calls)
        # the raw negotiation cart_delta/reason_code went to merchant-trace
        assert any(
            details.get("cart_delta") == {"remove_violating_tags": True}
            for _, details in merchant_trace_calls
        )


class TestShopNegotiation:
    async def test_tag_violation_resolves_via_one_round(self, settings, session):
        _make_policy(
            session,
            policy_id="pol_neg_tag",
            credential_id="cred_neg_tag",
            user_handle="discord:1",
            allowed_tags=["vegan"],
            tag_mode="all",
        )
        mcp = _CartMCPClient(
            [
                {"sku": "vanilla", "unit_minor": 500, "tags": ["vegan"]},
                {"sku": "cream", "unit_minor": 500, "tags": ["dairy"]},
            ],
            allowed_tags=["vegan"],
            tag_mode="all",
            cap_minor=1_000_000,
        )
        agent = BuyerAgent(settings, mcp)

        rounds_seen: list[tuple[int, dict]] = []

        async def _on_round(round_num, negotiation):
            rounds_seen.append((round_num, negotiation))

        pending = await agent.shop("vegan gelato", "pol_neg_tag", "trace_neg_tag")
        assert pending["awaiting_reply"] is True
        result = await agent.continue_shop(
            pending["messages"],
            "yes",
            "pol_neg_tag",
            "trace_neg_tag",
            on_negotiation_round=_on_round,
        )

        assert result["allowed"] is True
        assert result["checkout_id"] == "chk_neg"
        assert len(mcp.create_cart_calls) == 2
        assert len(mcp.create_cart_calls[0]) == 2  # both items, first attempt
        assert [i["sku"] for i in mcp.create_cart_calls[1]] == ["vanilla"]  # negotiated
        assert len(rounds_seen) == 1
        assert rounds_seen[0][1]["state"] == "COUNTERED"
        assert rounds_seen[0][1]["cart_delta"] == {"remove_violating_tags": True}

    async def test_spend_per_tx_exceeded_resolves_via_reduce_qty(self, settings, session):
        _make_policy(
            session,
            policy_id="pol_neg_spend",
            credential_id="cred_neg_spend",
            user_handle="discord:2",
            max_spend_per_tx_minor=5_000,
        )
        mcp = _CartMCPClient(
            [
                {"sku": "a", "unit_minor": 3_000, "tags": []},
                {"sku": "b", "unit_minor": 3_000, "tags": []},
            ],
            allowed_tags=[],
            tag_mode="all",
            cap_minor=5_000,
        )
        agent = BuyerAgent(settings, mcp)

        pending = await agent.shop("two scoops", "pol_neg_spend", "trace_neg_spend")
        assert pending["awaiting_reply"] is True
        result = await agent.continue_shop(
            pending["messages"], "yes", "pol_neg_spend", "trace_neg_spend"
        )

        assert result["allowed"] is True
        assert len(mcp.create_cart_calls) == 2
        assert len(mcp.create_cart_calls[1]) == 1  # one item dropped

    async def test_negotiation_exhausts_round_cap_and_reports_final_denial(self, settings, session):
        _make_policy(
            session,
            policy_id="pol_neg_cap",
            credential_id="cred_neg_cap",
            user_handle="discord:3",
            allowed_tags=["vegan"],
            tag_mode="all",
            blocked_skus=["y-blocked"],
            max_spend_per_tx_minor=50_000,
        )
        # x: tag-violating; y-blocked: sku-blocked; z: fine on its own, but
        # alone still exceeds the per-tx cap — three DIFFERENT violations,
        # each surfaced only once the previous one is fixed (compiler check
        # order: blocked_sku, then tag, then spend_per_tx).
        mcp = _CascadingMCPClient(
            [
                {"sku": "x", "unit_minor": 100_000, "tags": ["dairy"]},
                {"sku": "y-blocked", "unit_minor": 100_000, "tags": ["vegan"]},
                {"sku": "z", "unit_minor": 100_000, "tags": ["vegan"]},
            ],
            blocked_skus=["y-blocked"],
            allowed_tags=["vegan"],
            tag_mode="all",
            cap_minor=50_000,
        )
        agent = BuyerAgent(settings, mcp)

        rounds_seen: list[tuple[int, str]] = []

        async def _on_round(round_num, negotiation):
            rounds_seen.append((round_num, negotiation["reason_code"]))

        pending = await agent.shop("three scoops", "pol_neg_cap", "trace_neg_cap")
        assert pending["awaiting_reply"] is True
        result = await agent.continue_shop(
            pending["messages"],
            "yes",
            "pol_neg_cap",
            "trace_neg_cap",
            on_negotiation_round=_on_round,
        )

        assert result["allowed"] is False
        assert result["reason_code"] == "policy.spend_per_tx_exceeded"
        assert len(result["negotiation_rounds"]) == 3
        assert [r for _, r in rounds_seen] == [
            "policy.sku_blocked",
            "policy.tag_violation",
            "policy.spend_per_tx_exceeded",
        ]
        # every round made genuine, incremental progress (a shrinking cart) —
        # the loop never hangs, repeats an identical cart, or exceeds the cap
        sizes = [len(c) for c in mcp.create_cart_calls]
        assert sizes == [3, 2, 1]
        assert result["cart"] == [
            {
                "sku": "z",
                "merchant_id": "test-merchant",
                "qty": 1,
                "unit_minor": 100_000,
                "tags": ["vegan"],
                "name": "z",
            }
        ]

    async def test_non_negotiable_reason_code_reports_immediately(self, settings, session):
        _make_policy(
            session,
            policy_id="pol_neg_none",
            credential_id="cred_neg_none",
            user_handle="discord:4",
        )

        class _DenyOnceClient:
            async def search_products(self, query, tags=None, limit=10):
                return {
                    "success": True,
                    "data": {"items": [{"sku": "x", "unit_minor": 100, "tags": []}]},
                }

            async def call(self, tool_name, arguments):
                return {
                    "success": True,
                    "data": {
                        "allowed": False,
                        "reason_code": "policy.sku_duplicate",
                        "transcript": [],
                        "checkout_id": None,
                    },
                }

        agent = BuyerAgent(settings, _DenyOnceClient())
        pending = await agent.shop("dup order", "pol_neg_none", "trace_neg_none")
        assert pending["awaiting_reply"] is True
        result = await agent.continue_shop(
            pending["messages"], "yes", "pol_neg_none", "trace_neg_none"
        )

        assert result["allowed"] is False
        assert result["reason_code"] == "policy.sku_duplicate"
        assert result["negotiation_rounds"] == []
        # policy_hash is still populated for a non-negotiated policy.* denial
        # so BuyerBot can still offer an amendment.
        assert result["policy_hash"] == "h" * 64


class TestBuyerBotStreamsNegotiationRounds:
    async def test_handle_shop_streams_each_round_to_chat(self, settings, session):
        _make_policy(
            session,
            policy_id="pol_neg_stream",
            credential_id="cred_neg_stream",
            user_handle="discord:555002",
            allowed_tags=["vegan"],
            tag_mode="all",
        )
        mcp = _CartMCPClient(
            [
                {"sku": "vanilla", "unit_minor": 500, "tags": ["vegan"]},
                {"sku": "cream", "unit_minor": 500, "tags": ["dairy"]},
            ],
            allowed_tags=["vegan"],
            tag_mode="all",
            cap_minor=1_000_000,
        )
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        message = _FakeMessage(author_id=555002, channel_id=777002)

        await bot._handle_shop(message, "vegan gelato please")
        assert any(e.title == "Building your cart" for e in message.channel.embeds_sent)

        from openstore.core.database import get_session
        from openstore.core.shopping_session import find_active_session

        db = get_session(settings)
        try:
            pending = find_active_session(db, "discord", "555002", "777002")
        finally:
            db.close()
        assert pending is not None

        reply = _FakeMessage(author_id=555002, channel_id=777002, msg_id=2)
        reply.content = "yes"
        await bot._handle_conversation_reply(reply, pending.id)

        sent = reply.channel.sent
        assert any("tag policy" in s for s in sent)  # streamed negotiation update, no jargon
        embeds = reply.channel.embeds_sent
        assert any(e.title == "Order placed" for e in embeds)


def _register_queued_provider(monkeypatch, name: str, responses: list) -> None:
    from openstore.agents.llm import DummyProvider, register_provider

    queue = list(responses)

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            response = queue.pop(0)
            return response if isinstance(response, str) else _json.dumps(response)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


class _SimpleMCPClient:
    """Minimal MCP fake for conversational-shopping tests: create_cart
    always allows (no negotiation needed), checkout_initiate succeeds."""

    def __init__(self, catalog_items):
        self.catalog_items = catalog_items
        self.create_cart_calls: list[list[dict]] = []

    async def search_products(self, query, tags=None, limit=20):
        return {"success": True, "data": {"items": self.catalog_items}}

    async def call(self, tool_name, arguments):
        if tool_name == "create_cart":
            items = arguments["items"]
            self.create_cart_calls.append(items)
            total = sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in items)
            return {
                "success": True,
                "data": {
                    "allowed": True,
                    "reason_code": None,
                    "checkout_id": "chk_conv",
                    "aal_level": 0,
                    "effective_amount_minor": total,
                    "transcript": [],
                },
            }
        if tool_name == "checkout_initiate":
            return {
                "success": True,
                "data": {
                    "checkout_id": "chk_conv",
                    "state": "HELD",
                    "amount_minor": 15000,
                    "currency": "INR",
                    "short_url": "https://pay.example/conv",
                    "cancel_token": "tok_conv",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            }
        raise AssertionError(f"unexpected tool: {tool_name}")


class TestConversationalShopping:
    """S13: real tool-calling loop + multi-turn resumption across Discord
    messages, driven directly against BuyerBot's handlers (register()'s
    on_message is a thin dispatcher over these — see its own routing logic
    for what triggers each one)."""

    async def test_ask_creates_session_and_sends_question_as_plain_text(
        self, settings, session, monkeypatch
    ):
        _make_policy(
            session,
            policy_id="pol_conv_ask",
            credential_id="cred_conv_ask",
            user_handle="discord:900001",
        )
        _register_queued_provider(
            monkeypatch,
            "test_conv_ask",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "ask", "message": "No vanilla — want chocolate instead?"},
            ],
        )
        mcp = _SimpleMCPClient([])  # empty catalog -> no exact match
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        message = _FakeMessage(author_id=900001, channel_id=800001)

        await bot._handle_shop(message, "vanilla gelato please")

        assert message.channel.sent == [
            "🔍 Looking for vanilla…",
            "No vanilla — want chocolate instead?",
        ]
        assert message.channel.embeds_sent == []  # a question is plain text, not a card

        from openstore.core.database import get_session
        from openstore.models import ShoppingSession, ShoppingSessionState
        from sqlmodel import select

        db = get_session(settings)
        try:
            rows = db.exec(
                select(ShoppingSession).where(ShoppingSession.chat_user_id == "900001")
            ).all()
        finally:
            db.close()
        assert len(rows) == 1
        assert rows[0].state == ShoppingSessionState.AWAITING_REPLY

    async def test_full_multi_turn_round_trip_completes_purchase(
        self, settings, session, monkeypatch
    ):
        _make_policy(
            session,
            policy_id="pol_conv_full",
            credential_id="cred_conv_full",
            user_handle="discord:900002",
        )
        _register_queued_provider(
            monkeypatch,
            "test_conv_full",
            [
                {"action": "search", "query": "vanilla"},
                {"action": "ask", "message": "No vanilla — want chocolate instead?"},
                {"action": "search", "query": "chocolate"},
                {
                    "action": "answer",
                    "selections": [
                        {"sku": "gelato_chocolate", "merchant_id": "test-merchant", "qty": 1}
                    ],
                },
            ],
        )

        class _TwoRoundMCPClient(_SimpleMCPClient):
            async def search_products(self, query, tags=None, limit=20):
                if query == "vanilla":
                    return {"success": True, "data": {"items": []}, "error": None}
                return {
                    "success": True,
                    "data": {
                        "items": [
                            {"sku": "gelato_chocolate", "unit_minor": 15000, "tags": ["gelato"]}
                        ]
                    },
                    "error": None,
                }

        mcp = _TwoRoundMCPClient([])
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        first = _FakeMessage(author_id=900002, channel_id=800002)

        await bot._handle_shop(first, "vanilla gelato please")
        assert first.channel.sent == [
            "🔍 Looking for vanilla…",
            "No vanilla — want chocolate instead?",
        ]

        from openstore.core.database import get_session
        from openstore.core.shopping_session import find_active_session
        from openstore.models import ShoppingSession, ShoppingSessionState

        db = get_session(settings)
        try:
            pending = find_active_session(db, "discord", "900002", "800002")
        finally:
            db.close()
        assert pending is not None

        reply = _FakeMessage(author_id=900002, channel_id=800002, msg_id=2)
        reply.content = "chocolate please"
        await bot._handle_conversation_reply(reply, pending.id)

        # the LLM's "answer" pauses for confirmation (S16) — not placed yet
        assert any(e.title == "Building your cart" for e in reply.channel.embeds_sent)
        assert not any(e.title == "Order placed" for e in reply.channel.embeds_sent)
        assert mcp.create_cart_calls == []

        confirm = _FakeMessage(author_id=900002, channel_id=800002, msg_id=3)
        confirm.content = "yes"
        await bot._handle_conversation_reply(confirm, pending.id)

        assert any(e.title == "Order placed" for e in confirm.channel.embeds_sent)
        assert mcp.create_cart_calls == [
            [
                {
                    "sku": "gelato_chocolate",
                    "merchant_id": "test-merchant",
                    "qty": 1,
                    "unit_minor": 15000,
                    "tags": ["gelato"],
                    "name": "gelato_chocolate",
                }
            ]
        ]

        db = get_session(settings)
        try:
            row = db.get(ShoppingSession, pending.id)
        finally:
            db.close()
        assert row.state == ShoppingSessionState.COMPLETED

    async def test_max_conversation_turns_enforced(self, settings, session, monkeypatch):
        from openstore.agents.buyer_agent import MAX_CONVERSATION_TURNS

        _make_policy(
            session,
            policy_id="pol_conv_cap",
            credential_id="cred_conv_cap",
            user_handle="discord:900003",
        )
        _register_queued_provider(
            monkeypatch,
            "test_conv_cap",
            [{"action": "ask", "message": f"still not sure, round {i}"} for i in range(10)],
        )
        mcp = _SimpleMCPClient([])
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        first = _FakeMessage(author_id=900003, channel_id=800003)
        await bot._handle_shop(first, "something sweet")

        from openstore.core.database import get_session
        from openstore.core.shopping_session import find_active_session
        from openstore.models import ShoppingSession, ShoppingSessionState

        session_id: str | None = None
        last_reply = None
        for turn in range(1, MAX_CONVERSATION_TURNS + 1):
            db = get_session(settings)
            try:
                pending = find_active_session(db, "discord", "900003", "800003")
            finally:
                db.close()
            if pending is None:
                break
            session_id = pending.id
            reply = _FakeMessage(author_id=900003, channel_id=800003, msg_id=100 + turn)
            reply.content = f"reply {turn}"
            await bot._handle_conversation_reply(reply, session_id)
            last_reply = reply

        assert last_reply is not None
        assert "start over" in last_reply.channel.sent[-1]

        db = get_session(settings)
        try:
            row = db.get(ShoppingSession, session_id)
        finally:
            db.close()
        assert row.state == ShoppingSessionState.EXPIRED

    async def test_fresh_shop_supersedes_pending_session(self, settings, session, monkeypatch):
        _make_policy(
            session,
            policy_id="pol_conv_super",
            credential_id="cred_conv_super",
            user_handle="discord:900004",
        )
        _register_queued_provider(
            monkeypatch,
            "test_conv_super",
            [
                {"action": "ask", "message": "first question"},
                {"action": "ask", "message": "second question"},
            ],
        )
        mcp = _SimpleMCPClient([])
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        first = _FakeMessage(author_id=900004, channel_id=800004)
        await bot._handle_shop(first, "something sweet")

        from openstore.core.database import get_session
        from openstore.core.shopping_session import find_active_session
        from openstore.models import ShoppingSession, ShoppingSessionState

        db = get_session(settings)
        try:
            old_pending = find_active_session(db, "discord", "900004", "800004")
        finally:
            db.close()
        assert old_pending is not None
        old_id = old_pending.id

        # This is what on_message does before calling _handle_shop for a
        # fresh !shop command — a new request supersedes an unanswered one.
        await bot._maybe_cancel_pending_session("900004", "800004")
        second = _FakeMessage(author_id=900004, channel_id=800004, msg_id=2)
        await bot._handle_shop(second, "something else sweet")

        db = get_session(settings)
        try:
            old_row = db.get(ShoppingSession, old_id)
            new_pending = find_active_session(db, "discord", "900004", "800004")
        finally:
            db.close()
        assert old_row.state == ShoppingSessionState.CANCELLED
        assert new_pending is not None
        assert new_pending.id != old_id

    async def test_bare_cancel_aborts_pending_session_without_touching_checkout(
        self, settings, session, monkeypatch
    ):
        _make_policy(
            session,
            policy_id="pol_conv_cancel",
            credential_id="cred_conv_cancel",
            user_handle="discord:900005",
        )
        _register_queued_provider(
            monkeypatch, "test_conv_cancel", [{"action": "ask", "message": "what flavor?"}]
        )
        mcp = _SimpleMCPClient([])
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        first = _FakeMessage(author_id=900005, channel_id=800005)
        await bot._handle_shop(first, "something sweet")

        from openstore.core.database import get_session
        from openstore.core.shopping_session import find_active_session
        from openstore.models import ShoppingSession, ShoppingSessionState

        db = get_session(settings)
        try:
            pending = find_active_session(db, "discord", "900005", "800005")
        finally:
            db.close()
        assert pending is not None

        cancel_msg = _FakeMessage(author_id=900005, channel_id=800005, msg_id=2)
        await bot._handle_cancel_conversation(cancel_msg, pending.id)

        assert cancel_msg.channel.sent == ["No problem, order cancelled."]
        db = get_session(settings)
        try:
            row = db.get(ShoppingSession, pending.id)
        finally:
            db.close()
        assert row.state == ShoppingSessionState.CANCELLED
        assert mcp.create_cart_calls == []
