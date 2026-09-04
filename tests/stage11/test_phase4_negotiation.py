# tests/stage11/test_phase4_negotiation.py
# S11 Phase 4: negotiation wired into BuyerAgent.shop()'s DENY branch, plus
# the apply_cart_delta executor (plan items #23-24).

from __future__ import annotations

from datetime import UTC, datetime

from openstore.agents.buyer_agent import BuyerAgent, BuyerBot
from openstore.agents.merchant_agent import apply_cart_delta
from openstore.models import IntentPolicy, WebAuthnCredential


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


class _FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


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

    async def search_products(self, query, limit=10):
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
                        "data": {"allowed": False, "reason_code": "policy.tag_violation",
                                  "transcript": [], "checkout_id": None},
                    }

            total = sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in items)
            if total > self.cap_minor:
                return {
                    "success": True,
                    "data": {"allowed": False, "reason_code": "policy.spend_per_tx_exceeded",
                              "transcript": [], "checkout_id": None},
                }

            return {
                "success": True,
                "data": {
                    "allowed": True, "reason_code": None, "checkout_id": "chk_neg",
                    "aal_level": 0, "effective_amount_minor": total, "transcript": [],
                },
            }
        if tool_name == "checkout_initiate":
            return {
                "success": True,
                "data": {
                    "checkout_id": "chk_neg", "state": "HELD", "amount_minor": 1,
                    "currency": "INR", "short_url": "https://pay.example/x",
                    "cancel_token": "tok", "expires_at": "2030-01-01T00:00:00Z",
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

    async def search_products(self, query, limit=10):
        return {"success": True, "data": {"items": self.catalog_items}}

    async def call(self, tool_name, arguments):
        assert tool_name == "create_cart"
        items = arguments["items"]
        self.create_cart_calls.append(items)

        if any(i.get("sku") in self.blocked_skus for i in items):
            return {
                "success": True,
                "data": {"allowed": False, "reason_code": "policy.sku_blocked",
                          "transcript": [], "checkout_id": None},
            }
        for item in items:
            tags = set(item.get("tags", []))
            ok = tags.issubset(self.allowed_tags) if self.tag_mode == "all" else bool(tags & self.allowed_tags)
            if not ok:
                return {
                    "success": True,
                    "data": {"allowed": False, "reason_code": "policy.tag_violation",
                              "transcript": [], "checkout_id": None},
                }
        total = sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in items)
        if total > self.cap_minor:
            return {
                "success": True,
                "data": {"allowed": False, "reason_code": "policy.spend_per_tx_exceeded",
                          "transcript": [], "checkout_id": None},
            }
        return {
            "success": True,
            "data": {"allowed": True, "reason_code": None, "checkout_id": "chk_neg",
                      "aal_level": 0, "effective_amount_minor": total, "transcript": []},
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


class TestShopNegotiation:
    async def test_tag_violation_resolves_via_one_round(self, settings, session):
        _make_policy(
            session, policy_id="pol_neg_tag", credential_id="cred_neg_tag",
            user_handle="discord:1", allowed_tags=["vegan"], tag_mode="all",
        )
        mcp = _CartMCPClient(
            [
                {"sku": "vanilla", "unit_minor": 500, "tags": ["vegan"]},
                {"sku": "cream", "unit_minor": 500, "tags": ["dairy"]},
            ],
            allowed_tags=["vegan"], tag_mode="all", cap_minor=1_000_000,
        )
        agent = BuyerAgent(settings, mcp)

        rounds_seen: list[tuple[int, dict]] = []

        async def _on_round(round_num, negotiation):
            rounds_seen.append((round_num, negotiation))

        result = await agent.shop(
            "vegan gelato", "pol_neg_tag", "trace_neg_tag",
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
            session, policy_id="pol_neg_spend", credential_id="cred_neg_spend",
            user_handle="discord:2", max_spend_per_tx_minor=5_000,
        )
        mcp = _CartMCPClient(
            [
                {"sku": "a", "unit_minor": 3_000, "tags": []},
                {"sku": "b", "unit_minor": 3_000, "tags": []},
            ],
            allowed_tags=[], tag_mode="all", cap_minor=5_000,
        )
        agent = BuyerAgent(settings, mcp)

        result = await agent.shop("two scoops", "pol_neg_spend", "trace_neg_spend")

        assert result["allowed"] is True
        assert len(mcp.create_cart_calls) == 2
        assert len(mcp.create_cart_calls[1]) == 1  # one item dropped

    async def test_negotiation_exhausts_round_cap_and_reports_final_denial(self, settings, session):
        _make_policy(
            session, policy_id="pol_neg_cap", credential_id="cred_neg_cap",
            user_handle="discord:3", allowed_tags=["vegan"], tag_mode="all",
            blocked_skus=["y-blocked"], max_spend_per_tx_minor=50_000,
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
            blocked_skus=["y-blocked"], allowed_tags=["vegan"], tag_mode="all",
            cap_minor=50_000,
        )
        agent = BuyerAgent(settings, mcp)

        rounds_seen: list[tuple[int, str]] = []

        async def _on_round(round_num, negotiation):
            rounds_seen.append((round_num, negotiation["reason_code"]))

        result = await agent.shop(
            "three scoops", "pol_neg_cap", "trace_neg_cap",
            on_negotiation_round=_on_round,
        )

        assert result["allowed"] is False
        assert result["reason_code"] == "policy.spend_per_tx_exceeded"
        assert len(result["negotiation_rounds"]) == 3
        assert [r for _, r in rounds_seen] == [
            "policy.sku_blocked", "policy.tag_violation", "policy.spend_per_tx_exceeded",
        ]
        # every round made genuine, incremental progress (a shrinking cart) —
        # the loop never hangs, repeats an identical cart, or exceeds the cap
        sizes = [len(c) for c in mcp.create_cart_calls]
        assert sizes == [3, 2, 1]
        assert result["cart"] == [{"sku": "z", "qty": 1, "unit_minor": 100_000, "tags": ["vegan"]}]

    async def test_non_negotiable_reason_code_reports_immediately(self, settings, session):
        _make_policy(session, policy_id="pol_neg_none", credential_id="cred_neg_none", user_handle="discord:4")

        class _DenyOnceClient:
            async def search_products(self, query, limit=10):
                return {"success": True, "data": {"items": [{"sku": "x", "unit_minor": 100, "tags": []}]}}

            async def call(self, tool_name, arguments):
                return {
                    "success": True,
                    "data": {"allowed": False, "reason_code": "policy.sku_duplicate",
                              "transcript": [], "checkout_id": None},
                }

        agent = BuyerAgent(settings, _DenyOnceClient())
        result = await agent.shop("dup order", "pol_neg_none", "trace_neg_none")

        assert result["allowed"] is False
        assert result["reason_code"] == "policy.sku_duplicate"
        assert result["negotiation_rounds"] == []
        # policy_hash is still populated for a non-negotiated policy.* denial
        # so BuyerBot can still offer an amendment.
        assert result["policy_hash"] == "h" * 64


class TestBuyerBotStreamsNegotiationRounds:
    async def test_handle_shop_streams_each_round_to_chat(self, settings, session):
        _make_policy(
            session, policy_id="pol_neg_stream", credential_id="cred_neg_stream",
            user_handle="discord:555002", allowed_tags=["vegan"], tag_mode="all",
        )
        mcp = _CartMCPClient(
            [
                {"sku": "vanilla", "unit_minor": 500, "tags": ["vegan"]},
                {"sku": "cream", "unit_minor": 500, "tags": ["dairy"]},
            ],
            allowed_tags=["vegan"], tag_mode="all", cap_minor=1_000_000,
        )
        bot = BuyerBot(settings, BuyerAgent(settings, mcp))
        message = _FakeMessage(author_id=555002, channel_id=777002)

        await bot._handle_shop(message, "vegan gelato please")

        sent = message.channel.sent
        assert any("round 1" in s and "tag policy" in s for s in sent)
        assert any(s.startswith("Order created.") for s in sent)
