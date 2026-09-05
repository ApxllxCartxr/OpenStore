# tests/stage12/test_two_phase_checkout.py
# S12 plan item (step 6): two-phase checkout across multiple merchants, with
# per-merchant enrollment. Exercises BuyerAgent._submit_federated_cart
# directly (fakes stand in for the per-merchant MCP client — real HTTP
# fan-out is already covered by test_federating_mcp_client.py) and the two
# federated Discord embed builders.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from openstore.agents.buyer_agent import (
    BuyerAgent,
    build_federated_cart_preview_embed,
    build_federated_shop_result_embed,
)
from openstore.buyer_config import BuyerSettings, MerchantOrigin
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


@pytest.fixture()
def config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
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
        public_base_url="https://buyer.example",
    )


_POLICY_FIELDS = {
    "policy_id": "pol-1",
    "allowed_tags": [],
    "tag_mode": "all",
    "blocked_skus": [],
    "max_spend_per_tx_minor": 1_000_000,
    "policy_hash": "h" * 8,
}


class _FakeMerchantClient:
    """Stands in for HttpMCPClient at one merchant. Every call is recorded
    so tests can assert on ordering/gating, not just final outcomes."""

    def __init__(
        self,
        merchant_id: str,
        *,
        unsigned: bool = False,
        policy_fields: dict[str, Any] | None = None,
        create_cart_response: dict[str, Any] | None = None,
        checkout_response: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> None:
        self.merchant_id = merchant_id
        self.unsigned = unsigned
        self.policy_fields = policy_fields or dict(_POLICY_FIELDS)
        self.create_cart_response = create_cart_response
        self.checkout_response = checkout_response
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Mirrors HttpMCPClient.merchant (a MerchantOrigin): the federated
        # enrollment link is built from this origin's base_url, never from
        # buyer config.
        self.merchant = SimpleNamespace(
            base_url=base_url or f"https://{merchant_id}.example",
            merchant_id=merchant_id,
            name=merchant_id,
        )

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool = True
    ) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "resolve_policy":
            if self.unsigned:
                return {
                    "success": False,
                    "error": {"reason_code": "authority.policy_unsigned", "message": "no policy"},
                }
            return {"success": True, "data": self.policy_fields}
        if tool_name == "create_policy_handoff":
            return {"success": True, "data": {"token": f"tok-{self.merchant_id}"}}
        if tool_name == "create_cart":
            assert self.create_cart_response is not None
            return self.create_cart_response
        if tool_name == "checkout_initiate":
            assert self.checkout_response is not None
            return self.checkout_response
        raise AssertionError(f"unexpected tool call: {tool_name}")


class _FakeFederatingClient:
    def __init__(self, clients: dict[str, _FakeMerchantClient]) -> None:
        self._clients = clients

    def client_for(self, merchant_id: str) -> _FakeMerchantClient:
        return self._clients[merchant_id]


def _cart_ok(checkout_id: str, amount_minor: int) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "allowed": True,
            "checkout_id": checkout_id,
            "aal_level": "AAL0",
            "transcript": None,
            "effective_amount_minor": amount_minor,
        },
    }


def _checkout_ok(checkout_id: str, amount_minor: int, short_url: str) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "checkout_id": checkout_id,
            "state": "PENDING",
            "amount_minor": amount_minor,
            "currency": "INR",
            "short_url": short_url,
            "cancel_token": f"cancel-{checkout_id}",
            "expires_at": "2026-01-01T00:00:00Z",
        },
    }


def _item(merchant_id: str, sku: str, qty: int, unit_minor: int) -> dict[str, Any]:
    return {
        "merchant_id": merchant_id,
        "sku": sku,
        "qty": qty,
        "unit_minor": unit_minor,
        "tags": [],
        "name": sku,
    }


@pytest.mark.asyncio
async def test_two_merchants_both_validate_and_checkout(config: Settings) -> None:
    cart = [
        _item("store-a", "sku-a", 1, 500),
        _item("store-b", "sku-b", 2, 300),
    ]
    client_a = _FakeMerchantClient(
        "store-a",
        create_cart_response=_cart_ok("co-a", 500),
        checkout_response=_checkout_ok("co-a", 500, "https://pay/a"),
    )
    client_b = _FakeMerchantClient(
        "store-b",
        create_cart_response=_cart_ok("co-b", 600),
        checkout_response=_checkout_ok("co-b", 600, "https://pay/b"),
    )
    mcp = _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-1", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["allowed"] is True
    assert result["grand_total_minor"] == 1100
    assert set(result["per_merchant"]) == {"store-a", "store-b"}
    assert result["per_merchant"]["store-a"]["short_url"] == "https://pay/a"
    assert result["per_merchant"]["store-b"]["short_url"] == "https://pay/b"
    # Both merchants actually reached phase 2.
    assert [c[0] for c in client_a.calls] == ["resolve_policy", "create_cart", "checkout_initiate"]
    assert [c[0] for c in client_b.calls] == ["resolve_policy", "create_cart", "checkout_initiate"]


