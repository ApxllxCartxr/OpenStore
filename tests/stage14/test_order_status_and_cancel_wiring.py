# tests/stage14/test_order_status_and_cancel_wiring.py
# S14: FederatedBuyerBot's _report_order_status/_handle_cancel_request —
# the zero/one/many-HELD-orders branching across merchants. Graph-level
# action wiring (callback fires, pause) is covered directly against
# BuyerGraph in tests/stage07/test_agents.py; this covers what the callback
# actually DOES once fired, fanning out across however many merchants the
# federated buyer reaches.

from __future__ import annotations

from typing import Any

from openstore.agents.buyer_agent import BuyerAgent, FederatedBuyerBot


class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        if content is not None:
            self.sent.append(content)


class _FakeMessage:
    def __init__(self):
        self.channel = _FakeChannel()


class _FakeOrigin:
    def __init__(self, name: str, merchant_id: str):
        self.name = name
        self.merchant_id = merchant_id


class _FakeOrderClient:
    def __init__(self, orders: list[dict[str, Any]], cancel_result: dict[str, Any] | None = None):
        self._orders = orders
        self._cancel_result = cancel_result or {"success": True, "data": {"status": "RELEASE"}}
        self.cancel_calls: list[dict[str, Any]] = []

    async def call(self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool = True):
        if tool_name == "list_orders":
            return {"success": True, "data": {"orders": self._orders}}
        if tool_name == "cancel_order":
            self.cancel_calls.append(arguments)
            return self._cancel_result
        raise AssertionError(f"unexpected tool: {tool_name}")


class _FakeFederatingClient:
    def __init__(self, per_merchant: dict[str, tuple[str, _FakeOrderClient]]):
        self._origins = [_FakeOrigin(name, mid) for mid, (name, _) in per_merchant.items()]
        self._clients = {mid: client for mid, (_, client) in per_merchant.items()}

    def merchants(self) -> list[_FakeOrigin]:
        return self._origins

    def client_for(self, merchant_id: str) -> _FakeOrderClient:
        return self._clients[merchant_id]


def _bot(mcp: _FakeFederatingClient) -> FederatedBuyerBot:
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

    config = Settings(
        merchant=MerchantConfig(name="Test Merchant", currency="INR"),
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
    return FederatedBuyerBot(config, BuyerAgent(config, mcp))


class TestReportOrderStatus:
    async def test_reports_the_single_most_recent_order_across_merchants(self):
        mcp = _FakeFederatingClient(
            {
                "store-a": (
                    "Store A",
                    _FakeOrderClient(
                        [
                            {
                                "checkout_id": "chk_a",
                                "state": "PAID",
                                "amount_minor": 15000,
                                "currency": "INR",
                                "created_at": "2026-09-01T00:00:00",
                            }
                        ]
                    ),
                ),
                "store-b": (
                    "Store B",
                    _FakeOrderClient(
                        [
                            {
                                "checkout_id": "chk_b",
                                "state": "HELD",
                                "amount_minor": 12000,
                                "currency": "INR",
                                "created_at": "2026-09-02T00:00:00",
                            }
                        ]
                    ),
                ),
            }
        )
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._report_order_status(message, "discord", "u1")

        assert len(message.channel.sent) == 1
        assert "Store B" in message.channel.sent[0]
        assert "awaiting payment" in message.channel.sent[0]

    async def test_no_orders_anywhere_says_so(self):
        mcp = _FakeFederatingClient({"store-a": ("Store A", _FakeOrderClient([]))})
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._report_order_status(message, "discord", "u1")

        assert message.channel.sent == ["I don't see any orders for you yet."]


class TestHandleCancelRequest:
    async def test_cancels_the_one_held_order_when_unambiguous(self):
        held_client = _FakeOrderClient(
            [
                {
                    "checkout_id": "chk_held",
                    "state": "HELD",
                    "amount_minor": 15000,
                    "currency": "INR",
                    "created_at": "2026-09-01T00:00:00",
                }
            ]
        )
        mcp = _FakeFederatingClient(
            {
                "store-a": ("Store A", held_client),
                "store-b": ("Store B", _FakeOrderClient([])),
            }
        )
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._handle_cancel_request(message, "discord", "u1")

        assert held_client.cancel_calls == [
            {"checkout_id": "chk_held", "chat_platform": "discord", "chat_user_id": "u1"}
        ]
        assert message.channel.sent == ["Cancelled."]

    async def test_asks_which_store_when_held_at_more_than_one(self):
        client_a = _FakeOrderClient(
            [
                {
                    "checkout_id": "chk_a",
                    "state": "HELD",
                    "amount_minor": 15000,
                    "currency": "INR",
                    "created_at": "2026-09-01T00:00:00",
                }
            ]
        )
        client_b = _FakeOrderClient(
            [
                {
                    "checkout_id": "chk_b",
                    "state": "HELD",
                    "amount_minor": 12000,
                    "currency": "INR",
                    "created_at": "2026-09-02T00:00:00",
                }
            ]
        )
        mcp = _FakeFederatingClient(
            {"store-a": ("Store A", client_a), "store-b": ("Store B", client_b)}
        )
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._handle_cancel_request(message, "discord", "u1")

        assert client_a.cancel_calls == []
        assert client_b.cancel_calls == []
        assert len(message.channel.sent) == 1
        assert "Store A" in message.channel.sent[0]
        assert "Store B" in message.channel.sent[0]

    async def test_no_held_orders_anywhere_says_so(self):
        mcp = _FakeFederatingClient({"store-a": ("Store A", _FakeOrderClient([]))})
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._handle_cancel_request(message, "discord", "u1")

        assert message.channel.sent == ["You have no open orders to cancel."]

    async def test_already_paid_at_cancel_time_reports_that_instead(self):
        client = _FakeOrderClient(
            [
                {
                    "checkout_id": "chk_race",
                    "state": "HELD",
                    "amount_minor": 15000,
                    "currency": "INR",
                    "created_at": "2026-09-01T00:00:00",
                }
            ],
            cancel_result={
                "success": False,
                "error": {"reason_code": "psp.invalid_state", "message": "invalid_state: PAID"},
            },
        )
        mcp = _FakeFederatingClient({"store-a": ("Store A", client)})
        bot = _bot(mcp)
        message = _FakeMessage()

        await bot._handle_cancel_request(message, "discord", "u1")

        assert message.channel.sent == ["That one's already paid, so there's nothing to cancel."]
