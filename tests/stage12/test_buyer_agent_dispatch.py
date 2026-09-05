# tests/stage12/test_buyer_agent_dispatch.py
# S12 step 7: BuyerAgent._submit_cart is the dispatch point start_shop/
# continue_shop route every confirmed cart through — federated when the
# injected MCP client fans out across merchants (duck-typed on client_for),
# legacy single-merchant otherwise.

from __future__ import annotations

from typing import Any

import pytest
from openstore.agents.buyer_agent import BuyerAgent


class _LegacyMCPClient:
    """Shaped like InProcessMCPClient/HttpMCPClient — no client_for."""

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected call in dispatch test: {tool_name}")

    async def search_products(self, query: str, tags=None, limit=10) -> dict[str, Any]:
        return {"success": True, "data": {"items": []}}


class _FederatingMCPClient:
    """Shaped like FederatingMCPClient — has client_for."""

    def client_for(self, merchant_id: str) -> Any:
        raise AssertionError("not exercised in this test")

    async def search_products(self, query: str, tags=None, limit=10) -> dict[str, Any]:
        return {"success": True, "data": {"items": []}}


class TestDispatch:
    async def test_routes_to_legacy_single_merchant_by_default(self, settings, monkeypatch):
        agent = BuyerAgent(settings, _LegacyMCPClient())
        calls: list[str] = []

        async def fake_single(
            cart,
            policy_id,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_round,
            request_text,
        ):
            calls.append("legacy")
            return {"allowed": True, "trace_id": trace_id}

        async def fake_federated(*args, **kwargs):
            calls.append("federated")
            return {"allowed": True}

        monkeypatch.setattr(agent, "_submit_cart_single_merchant", fake_single)
        monkeypatch.setattr(agent, "_submit_federated_cart", fake_federated)

        result = await agent._submit_cart(
            [{"sku": "gelato_vanilla", "qty": 1}],
            "pol_1",
            "trace_1",
            "discord",
            "u1",
            "c1",
            None,
            "goal text",
        )

        assert calls == ["legacy"]
        assert result == {"allowed": True, "trace_id": "trace_1"}

    async def test_routes_to_federated_when_client_fans_out(self, settings, monkeypatch):
        agent = BuyerAgent(settings, _FederatingMCPClient())
        calls: list[str] = []

        async def fake_single(*args, **kwargs):
            calls.append("legacy")
            return {"allowed": True}

        async def fake_federated(
            cart, trace_id, chat_platform, chat_user_id, chat_channel_id, on_round, request_text
        ):
            calls.append("federated")
            return {"allowed": True, "trace_id": trace_id, "per_merchant": {}}

        monkeypatch.setattr(agent, "_submit_cart_single_merchant", fake_single)
        monkeypatch.setattr(agent, "_submit_federated_cart", fake_federated)

        result = await agent._submit_cart(
            [{"sku": "gelato_vanilla", "merchant_id": "gelateria-milano", "qty": 1}],
            "pol_ignored",
            "trace_2",
            "discord",
            "u1",
            "c1",
            None,
            "goal text",
        )

        assert calls == ["federated"]
        assert result["trace_id"] == "trace_2"

    async def test_federated_dispatch_requires_chat_identity(self, settings, monkeypatch):
        agent = BuyerAgent(settings, _FederatingMCPClient())
        monkeypatch.setattr(
            agent,
            "_submit_federated_cart",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be reached")),
        )

        with pytest.raises(ValueError, match="chat_platform and chat_user_id"):
            await agent._submit_cart(
                [{"sku": "x", "merchant_id": "m1", "qty": 1}],
                "pol_1",
                "trace_3",
                None,
                None,
                None,
                None,
                "goal",
            )

    async def test_negotiation_round_callback_is_adapted_for_federated_path(
        self, settings, monkeypatch
    ):
        """The 2-arg (round, negotiation) callback start_shop/continue_shop
        accept must still be invoked, even though _submit_federated_cart's
        own callback shape carries a leading merchant_id."""
        agent = BuyerAgent(settings, _FederatingMCPClient())
        seen: list[tuple[int, dict[str, Any]]] = []

        async def on_round(round_num: int, negotiation: dict[str, Any]) -> None:
            seen.append((round_num, negotiation))

        async def fake_federated(
            cart,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_round_3arg,
            request_text,
        ):
            await on_round_3arg("gelateria-milano", 1, {"state": "COUNTERED"})
            return {"allowed": True, "trace_id": trace_id}

        monkeypatch.setattr(agent, "_submit_federated_cart", fake_federated)

        await agent._submit_cart(
            [{"sku": "x", "merchant_id": "gelateria-milano", "qty": 1}],
            "pol_1",
            "trace_4",
            "discord",
            "u1",
            "c1",
            on_round,
            "goal",
        )

        assert seen == [(1, {"state": "COUNTERED"})]