@pytest.mark.asyncio
async def test_merchant_denial_in_phase1_blocks_checkout_everywhere(config: Settings) -> None:
    cart = [
        _item("store-a", "sku-a", 1, 500),
        _item("store-b", "sku-b", 1, 300),
    ]
    client_a = _FakeMerchantClient(
        "store-a",
        create_cart_response=_cart_ok("co-a", 500),
        checkout_response=_checkout_ok("co-a", 500, "https://pay/a"),
    )
    client_b = _FakeMerchantClient(
        "store-b",
        # Not in NEGOTIABLE_REASON_CODES — denies straight away, no negotiation.
        create_cart_response={
            "success": True,
            "data": {"allowed": False, "reason_code": "policy.merchant_mismatch"},
        },
    )
    mcp = _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-2", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "buyer.federation_partial_failure"
    assert result["per_merchant"]["store-a"]["allowed"] is True
    assert result["per_merchant"]["store-b"]["allowed"] is False
    assert result["per_merchant"]["store-b"]["reason_code"] == "policy.merchant_mismatch"
    # The gate must actually gate: A validated fine but checkout_initiate
    # must NEVER be called for A once B's slice denies.
    assert "checkout_initiate" not in [c[0] for c in client_a.calls]
    assert "checkout_initiate" not in [c[0] for c in client_b.calls]


@pytest.mark.asyncio
async def test_psp_failure_in_phase2_reports_partial_success(config: Settings) -> None:
    cart = [
        _item("store-a", "sku-a", 1, 500),
        _item("store-b", "sku-b", 1, 300),
    ]
    client_a = _FakeMerchantClient(
        "store-a",
        create_cart_response=_cart_ok("co-a", 500),
        checkout_response=_checkout_ok("co-a", 500, "https://pay/a"),
    )
    client_b = _FakeMerchantClient(
        "store-b",
        create_cart_response=_cart_ok("co-b", 300),
        checkout_response={
            "success": False,
            "error": {"reason_code": "psp.charge_failed", "message": "gateway down"},
        },
    )
    mcp = _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-3", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "buyer.federation_partial_failure"
    assert result["per_merchant"]["store-a"]["allowed"] is True
    assert result["per_merchant"]["store-a"]["short_url"] == "https://pay/a"
    assert result["per_merchant"]["store-b"]["allowed"] is False
    assert result["per_merchant"]["store-b"]["reason_code"] == "psp.charge_failed"
    # Both merchants did reach phase 2 (validation passed for both) — this
    # is a real PSP failure, not a gating bug.
    assert [c[0] for c in client_a.calls] == ["resolve_policy", "create_cart", "checkout_initiate"]
    assert [c[0] for c in client_b.calls] == ["resolve_policy", "create_cart", "checkout_initiate"]


@pytest.mark.asyncio
async def test_unenrolled_merchant_surfaces_signing_link_no_checkout(config: Settings) -> None:
    cart = [_item("store-a", "sku-a", 1, 500)]
    client_a = _FakeMerchantClient("store-a", unsigned=True)
    mcp = _FakeFederatingClient({"store-a": client_a})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-4", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["awaiting_reply"] is True
    assert "https://store-a.example/intent/studio?token=tok-store-a" in result["question"]
    assert [c[0] for c in client_a.calls] == ["resolve_policy", "create_policy_handoff"]
    # Regression: this used to return "messages": [] here, silently discarding
    # the buyer's already-confirmed cart. Once signing completed there was
    # nothing left to resume from — the buyer's next reply reached the LLM
    # with zero context. The cart must survive as the same tool_result marker
    # _confirm_cart uses, so pending_cart_from_messages() finds it again.
    from openstore.agents.buyer_graph import pending_cart_from_messages

    assert pending_cart_from_messages(result["messages"]) == cart


@pytest.mark.asyncio
async def test_two_unenrolled_merchants_surface_both_links_at_once(config: Settings) -> None:
    cart = [_item("store-a", "sku-a", 1, 500), _item("store-b", "sku-b", 1, 300)]
    client_a = _FakeMerchantClient("store-a", unsigned=True)
    client_b = _FakeMerchantClient("store-b", unsigned=True)
    mcp = _FakeFederatingClient({"store-a": client_a, "store-b": client_b})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-5", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["awaiting_reply"] is True
    assert "tok-store-a" in result["question"]
    assert "tok-store-b" in result["question"]


