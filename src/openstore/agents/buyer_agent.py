# OpenStore agents — Buyer agent (S7.2 / §5.1)
# Discord bot, MCP client, planning loop.
# NEVER holds Razorpay credentials, payment links go out-of-band (R0.10).

from __future__ import annotations

import logging
from typing import Any, cast

from openstore.config import Settings
from openstore.notifier import sync_buyer_trace

logger = logging.getLogger("openstore.buyer_agent")


class BuyerAgent:
    """
    S7.2: Buyer agent planning loop.
    parse goal → search_products → policy-aware cart → checkout_initiate
    → checkout_confirm → hold monitoring → report.

    Every money action goes through the compiler (R0.9). The agent NEVER
    sees Razorpay credentials; payment links go out-of-band to the human.
    """

    def __init__(self, config: Settings, mcp_client: Any | None = None):
        self.config = config
        self.mcp = mcp_client

    async def plan(self, goal: str, policy_id: str, trace_id: str) -> dict[str, Any]:
        """
        Build a cart from a high-level goal. Pure planning — no money movement.
        """
        sync_buyer_trace(trace_id, "plan_start", {"goal": goal, "policy_id": policy_id})

        if self.mcp is None:
            # No MCP client: produce a stub plan
            sync_buyer_trace(trace_id, "plan_no_mcp", {"goal": goal})
            return {
                "trace_id": trace_id,
                "goal": goal,
                "plan": [{"step": "search_products", "args": {"query": goal}}],
                "policy_id": policy_id,
            }

        # If MCP client is available, use it
        search_result = await self.mcp.call(
            "search_products", {"query": goal, "limit": 10}
        )

        cart = []
        for item in search_result.get("data", {}).get("items", []):
            cart.append({
                "sku": item["sku"],
                "qty": 1,
                "unit_minor": item["unit_minor"],
                "tags": item.get("tags", []),
            })

        sync_buyer_trace(trace_id, "plan_done", {"items": len(cart), "policy_id": policy_id})
        return {
            "trace_id": trace_id,
            "goal": goal,
            "cart": cart,
            "policy_id": policy_id,
        }

    async def shop(
        self,
        goal: str,
        policy_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """Full shopping flow. Returns a result with allowed/reason_code."""
        sync_buyer_trace(trace_id, "shop_start", {"goal": goal})

        # Step 1: Plan
        plan_result = await self.plan(goal, policy_id, trace_id)
        cart = plan_result.get("cart", [])
        if not cart:
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": trace_id}

        if self.mcp is None:
            return {"allowed": False, "reason_code": "buyer.no_mcp_client", "trace_id": trace_id}

        # Step 2: Create cart (compile via MCP)
        cart_hash = compute_cart_hash(cart)
        result = await self.mcp.call("create_cart", {
            "merchant_id": self.config.merchant.name.lower().replace(" ", "-"),
            "items": cart,
            "policy_id": policy_id,
            "cart_hash": cart_hash,
            "cart_version": 1,
        })

        sync_buyer_trace(trace_id, "cart_compiled", result)
        return cast("dict[str, Any]", result)

    async def hold_monitoring(self, checkout_id: str, trace_id: str) -> dict[str, Any]:
        """S7.2: Monitor the hold window. Periodically poll the order state."""
        sync_buyer_trace(trace_id, "hold_monitoring_start", {"checkout_id": checkout_id})
        if self.mcp is None:
            return {"status": "unknown", "checkout_id": checkout_id}

        result = await self.mcp.call("get_order", {"checkout_id": checkout_id})
        return cast("dict[str, Any]", result)


def compute_cart_hash(cart: list[dict[str, Any]]) -> str:
    """Deterministic cart hash (server-side recomputation, R0.8)."""
    import hashlib
    import json
    sorted_cart = sorted(cart, key=lambda x: x.get("sku", ""))
    serialized = json.dumps(sorted_cart, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


# Discord bot (S7.2) — runs the planning loop when invoked
class BuyerBot:
    def __init__(self, config: Settings, agent: BuyerAgent | None = None):
        self.config = config
        self.agent = agent or BuyerAgent(config)
        self._client: Any | None = None

    async def start(self) -> None:
        token = self.config.discord.bot_token
        if not token or token == "token":
            logger.info("BuyerBot: no token, skipping Discord start")
            return
        try:
            import discord
            self._client = discord.Client(intents=discord.Intents.default())

            @self._client.event
            async def on_message(message: Any) -> None:
                client = self._client
                if client is None:
                    return
                if message.author == client.user:
                    return
                if message.content.startswith("!shop "):
                    goal = message.content[6:].strip()
                    result = await self.agent.shop(goal, "default_policy", f"trace_{message.id}")
                    await message.channel.send(f"Result: {result.get('allowed', False)}, reason: {result.get('reason_code', 'none')}")

            await self._client.start(token)
        except Exception as e:
            logger.warning(f"BuyerBot start failed: {e}")
