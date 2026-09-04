# OpenStore agents — Buyer agent (S7.2 / §5.1)
# Discord bot, MCP client, planning loop.
# NEVER holds Razorpay credentials, payment links go out-of-band (R0.10).

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from sqlmodel import select

from openstore.agents.merchant_agent import MerchantAgent, apply_cart_delta
from openstore.config import Settings, merchant_id
from openstore.core.database import get_session
from openstore.core.handoff import HandoffError, create_handoff, require_active_policy
from openstore.models import Checkout, HandoffKind, IntentPolicy
from openstore.notifier import send_dm, sync_buyer_trace
from openstore.psp.razorpay_driver import RazorpayError, cancel_checkout_by_id

logger = logging.getLogger("openstore.buyer_agent")

# S11 Phase 4 (plan item #24): the three reason codes MerchantAgent.negotiate()
# knows how to counter. Any other reason_code (or exhausting the round cap
# below) is NO_COMPLIANT_PATH — negotiation stops, never loops silently (R0.5).
NEGOTIABLE_REASON_CODES = frozenset(
    {
        "policy.tag_violation",
        "policy.sku_blocked",
        "policy.spend_per_tx_exceeded",
    }
)
MAX_NEGOTIATION_ROUNDS = 3


class BuyerAgent:
    """
    S7.2: Buyer agent planning loop.
    parse goal → search_products → policy-aware cart → checkout_initiate
    → checkout_confirm → hold monitoring → report.

    Every money action goes through the compiler (R0.9). The agent NEVER
    sees Razorpay credentials; payment links go out-of-band to the human.
    """

    def __init__(self, config: Settings, mcp_client: Any):
        self.config = config
        self.mcp = mcp_client

    async def plan(self, goal: str, policy_id: str, trace_id: str) -> dict[str, Any]:
        """
        Build a cart from a high-level goal. Pure planning — no money movement.
        """
        sync_buyer_trace(trace_id, "plan_start", {"goal": goal, "policy_id": policy_id})

        search_result = await self.mcp.search_products(goal, limit=10)

        cart = []
        for item in (search_result.get("data") or {}).get("items", []):
            cart.append(
                {
                    "sku": item["sku"],
                    "qty": 1,
                    "unit_minor": item["unit_minor"],
                    "tags": item.get("tags", []),
                }
            )

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
        chat_platform: str | None = None,
        chat_user_id: str | None = None,
        chat_channel_id: str | None = None,
        on_negotiation_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Full shopping flow: plan → create_cart → checkout_initiate.

        Returns the payment link (short_url) and cancel_token, with the
        terminal checkout state, or an error carrying a reason_code.

        chat_platform/chat_user_id/chat_channel_id (S11 Phase 3 / Q-018) are
        optional — only BuyerBot._handle_shop knows them — and are forwarded
        to checkout_initiate so the Checkout row carries chat identity for
        the webhook push and hold-release loop.

        On a DENY whose reason_code is negotiable (S11 Phase 4, plan item
        #23-24), invokes MerchantAgent.negotiate() and applies the returned
        cart_delta via apply_cart_delta(), retrying create_cart with the
        mutated cart — up to MAX_NEGOTIATION_ROUNDS times (R0.5: never loop
        silently). on_negotiation_round, when given, is awaited once per
        round with (round_number, negotiation_message_dict) so a caller (e.g.
        BuyerBot._handle_shop) can stream the counter-offers into chat as
        they happen. A final, unresolved denial carries the negotiated
        `cart` and `policy_hash` so a caller can offer an amendment."""
        sync_buyer_trace(trace_id, "shop_start", {"goal": goal})

        plan_result = await self.plan(goal, policy_id, trace_id)
        cart = plan_result.get("cart", [])
        if not cart:
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": trace_id}

        cart_hash = compute_cart_hash(cart)
        cart_result = await self.mcp.call(
            "create_cart",
            {
                "merchant_id": merchant_id(self.config),
                "items": cart,
                "policy_id": policy_id,
                "cart_hash": cart_hash,
                "cart_version": 1,
            },
        )
        sync_buyer_trace(trace_id, "cart_compiled", cart_result)
        if not cart_result.get("success"):
            return {
                "allowed": False,
                "reason_code": cart_result.get("error", {}).get("reason_code", "buyer.cart_failed"),
                "trace_id": trace_id,
            }

        cart_data = cart_result.get("data", {})
        negotiation_rounds: list[dict[str, Any]] = []
        policy_hash: str | None = None
        round_num = 0
        while not cart_data.get("allowed") and round_num < MAX_NEGOTIATION_ROUNDS:
            reason_code = cart_data.get("reason_code")
            if reason_code not in NEGOTIABLE_REASON_CODES:
                break

            policy_fields = self._load_policy_fields(policy_id)
            if not policy_fields:
                break
            policy_hash = policy_fields.get("policy_hash")

            round_num += 1
            negotiation = MerchantAgent(self.config).negotiate(
                cart, reason_code, trace_id, policy_fields
            )
            negotiation_rounds.append(
                {"round": round_num, "reason_code": reason_code, **negotiation}
            )
            if on_negotiation_round is not None:
                await on_negotiation_round(round_num, negotiation)

            if negotiation.get("state") != "COUNTERED":
                break

            new_cart = apply_cart_delta(cart, negotiation.get("cart_delta", {}), policy_fields)
            if not new_cart or new_cart == cart:
                # No progress possible (e.g. the delta would empty the cart, or
                # nothing changed) — stop rather than loop silently (R0.5).
                break
            cart = new_cart
            cart_hash = compute_cart_hash(cart)

            cart_result = await self.mcp.call(
                "create_cart",
                {
                    "merchant_id": merchant_id(self.config),
                    "items": cart,
                    "policy_id": policy_id,
                    "cart_hash": cart_hash,
                    "cart_version": 1,
                },
            )
            sync_buyer_trace(trace_id, "cart_compiled", cart_result)
            if not cart_result.get("success"):
                return {
                    "allowed": False,
                    "reason_code": cart_result.get("error", {}).get(
                        "reason_code", "buyer.cart_failed"
                    ),
                    "trace_id": trace_id,
                    "negotiation_rounds": negotiation_rounds,
                }
            cart_data = cart_result.get("data", {})

        if not cart_data.get("allowed"):
            final_reason = cart_data.get("reason_code", "buyer.cart_denied")
            # policy_hash is only set above when a negotiation round actually
            # ran; a policy.* denial that was never negotiable (e.g. straight
            # to NO_COMPLIANT_PATH) still needs it so a caller can offer an
            # amendment (BuyerBot._maybe_offer_amendment).
            if (
                policy_hash is None
                and isinstance(final_reason, str)
                and final_reason.startswith("policy.")
            ):
                policy_hash = self._load_policy_fields(policy_id).get("policy_hash")
            return {
                "allowed": False,
                "reason_code": final_reason,
                "trace_id": trace_id,
                "transcript": cart_data.get("transcript"),
                "cart": cart,
                "policy_hash": policy_hash,
                "negotiation_rounds": negotiation_rounds,
            }

        checkout_id = cart_data.get("checkout_id")
        if not checkout_id:
            return {
                "allowed": False,
                "reason_code": "buyer.no_checkout_id",
                "trace_id": trace_id,
            }

        result = await self.mcp.call(
            "checkout_initiate",
            {
                "checkout_id": checkout_id,
                "chat_platform": chat_platform,
                "chat_user_id": chat_user_id,
                "chat_channel_id": chat_channel_id,
                "request_text": goal if chat_user_id else None,
            },
        )
        sync_buyer_trace(trace_id, "checkout_initiated", result)
        if not result.get("success"):
            return {
                "allowed": False,
                "reason_code": result.get("error", {}).get(
                    "reason_code", "buyer.checkout_initiate_failed"
                ),
                "trace_id": trace_id,
            }

        data = result.get("data", {})
        return {
            "allowed": True,
            "trace_id": trace_id,
            "checkout_id": data.get("checkout_id"),
            "state": data.get("state"),
            "amount_minor": data.get("amount_minor"),
            "currency": data.get("currency"),
            "short_url": data.get("short_url"),
            "cancel_token": data.get("cancel_token"),
            "expires_at": data.get("expires_at"),
            "aal_level": cart_data.get("aal_level"),
            "transcript": cart_data.get("transcript"),
            "effective_amount_minor": cart_data.get("effective_amount_minor"),
        }

    def _load_policy_fields(self, policy_id: str) -> dict[str, Any]:
        """Read-only policy snapshot for negotiation (S11 Phase 4): the fields
        apply_cart_delta() needs (allowed_tags/tag_mode/blocked_skus/
        max_spend_per_tx_minor/policy_hash). The compiler is still the sole
        authority on whether a retried cart compiles (R0.8/R0.9) — this is
        only used to compute what cart mutation to *try*, never to decide the
        outcome. Returns {} if the policy cannot be found."""
        session = get_session(self.config)
        try:
            policy = session.exec(select(IntentPolicy).where(IntentPolicy.id == policy_id)).first()
            if not policy:
                return {}
            return {
                "allowed_tags": policy.allowed_tags,
                "tag_mode": policy.tag_mode,
                "blocked_skus": policy.blocked_skus,
                "max_spend_per_tx_minor": policy.max_spend_per_tx_minor,
                "policy_hash": policy.policy_hash,
            }
        finally:
            session.close()

    async def confirm(
        self,
        checkout_id: str,
        trace_id: str,
        webauthn_assertion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Confirm an already-paid checkout (AAL1+ passes a webauthn_assertion,
        forwarded to checkout_confirm's real re-authorization check — plan
        item #14; previously always dropped, so no AAL1+ policy could ever
        be confirmed by the agent)."""
        return cast(
            "dict[str, Any]",
            await self.mcp.call(
                "checkout_confirm",
                {"checkout_id": checkout_id, "webauthn_assertion": webauthn_assertion},
            ),
        )

    async def hold_monitoring(self, checkout_id: str, trace_id: str) -> dict[str, Any]:
        """S7.2: Monitor the hold window. Periodically poll the order state."""
        sync_buyer_trace(trace_id, "hold_monitoring_start", {"checkout_id": checkout_id})
        result = await self.mcp.get_order(checkout_id)
        return cast("dict[str, Any]", result)


def compute_cart_hash(cart: list[dict[str, Any]]) -> str:
    """Deterministic cart hash (server-side recomputation, R0.8)."""
    sorted_cart = sorted(cart, key=lambda x: x.get("sku", ""))
    serialized = json.dumps(sorted_cart, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def render_shop_result(result: dict[str, Any]) -> str:
    """Render a full BuyerAgent.shop() outcome for chat — amount, short_url,
    AAL, checkout_id, and (on denial) the compiler transcript, instead of just
    allowed/reason (S11 plan item #11)."""
    if not result.get("allowed"):
        lines = [f"Order denied: {result.get('reason_code', 'unknown')}"]
        transcript = result.get("transcript")
        if transcript:
            lines.append(f"Transcript: {json.dumps(transcript, sort_keys=True)}")
        return "\n".join(lines)

    lines = ["Order created."]
    amount_minor = result.get("amount_minor")
    if amount_minor is not None:
        lines.append(f"Amount: ₹{amount_minor / 100:.2f}")
    if result.get("checkout_id"):
        lines.append(f"Checkout: {result['checkout_id']}")
    if result.get("aal_level") is not None:
        lines.append(f"AAL level: {result['aal_level']}")
    if result.get("short_url"):
        lines.append(f"Pay here: {result['short_url']}")
    if result.get("expires_at"):
        lines.append(f"Hold expires: {result['expires_at']}")
    return "\n".join(lines)


def _describe_cart_delta(cart_delta: dict[str, Any]) -> str:
    """Human-readable one-liner for a negotiation round's cart_delta flag
    (S11 Phase 4: "stream the counter-offers into chat as they happen")."""
    if cart_delta.get("remove_violating_tags"):
        return "removing items that violate your tag policy"
    if cart_delta.get("swap_sku"):
        return "removing the blocked SKU from your cart"
    if cart_delta.get("reduce_qty"):
        return "reducing quantities to fit your per-transaction cap"
    return "no compliant counter-offer"


def _signing_link(config: Settings, token: str) -> str:
    origin = (config.public_base_url or config.webauthn.origin or "").rstrip("/")
    return f"{origin}/intent/studio?token={token}"


# Discord bot (S7.2) — DM-first command handling, wired to a client owned and
# started by server.py's lifespan (S11 plan: one discord.Client, shared with
# the notifier via notifier.set_discord_client). BuyerBot never starts its own
# connection so there is only ever one Discord login per merchant process.
class BuyerBot:
    def __init__(self, config: Settings, agent: BuyerAgent):
        self.config = config
        self.agent = agent

    def register(self, client: Any) -> None:
        """Attach the DM-first !shop handler to a live, already-constructed
        discord.Client. DMs accept a bare "shop <goal>"; guild channels
        require the "!shop " prefix and the (privileged) message_content
        intent the operator must enable in the developer portal."""

        # discord.py's Client.event is untyped, so mypy cannot see through it.
        @client.event  # type: ignore[misc]
        async def on_message(message: Any) -> None:
            if message.author == client.user:
                return
            content = (message.content or "").strip()
            is_dm = message.guild is None

            goal: str | None = None
            if content.lower().startswith("!shop "):
                goal = content[len("!shop ") :].strip()
            elif is_dm and content.lower().startswith("shop "):
                goal = content[len("shop ") :].strip()
            if goal:
                await self._handle_shop(message, goal)
                return

            checkout_id: str | None = None
            if content.lower().startswith("!cancel "):
                checkout_id = content[len("!cancel ") :].strip()
            elif is_dm and content.lower().startswith("cancel "):
                checkout_id = content[len("cancel ") :].strip()
            if checkout_id:
                await self._handle_cancel(message, checkout_id)

    async def _handle_shop(self, message: Any, goal: str) -> None:
        chat_user_id = str(message.author.id)
        chat_channel_id = str(message.channel.id)
        trace_id = f"trace_{message.id}"
        buyer = f"discord:{chat_user_id}"

        session = get_session(self.config)
        try:
            policy_id = require_active_policy(session, buyer).id
        except HandoffError:
            handoff = create_handoff(
                session,
                kind=HandoffKind.POLICY,
                merchant_id=merchant_id(self.config),
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                request_text=goal,
            )
            session.commit()
            link = _signing_link(self.config, handoff.token)
            await message.channel.send(
                f"You need a signed spending policy before I can shop for you. Sign here: {link}"
            )
            return
        finally:
            session.close()

        async def _stream_round(round_num: int, negotiation: dict[str, Any]) -> None:
            await message.channel.send(
                f"Merchant proposes (round {round_num}): "
                f"{_describe_cart_delta(negotiation.get('cart_delta', {}))}. Retrying…"
            )

        result = await self.agent.shop(
            goal,
            policy_id,
            trace_id,
            chat_platform="discord",
            chat_user_id=chat_user_id,
            chat_channel_id=chat_channel_id,
            on_negotiation_round=_stream_round,
        )
        await message.channel.send(render_shop_result(result))

        if not result.get("allowed"):
            await self._maybe_offer_amendment(
                message, result, policy_id, chat_user_id, chat_channel_id, goal
            )

    async def _maybe_offer_amendment(
        self,
        message: Any,
        result: dict[str, Any],
        policy_id: str,
        chat_user_id: str,
        chat_channel_id: str,
        goal: str,
    ) -> None:
        """S11 Phase 4 (plan): once negotiation cannot resolve a DENY within
        policy, offer a human-approval-gated amendment — never an automatic
        fallback (R0.9: MerchantAgent output is a proposal, never a command).
        Only policy.* denials are amendment-eligible: buyer.* codes (no cart,
        no checkout_id, MCP failure) are not a policy rejection to amend."""
        reason_code = result.get("reason_code", "")
        cart = result.get("cart")
        policy_hash = result.get("policy_hash")
        if not reason_code.startswith("policy.") or not cart or not policy_hash:
            return

        trace_id = f"trace_amend_{message.id}"
        draft = MerchantAgent(self.config).draft_amendment(policy_hash, reason_code, cart, trace_id)

        session = get_session(self.config)
        try:
            handoff = create_handoff(
                session,
                kind=HandoffKind.AMENDMENT,
                merchant_id=merchant_id(self.config),
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                request_text=goal,
                amendment_draft={"draft": draft, "cart": cart},
            )
            session.commit()
        finally:
            session.close()

        link = _signing_link(self.config, handoff.token)
        await message.channel.send(
            "I can't resolve this within your current policy. I've drafted a "
            f"one-time amendment for your approval: {link}"
        )

    async def _handle_cancel(self, message: Any, checkout_id: str) -> None:
        """`cancel <checkout_id>` (PRD §3.7, plan item #16): the cancel_token
        never reaches the buyer as a raw link — the bot resolves it
        internally and replies in chat. Verifies the checkout belongs to the
        caller before touching it (fail loud, never leak another user's
        checkout — R0.5)."""
        chat_user_id = str(message.author.id)

        session = get_session(self.config)
        try:
            checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
            if checkout is None:
                await message.channel.send(f"No such checkout: {checkout_id}")
                return
            if checkout.chat_user_id != chat_user_id:
                await message.channel.send("Not your checkout.")
                return

            trace_id = f"trace_cancel_{message.id}"
            try:
                result = cancel_checkout_by_id(
                    config=self.config,
                    session=session,
                    trace_id=trace_id,
                    client_id=f"discord:{chat_user_id}",
                    checkout=checkout,
                )
            except RazorpayError as e:
                if e.error_code == "psp.invalid_state":
                    await message.channel.send("Already paid, nothing to cancel.")
                    return
                raise
            session.commit()
            if result.get("status") == "REFUND":
                await message.channel.send("Already paid — refunded.")
            else:
                await message.channel.send("Cancelled.")
        finally:
            session.close()


async def resume_after_signing(
    config: Settings,
    mcp_client: Any,
    *,
    chat_user_id: str,
    request_text: str,
    policy_id: str,
    trace_id: str,
    chat_platform: str | None = None,
    chat_channel_id: str | None = None,
) -> dict[str, Any]:
    """Auto-resume a parked errand after policy signing succeeds (S11 plan:
    "resume the stored request_text so they never retype"). DMs the buyer the
    full shop() outcome and returns it for the caller (studio.py) to log.

    chat_platform/chat_channel_id default to the handoff's own values
    (studio.py passes them from the just-consumed Handoff row) so the
    resumed Checkout still carries chat identity for the webhook push and
    hold-release loop."""
    agent = BuyerAgent(config, mcp_client)
    result = await agent.shop(
        request_text,
        policy_id,
        trace_id,
        chat_platform=chat_platform or "discord",
        chat_user_id=chat_user_id,
        chat_channel_id=chat_channel_id,
    )
    await send_dm(config, chat_user_id, render_shop_result(result))
    return result