@pytest.mark.asyncio
async def test_federated_signing_link_needs_no_single_merchant_config() -> None:
    """Regression: the federated enrollment path crashed with
    AttributeError: 'BuyerSettings' object has no attribute
    'public_base_url' because it derived the signing link from buyer
    config. The link must come from the merchant origin's base_url —
    a real BuyerSettings (no merchant block, no public_base_url,
    no webauthn) exercises this."""
    buyer_config = BuyerSettings(
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        merchants=[
            MerchantOrigin(
                name="Store A",
                base_url="https://store-a.example",
                client_id="cid-a",
                client_secret="secret-a",
            ),
        ],
    )
    client_a = _FakeMerchantClient("store-a", unsigned=True)
    mcp = _FakeFederatingClient({"store-a": client_a})
    agent = BuyerAgent(buyer_config, mcp)  # type: ignore[arg-type]

    result = await agent._submit_federated_cart(
        [_item("store-a", "sku-a", 1, 500)],
        "trace-buyer-settings",
        "discord",
        "buyer1",
        "chan1",
        None,
        "goal text",
    )

    assert result["awaiting_reply"] is True
    assert "https://store-a.example/intent/studio?token=tok-store-a" in result["question"]
    assert [c[0] for c in client_a.calls] == ["resolve_policy", "create_policy_handoff"]


@pytest.mark.asyncio
async def test_single_merchant_cart_still_returns_one_entry(config: Settings) -> None:
    cart = [_item("store-a", "sku-a", 3, 200)]
    client_a = _FakeMerchantClient(
        "store-a",
        create_cart_response=_cart_ok("co-a", 600),
        checkout_response=_checkout_ok("co-a", 600, "https://pay/a"),
    )
    mcp = _FakeFederatingClient({"store-a": client_a})
    agent = BuyerAgent(config, mcp)

    result = await agent._submit_federated_cart(
        cart, "trace-6", "discord", "buyer1", "chan1", None, "goal text"
    )

    assert result["allowed"] is True
    assert list(result["per_merchant"].keys()) == ["store-a"]
    assert result["grand_total_minor"] == 600


def test_build_federated_cart_preview_embed_groups_by_store() -> None:
    cart = [
        _item("store-a", "sku-a", 1, 500),
        _item("store-a", "sku-a2", 2, 100),
        _item("store-b", "sku-b", 1, 300),
    ]
    embed = build_federated_cart_preview_embed(cart, [])

    field_names = [f["name"] for f in embed["fields"]]
    assert "store-a" in field_names
    assert "store-b" in field_names
    assert "Grand total" in field_names

    store_a_field = next(f for f in embed["fields"] if f["name"] == "store-a")
    assert "1 × sku-a — ₹5.00" in store_a_field["value"]
    assert "2 × sku-a2 — ₹1.00" in store_a_field["value"]
    assert "Subtotal: ₹7.00" in store_a_field["value"]

    grand_total_field = next(f for f in embed["fields"] if f["name"] == "Grand total")
    # 500 + 200 + 300 = 1000 minor => 10.00
    assert grand_total_field["value"] == "₹10.00"


def test_build_federated_shop_result_embed_partial_success() -> None:
    result = {
        "allowed": False,
        "reason_code": "buyer.federation_partial_failure",
        "per_merchant": {
            "store-a": {
                "allowed": True,
                "short_url": "https://pay/a",
                "expires_at": "2026-01-01T00:00:00Z",
            },
            "store-b": {"allowed": False, "reason_code": "policy.merchant_mismatch"},
        },
    }
    embed = build_federated_shop_result_embed(result)

    assert embed["title"] == "1 of 2 orders placed"
    store_a_field = next(f for f in embed["fields"] if f["name"] == "store-a")
    assert "https://pay/a" in store_a_field["value"]
    store_b_field = next(f for f in embed["fields"] if f["name"] == "store-b")
    assert "your policy blocks" in store_b_field["value"] or "policy" in store_b_field["value"]


def test_build_federated_shop_result_embed_full_success() -> None:
    result = {
        "allowed": True,
        "grand_total_minor": 1100,
        "per_merchant": {
            "store-a": {"allowed": True, "short_url": "https://pay/a"},
            "store-b": {"allowed": True, "short_url": "https://pay/b"},
        },
    }
    embed = build_federated_shop_result_embed(result)

    assert embed["title"] == "All 2 orders placed"
    field_names = [f["name"] for f in embed["fields"]]
    assert "Grand total" in field_names
