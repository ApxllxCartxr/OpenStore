# OpenStore agents — Buyer agent (S7.2 / §5.1)
# Discord bot, MCP client, planning loop.
# NEVER holds Razorpay credentials, payment links go out-of-band (R0.10).

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlmodel import select

from openstore.agents.buyer_graph import (
    BuyerGraph,
    BuyerPlanError,
    cart_signature,
    is_affirmative_reply,
    pending_cart_from_messages,
)
from openstore.agents.llm import LLMError
from openstore.agents.merchant_agent import MerchantAgent, apply_cart_delta
from openstore.config import Settings, merchant_id
from openstore.core.database import get_session
from openstore.core.handoff import HandoffError, create_handoff, require_active_policy
from openstore.core.shopping_session import (
    advance_session,
    close_session,
    create_session,
    find_active_session,
    take_expired_session,
)
from openstore.models import (
    Checkout,
    HandoffKind,
    IntentPolicy,
    OrderState,
    ShoppingSession,
    ShoppingSessionState,
)
from openstore.notifier import DiscordNotifier, build_discord_embed, send_dm, sync_alert
from openstore.psp.razorpay_driver import RazorpayError, cancel_checkout_by_id
from openstore.surfaces.catalog import suggest_related_items

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

# S13: total buyer replies allowed in one conversational-shopping session
# (BuyerGraph asking a follow-up, the buyer answering) before the bot gives
# up and asks the buyer to start over with !shop — a hard Python-side cap,
# outside the LLM's reach, same pattern as MAX_NEGOTIATION_ROUNDS (R0.5). A
# free confirmation of an already-built cart never counts against this (see
# _handle_conversation_reply) — 20 is a bound on genuine back-and-forth
# (browsing, revisions, questions), not on how long checkout itself takes.
MAX_CONVERSATION_TURNS = 20


class BuyerAgent:
    """
    S7.2: Buyer agent planning loop.
    parse goal → search_products → policy-aware cart → checkout_initiate
    → checkout_confirm → hold monitoring → report.

    Every money action goes through the compiler (R0.9). The agent NEVER
    sees Razorpay credentials; payment links go out-of-band to the human.
    """

    def __init__(self, config: Settings, mcp_client: Any, *, resume_url: str | None = None):
        self.config = config
        self.mcp = mcp_client
        self._graph = BuyerGraph(config, mcp_client)
        # S12 step 8: this buyer's own /internal/signing-complete URL,
        # handed to merchants via create_policy_handoff so they can ping
        # back once a policy is signed. None for the local single-process
        # path and for any federated buyer that opted out
        # (BuyerSettings.signing_complete_url() == None) — those merchants
        # never get a resume_url and behave exactly as before this feature.
        self._resume_url = resume_url
        # Correlates a handoff_token THIS buyer minted (below) to the chat
        # identity it belongs to, so the signing-complete endpoint
        # (surfaces/buyer_internal.py) knows who to re-check — never trust
        # anything the incoming ping itself claims about identity.
        self._pending_handoffs: dict[str, dict[str, str]] = {}
        # S12 UX polish: a display-only aggregation of the signing links for
        # one federated cart's unenrolled merchants, so chat can hand the
        # buyer one link instead of N raw studio URLs. Carries no signing
        # authority — each entry's sign_url still points at that merchant's
        # own /intent/studio origin; this dict is never consulted by anything
        # that grants or verifies a policy (see surfaces/buyer_studio.py).
        self._enrollment_groups: dict[str, dict[str, Any]] = {}

    def create_enrollment_group(self, user_id: str, entries: list[dict[str, str]]) -> str:
        """entries: [{"merchant_id", "merchant_name", "sign_url"}, ...]."""
        group_id = secrets.token_urlsafe(16)
        self._enrollment_groups[group_id] = {"user_id": user_id, "entries": entries}
        return group_id

    def get_enrollment_group(self, group_id: str) -> dict[str, Any] | None:
        return self._enrollment_groups.get(group_id)

    def record_pending_handoff(
        self,
        token: str,
        *,
        merchant_id: str,
        chat_platform: str,
        chat_user_id: str,
        chat_channel_id: str,
    ) -> None:
        self._pending_handoffs[token] = {
            "merchant_id": merchant_id,
            "chat_platform": chat_platform,
            "chat_user_id": chat_user_id,
            "chat_channel_id": chat_channel_id,
        }

    def resolve_pending_handoff(self, token: str) -> dict[str, str] | None:
        """Pop-and-return: a token is meaningfully checked at most once (the
        DM it triggers, or the silent no-op, is the whole point of the round
        trip), so popping here also keeps this map from growing unboundedly
        over a long-running process."""
        return self._pending_handoffs.pop(token, None)

    async def start_shop(
        self,
        goal: str,
        policy_id: str,
        trace_id: str,
        chat_platform: str | None = None,
        chat_user_id: str | None = None,
        chat_channel_id: str | None = None,
        on_negotiation_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
        on_cart_ready: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_search: Callable[[str], Awaitable[None]] | None = None,
        on_menu_ready: Callable[[str | None], Awaitable[None]] | None = None,
        on_cart_shown: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_order_status: Callable[[], Awaitable[None]] | None = None,
        on_cancel_requested: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Runs BuyerGraph from a fresh goal (S13: real tool-calling — the LLM
        may search the catalog itself, more than once, before answering or
        asking the buyer a follow-up question).

        Returns one of three shapes:
        - {"awaiting_reply": True, "question": ..., "cart": ..., "messages": [...],
          "trace_id": ...} when the LLM wants the buyer's input before
          proceeding — either it couldn't resolve the goal (cart is empty:
          a clarifying question/offer) or it built a cart and is waiting for
          explicit confirmation before checkout is ever submitted (S16,
          R0.9: cart is non-empty). Either way no cart is submitted yet; the
          caller (BuyerBot) persists a ShoppingSession and sends `question`,
          then resumes via continue_shop() on the buyer's next message.
        - {"allowed": False, "reason_code": "buyer.no_cart", ...} when the LLM
          answered with an empty selection (nothing fit the goal at all).
        - Whatever _submit_cart_single_merchant() returns, once the buyer has confirmed a
          cart — identical shape/behavior to before this method existed."""
        await DiscordNotifier(self.config).buyer_trace(trace_id, "shop_start", {"goal": goal})

        plan_result = await self._graph.plan(
            goal,
            policy_id,
            trace_id,
            on_search,
            on_menu_ready,
            on_cart_shown,
            on_order_status,
            on_cancel_requested,
        )
        if plan_result.get("awaiting_reply"):
            pending_cart = plan_result.get("cart") or []
            if pending_cart and on_cart_ready is not None:
                await on_cart_ready(pending_cart)
            return {
                "awaiting_reply": True,
                "question": plan_result["question"],
                "cart": pending_cart,
                "messages": plan_result["messages"],
                "trace_id": trace_id,
            }

        cart = plan_result.get("cart", [])
        if not cart:
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": trace_id}

        return await self._submit_cart(
            cart,
            policy_id,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_negotiation_round,
            goal,
        )

    async def continue_shop(
        self,
        messages: list[dict[str, str]],
        reply_text: str,
        policy_id: str,
        trace_id: str,
        chat_platform: str | None = None,
        chat_user_id: str | None = None,
        chat_channel_id: str | None = None,
        on_negotiation_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None,
        on_cart_ready: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_search: Callable[[str], Awaitable[None]] | None = None,
        on_menu_ready: Callable[[str | None], Awaitable[None]] | None = None,
        on_cart_shown: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_order_status: Callable[[], Awaitable[None]] | None = None,
        on_cancel_requested: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Resumes a parked conversation (a prior start_shop/continue_shop
        call that returned awaiting_reply) with the buyer's next message.
        Same three-shape return contract as start_shop.

        S16 fast path: if the previous pause was a cart-confirmation (not a
        plain "ask") and this reply is a plain "yes", skip the LLM entirely
        and submit the already-built cart directly — zero extra LLM cost for
        the common case. Anything else (including a plain "ask"'s reply)
        re-enters the tool-calling loop as before — a decline/revision is
        just another turn in the SAME general loop (search/ask/answer all
        still available), not a narrower "edit this cart" mode, so the buyer
        can keep shopping (add items, ask questions, swap things) for as
        many turns as MAX_CONVERSATION_TURNS allows.

        Safety net: if that LLM round-trip comes back with a NEW
        confirmation pause whose cart is identical (same skus/qtys) to the
        one already pending, the buyer's confirming wording just wasn't
        recognized — there was nothing to revise. Submit it directly rather
        than asking again, so an unrecognized "yes" phrasing costs one extra
        LLM call, not an infinite confirm-loop (bounded only by
        MAX_CONVERSATION_TURNS otherwise — this is a real bug that shipped
        once already: "yes, go ahead." fell through to the LLM, which
        re-answered with the same cart, forever)."""
        request_text = messages[0]["content"] if messages else reply_text
        previous_pending_cart = pending_cart_from_messages(messages)
        if previous_pending_cart is not None and is_affirmative_reply(reply_text):
            return await self._submit_cart(
                previous_pending_cart,
                policy_id,
                trace_id,
                chat_platform,
                chat_user_id,
                chat_channel_id,
                on_negotiation_round,
                request_text,
            )

        updated_messages = [*messages, {"role": "user", "content": reply_text}]
        result = await self._graph.converse(
            updated_messages,
            policy_id,
            trace_id,
            on_search,
            on_menu_ready,
            on_cart_shown,
            on_order_status,
            on_cancel_requested,
        )

        if result.get("awaiting_reply"):
            new_pending_cart = result.get("cart") or []
            if (
                new_pending_cart
                and previous_pending_cart is not None
                and cart_signature(new_pending_cart) == cart_signature(previous_pending_cart)
            ):
                return await self._submit_cart(
                    new_pending_cart,
                    policy_id,
                    trace_id,
                    chat_platform,
                    chat_user_id,
                    chat_channel_id,
                    on_negotiation_round,
                    request_text,
                )
            if new_pending_cart and on_cart_ready is not None:
                await on_cart_ready(new_pending_cart)
            return {
                "awaiting_reply": True,
                "question": result["question"],
                "cart": new_pending_cart,
                "messages": result["messages"],
                "trace_id": trace_id,
            }

        cart = result.get("cart", [])
        if not cart:
            return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": trace_id}

        return await self._submit_cart(
            cart,
            policy_id,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_negotiation_round,
            request_text,
        )

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
        """Thin backward-compatible wrapper over start_shop, for callers that
        only care about the single-turn case (a cart is either produced or
        it isn't — no multi-turn handling). BuyerBot uses start_shop/
        continue_shop directly so it can persist a ShoppingSession on
        awaiting_reply; direct BuyerAgent callers that don't need that keep
        working exactly as before."""
        return await self.start_shop(
            goal,
            policy_id,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_negotiation_round,
        )

    async def _submit_cart(
        self,
        cart: list[dict[str, Any]],
        policy_id: str,
        trace_id: str,
        chat_platform: str | None,
        chat_user_id: str | None,
        chat_channel_id: str | None,
        on_negotiation_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None,
        request_text: str | None,
    ) -> dict[str, Any]:
        """Dispatch point (S12 step 7): federated when self.mcp fans out
        across merchants, single-merchant otherwise. Duck-typed on the
        presence of client_for rather than isinstance(self.mcp,
        FederatingMCPClient) — avoids importing mcp_client.py here (a cycle:
        mcp_client already imports from buyer_config, not this module, but
        matching this codebase's established duck-typing style over
        isinstance+import is the house convention) and keeps this agent
        working with any client shaped either way, real or fake.

        A single-merchant cart routed through the federated path still
        produces exactly one per_merchant entry — that's
        _submit_federated_cart's own guarantee, not something this dispatch
        has to special-case."""
        if hasattr(self.mcp, "client_for"):
            if chat_platform is None or chat_user_id is None:
                # Federated checkout resolves each merchant's policy against
                # the buyer's identity (chat_platform:chat_user_id) — there
                # is no anonymous federated purchase path. Fail loud (R0.5)
                # rather than let this surface as a confusing downstream
                # KeyError/None-vs-str mismatch.
                raise ValueError("federated checkout requires chat_platform and chat_user_id")

            async def _adapted_round(mid: str, round_num: int, negotiation: dict[str, Any]) -> None:
                if on_negotiation_round is not None:
                    await on_negotiation_round(round_num, negotiation)

            return await self._submit_federated_cart(
                cart,
                trace_id,
                chat_platform,
                chat_user_id,
                chat_channel_id,
                _adapted_round,
                request_text,
            )
        return await self._submit_cart_single_merchant(
            cart,
            policy_id,
            trace_id,
            chat_platform,
            chat_user_id,
            chat_channel_id,
            on_negotiation_round,
            request_text,
        )

    async def _submit_cart_single_merchant(
        self,
        cart: list[dict[str, Any]],
        policy_id: str,
        trace_id: str,
        chat_platform: str | None,
        chat_user_id: str | None,
        chat_channel_id: str | None,
        on_negotiation_round: Callable[[int, dict[str, Any]], Awaitable[None]] | None,
        request_text: str | None,
    ) -> dict[str, Any]:
        """create_cart → negotiate (up to MAX_NEGOTIATION_ROUNDS) → checkout_initiate,
        given a cart the buyer has ALREADY confirmed (S16: the cart-preview
        embed and "reply yes" gate both happen before this is ever called —
        see start_shop/continue_shop's awaiting_reply branch and the S16
        fast-path in continue_shop).

        Returns the payment link (short_url) and cancel_token, with the
        terminal checkout state, or an error carrying a reason_code.

        On a DENY whose reason_code is negotiable (S11 Phase 4, plan item
        #23-24), invokes MerchantAgent.negotiate() and applies the returned
        cart_delta via apply_cart_delta(), retrying create_cart with the
        mutated cart — up to MAX_NEGOTIATION_ROUNDS times (R0.5: never loop
        silently). on_negotiation_round, when given, is awaited once per
        round with (round_number, negotiation_message_dict) so a caller (e.g.
        BuyerBot._handle_shop) can stream the counter-offers into chat as
        they happen. A final, unresolved denial carries the negotiated
        `cart` and `policy_hash` so a caller can offer an amendment."""
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
        await DiscordNotifier(self.config).buyer_trace(trace_id, "cart_compiled", cart_result)
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
            await DiscordNotifier(self.config).merchant_trace(
                trace_id, f"negotiate_round_{round_num}", negotiation
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
            await DiscordNotifier(self.config).buyer_trace(trace_id, "cart_compiled", cart_result)
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
                "request_text": request_text if chat_user_id else None,
            },
        )
        await DiscordNotifier(self.config).buyer_trace(trace_id, "checkout_initiated", result)
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
            "cart": cart,
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

    async def _resolve_remote_policy(self, merchant_id: str, user_id: str) -> dict[str, Any]:
        """Remote sibling of _load_policy_fields (S12): resolves a buyer's
        active policy at a remote merchant via the resolve_policy MCP tool,
        returning the SAME field shape (allowed_tags/tag_mode/blocked_skus/
        max_spend_per_tx_minor/policy_hash, plus policy_id since federated
        carts have no policy_id handed down by the caller) so
        apply_cart_delta needs no change, plus exposure_minor (S23: the
        merchant-computed spend exposure, advisory planning input for the
        consolidated budget guardrail — never enforcement).

        Returns {"policy_fields": {...}, "exposure_minor": int} on success,
        {"unsigned": True} when this buyer has no active policy at that
        merchant yet (authority.policy_unsigned — the caller triggers
        enrollment), or {"error": reason_code} for any other MCP failure."""
        client = self.mcp.client_for(merchant_id)
        result = await client.call("resolve_policy", {"user_id": user_id}, require_auth=True)
        if result.get("success"):
            data = result.get("data", {})
            return {
                "policy_fields": {
                    "policy_id": data.get("policy_id"),
                    "allowed_tags": data.get("allowed_tags"),
                    "tag_mode": data.get("tag_mode"),
                    "blocked_skus": data.get("blocked_skus"),
                    "max_spend_per_tx_minor": data.get("max_spend_per_tx_minor"),
                    "policy_hash": data.get("policy_hash"),
                },
                "exposure_minor": data.get("exposure_minor"),
            }
        reason_code = result.get("error", {}).get("reason_code")
        if reason_code == "authority.policy_unsigned":
            return {"unsigned": True}
        return {"error": reason_code or "buyer.policy_resolve_failed"}

    async def _create_remote_policy_handoff(
        self,
        merchant_id: str,
        chat_platform: str,
        chat_user_id: str,
        chat_channel_id: str,
        request_text: str,
    ) -> str:
        """Bootstraps a signing link at a merchant this buyer has no active
        policy with yet (S12 enrollment), via the create_policy_handoff MCP
        tool — the remote sibling of core.handoff.create_handoff, which
        BuyerBot calls directly in the local single-merchant path. Fails
        loud (R0.5): a merchant that can't even issue a handoff token is a
        broken origin, not a silently-skipped store.

        S12 step 8: also hands the merchant self._resume_url (if set) so it
        can ping this buyer's own signing-complete endpoint once the handoff
        is consumed, and records the token -> chat-identity mapping so this
        buyer can resolve that ping later without trusting anything it
        claims (see resolve_pending_handoff)."""
        client = self.mcp.client_for(merchant_id)
        arguments: dict[str, Any] = {
            "merchant_id": merchant_id,
            "chat_platform": chat_platform,
            "chat_user_id": chat_user_id,
            "chat_channel_id": chat_channel_id,
            "request_text": request_text,
        }
        if self._resume_url:
            arguments["resume_url"] = self._resume_url
        result = await client.call("create_policy_handoff", arguments, require_auth=True)
        if not result.get("success"):
            raise RuntimeError(
                f"create_policy_handoff failed at merchant {merchant_id!r}: {result.get('error')}"
            )
        token: str = result["data"]["token"]
        if self._resume_url:
            self.record_pending_handoff(
                token,
                merchant_id=merchant_id,
                chat_platform=chat_platform,
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
            )
        return token

    async def _validate_merchant_slice(
        self,
        merchant_id: str,
        items: list[dict[str, Any]],
        policy_fields: dict[str, Any],
        trace_id: str,
        on_negotiation_round: Callable[[str, int, dict[str, Any]], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        """Phase 1 (S12) for ONE merchant of a federated cart: create_cart →
        negotiate, bounded by MAX_NEGOTIATION_ROUNDS — the exact same
        machinery _submit_cart_single_merchant uses (NEGOTIABLE_REASON_CODES,
        MerchantAgent.negotiate, apply_cart_delta) — but stops short of
        checkout_initiate. Phase 2 (checkout_initiate) only ever runs once
        every merchant in the cart has validated; see _submit_federated_cart.

        Returns {"allowed": True, "checkout_id": ..., "cart": ..., ...} or
        {"allowed": False, "reason_code": ..., ...}. Never raises for an
        ordinary compiler denial — only a caller-side network fault would
        propagate."""
        client = self.mcp.client_for(merchant_id)
        policy_id = policy_fields.get("policy_id", "")
        cart = items
        cart_hash = compute_cart_hash(cart)
        cart_result = await client.call(
            "create_cart",
            {
                "merchant_id": merchant_id,
                "items": cart,
                "policy_id": policy_id,
                "cart_hash": cart_hash,
                "cart_version": 1,
            },
            require_auth=True,
        )
        await DiscordNotifier(self.config).buyer_trace(
            trace_id, "cart_compiled", {"merchant_id": merchant_id, **cart_result}
        )
        if not cart_result.get("success"):
            return {
                "allowed": False,
                "reason_code": cart_result.get("error", {}).get("reason_code", "buyer.cart_failed"),
            }

        cart_data = cart_result.get("data", {})
        negotiation_rounds: list[dict[str, Any]] = []
        round_num = 0
        while not cart_data.get("allowed") and round_num < MAX_NEGOTIATION_ROUNDS:
            reason_code = cart_data.get("reason_code")
            if reason_code not in NEGOTIABLE_REASON_CODES:
                break

            round_num += 1
            negotiation = MerchantAgent(self.config).negotiate(
                cart, reason_code, trace_id, policy_fields
            )
            negotiation_rounds.append(
                {"round": round_num, "reason_code": reason_code, **negotiation}
            )
            await DiscordNotifier(self.config).merchant_trace(
                trace_id,
                f"negotiate_round_{round_num}",
                {"merchant_id": merchant_id, **negotiation},
            )
            if on_negotiation_round is not None:
                await on_negotiation_round(merchant_id, round_num, negotiation)

            if negotiation.get("state") != "COUNTERED":
                break

            new_cart = apply_cart_delta(cart, negotiation.get("cart_delta", {}), policy_fields)
            if not new_cart or new_cart == cart:
                # No progress possible — stop rather than loop silently (R0.5).
                break
            cart = new_cart
            cart_hash = compute_cart_hash(cart)

            cart_result = await client.call(
                "create_cart",
                {
                    "merchant_id": merchant_id,
                    "items": cart,
                    "policy_id": policy_id,
                    "cart_hash": cart_hash,
                    "cart_version": 1,
                },
                require_auth=True,
            )
            await DiscordNotifier(self.config).buyer_trace(
                trace_id, "cart_compiled", {"merchant_id": merchant_id, **cart_result}
            )
            if not cart_result.get("success"):
                return {
                    "allowed": False,
                    "reason_code": cart_result.get("error", {}).get(
                        "reason_code", "buyer.cart_failed"
                    ),
                    "negotiation_rounds": negotiation_rounds,
                }
            cart_data = cart_result.get("data", {})

        if not cart_data.get("allowed"):
            return {
                "allowed": False,
                "reason_code": cart_data.get("reason_code", "buyer.cart_denied"),
                "transcript": cart_data.get("transcript"),
                "cart": cart,
                "policy_hash": policy_fields.get("policy_hash"),
                "negotiation_rounds": negotiation_rounds,
            }

        checkout_id = cart_data.get("checkout_id")
        if not checkout_id:
            return {"allowed": False, "reason_code": "buyer.no_checkout_id"}

        return {
            "allowed": True,
            "checkout_id": checkout_id,
            "cart": cart,
            "aal_level": cart_data.get("aal_level"),
            "transcript": cart_data.get("transcript"),
            "effective_amount_minor": cart_data.get("effective_amount_minor"),
        }

    async def _commit_merchant_checkout(
        self,
        merchant_id: str,
        checkout_id: str,
        chat_platform: str | None,
        chat_user_id: str | None,
        chat_channel_id: str | None,
        request_text: str | None,
        trace_id: str,
    ) -> dict[str, Any]:
        """Phase 2 (S12) for ONE merchant: checkout_initiate on an
        already-validated cart. Only ever called once every merchant in the
        federated cart has cleared Phase 1 (_validate_merchant_slice)."""
        client = self.mcp.client_for(merchant_id)
        result = await client.call(
            "checkout_initiate",
            {
                "checkout_id": checkout_id,
                "chat_platform": chat_platform,
                "chat_user_id": chat_user_id,
                "chat_channel_id": chat_channel_id,
                "request_text": request_text if chat_user_id else None,
            },
            require_auth=True,
        )
        await DiscordNotifier(self.config).buyer_trace(
            trace_id, "checkout_initiated", {"merchant_id": merchant_id, **result}
        )
        if not result.get("success"):
            return {
                "allowed": False,
                "reason_code": result.get("error", {}).get(
                    "reason_code", "buyer.checkout_initiate_failed"
                ),
            }

        data = result.get("data", {})
        return {
            "allowed": True,
            "checkout_id": data.get("checkout_id"),
            "state": data.get("state"),
            "amount_minor": data.get("amount_minor"),
            "currency": data.get("currency"),
            "short_url": data.get("short_url"),
            "cancel_token": data.get("cancel_token"),
            "expires_at": data.get("expires_at"),
        }

    async def _check_consolidated_budget(
        self,
        by_merchant: dict[str, list[dict[str, Any]]],
        per_merchant: dict[str, dict[str, Any]],
        user_id: str,
        trace_id: str,
    ) -> dict[str, Any] | None:
        """S23 (Q-043): consolidated budget guardrail — planning-time only.

        Sums, across every merchant in this cart, the merchant-reported
        spend exposure (fresh resolve_policy round, computed server-side by
        compute_policy_exposure) plus this cart's pending total (the merchant
        compiler's own effective_amount_minor from Phase 1 — zero buyer-side
        arithmetic, R0.8), and blocks the whole commit when the projection
        exceeds the operator-declared federation_total_cap_minor.

        Returns None when the projection fits or the knob is unset.
        Otherwise a buyer.* rejection shaped like the other
        _submit_federated_cart failures. Malformed merchant data blocks with
        buyer.exposure_unavailable (R0.5: a merchant that can't report a
        number must not silently pass — and assuming 0 would understate
        spend, the dangerous direction). Residuals per DECISION-038:
        read-then-act races and untrusted merchant input bound the guarantee;
        per-merchant hard caps hold regardless."""
        cap = getattr(self.config, "federation_total_cap_minor", None)
        if cap is None:
            return None
        exposures = await asyncio.gather(
            *(self._resolve_remote_policy(mid, user_id) for mid in by_merchant),
            return_exceptions=True,
        )
        breakdown: dict[str, dict[str, int]] = {}
        for mid, outcome in zip(by_merchant.keys(), exposures, strict=True):
            if (
                isinstance(outcome, BaseException)
                or not isinstance(outcome, dict)
                or outcome.get("error")
                or outcome.get("unsigned")
            ):
                return {
                    "allowed": False,
                    "reason_code": "buyer.exposure_unavailable",
                    "trace_id": trace_id,
                    "merchant_id": mid,
                }
            exposure = outcome.get("exposure_minor")
            pending = per_merchant[mid].get("effective_amount_minor")
            if (
                isinstance(exposure, bool)
                or not isinstance(exposure, int)
                or exposure < 0
                or isinstance(pending, bool)
                or not isinstance(pending, int)
                or pending < 0
            ):
                return {
                    "allowed": False,
                    "reason_code": "buyer.exposure_unavailable",
                    "trace_id": trace_id,
                    "merchant_id": mid,
                }
            breakdown[mid] = {
                "exposure_minor": exposure,
                "pending_minor": pending,
                "total_minor": exposure + pending,
            }
        projected = sum(v["total_minor"] for v in breakdown.values())
        if projected > cap:
            return {
                "allowed": False,
                "reason_code": "buyer.budget_exceeded",
                "trace_id": trace_id,
                "declared_total_minor": cap,
                "projected_total_minor": projected,
                "per_merchant": breakdown,
            }
        return None

    async def _submit_federated_cart(
        self,
        cart: list[dict[str, Any]],
        trace_id: str,
        chat_platform: str,
        chat_user_id: str,
        chat_channel_id: str | None,
        on_negotiation_round: Callable[[str, int, dict[str, Any]], Awaitable[None]] | None,
        request_text: str | None,
    ) -> dict[str, Any]:
        """Two-phase checkout across multiple merchants (S12), given a cart
        the buyer has already confirmed — same precondition as
        _submit_cart_single_merchant, one level up (the multi-merchant
        cart-preview embed and confirmation gate happen before this is ever
        called).

        Phase 1 validates EVERY merchant in the cart in parallel
        (_validate_merchant_slice: create_cart + negotiate, no
        checkout_initiate). Phase 2 (checkout_initiate, in parallel) runs
        ONLY if every merchant came back allowed — never issue a payment
        link at merchant A while merchant B might still deny (R0.9 extended
        to federation). There is no cross-DB transaction: a genuine PSP
        failure at one merchant during Phase 2 is reported per-merchant,
        never rolled back at the others (that guarantee would be a false
        claim).

        Enrollment: a merchant this buyer has never signed a policy with
        surfaces a signing link (via create_policy_handoff) instead of ever
        calling create_cart there. A cart spanning two unenrolled merchants
        surfaces both links in the same awaiting_reply-shaped pause — this
        only ever happens once per merchant, ever.

        A single-merchant cart takes the exact same path and produces
        exactly one per_merchant entry."""
        by_merchant: dict[str, list[dict[str, Any]]] = {}
        for item in cart:
            by_merchant.setdefault(item["merchant_id"], []).append(item)

        user_id = f"{chat_platform}:{chat_user_id}"
        resolved_channel_id = chat_channel_id or chat_user_id

        policy_fields_by_merchant: dict[str, dict[str, Any]] = {}
        signing_links: dict[str, str] = {}
        enrollment_entries: list[dict[str, str]] = []
        for mid in by_merchant:
            resolved = await self._resolve_remote_policy(mid, user_id)
            if resolved.get("unsigned"):
                token = await self._create_remote_policy_handoff(
                    mid,
                    chat_platform,
                    chat_user_id,
                    resolved_channel_id,
                    request_text or "",
                )
                # The link must point at THIS merchant's own studio: the
                # handoff token was minted by that merchant's DB, so only
                # its /intent/studio can consume it. Never derive it from
                # buyer config — a buyer process (BuyerSettings) has no
                # studio, no public_base_url, and no webauthn origin at all.
                origin = self.mcp.client_for(mid).merchant
                sign_url = _federated_signing_link(origin.base_url, token)
                signing_links[mid] = sign_url
                enrollment_entries.append(
                    {"merchant_id": mid, "merchant_name": origin.name, "sign_url": sign_url}
                )
                continue
            if resolved.get("error"):
                return {
                    "allowed": False,
                    "reason_code": resolved["error"],
                    "trace_id": trace_id,
                }
            policy_fields_by_merchant[mid] = resolved["policy_fields"]

        if signing_links:
            question = _federation_enrollment_question(signing_links)
            # S12 UX polish: fold N raw per-merchant links into one hosted
            # aggregator page — but only when this exact agent instance
            # actually serves the internal FastAPI app that page lives on
            # (self._resume_url, the same flag that gates recording a
            # pending handoff above). Deriving the base from _resume_url
            # rather than re-querying config keeps this in lockstep with
            # what buyer_cli.py actually wired up, not what the config file
            # merely allows. Purely cosmetic either way — the aggregator
            # page still sends the buyer to each merchant's own
            # /intent/studio origin to sign (no signing hub, DECISION-022);
            # this never changes what gets verified.
            base_url = (
                self._resume_url.removesuffix("/internal/signing-complete")
                if self._resume_url
                else None
            )
            if base_url:
                group_id = self.create_enrollment_group(user_id, enrollment_entries)
                question = (
                    f"I'll need signed spending policies at {len(enrollment_entries)} "
                    f"store(s) before I can shop there — sign here: "
                    f"{base_url}/enroll/{group_id}"
                )
            # Preserve the cart as the same tool_result marker _confirm_cart
            # uses (buyer_graph.py) — not an empty list. This pause used to
            # discard the buyer's already-confirmed cart entirely: once
            # signing completed there was nothing left to resume, so the
            # buyer's next reply ("continue") reached the LLM with zero
            # context and had to be re-asked from scratch.
            cart_ready_message = {
                "role": "user",
                "content": json.dumps({"tool_result": "cart_ready", "cart": cart}),
            }
            return {
                "awaiting_reply": True,
                "question": question,
                "cart": cart,
                "messages": [cart_ready_message],
                "trace_id": trace_id,
            }

        # Phase 1 — validate every merchant in parallel. No checkout_initiate.
        slice_results = await asyncio.gather(
            *(
                self._validate_merchant_slice(
                    mid, items, policy_fields_by_merchant[mid], trace_id, on_negotiation_round
                )
                for mid, items in by_merchant.items()
            )
        )
        per_merchant: dict[str, dict[str, Any]] = dict(
            zip(by_merchant.keys(), slice_results, strict=True)
        )

        if not all(slice_result.get("allowed") for slice_result in per_merchant.values()):
            return {
                "allowed": False,
                "reason_code": "buyer.federation_partial_failure",
                "trace_id": trace_id,
                "per_merchant": per_merchant,
            }

        # S23 (Q-043): consolidated budget guardrail — fresh exposures plus
        # merchant-computed pendings against one declared total. Breach (or
        # malformed merchant data) blocks the whole commit; Phase 2 runs only
        # on an explicit fit.
        budget_block = await self._check_consolidated_budget(
            by_merchant, per_merchant, user_id, trace_id
        )
        if budget_block is not None:
            return budget_block

        # Phase 2 — commit every merchant in parallel. Reached only because
        # every slice above validated.
        checkout_results = await asyncio.gather(
            *(
                self._commit_merchant_checkout(
                    mid,
                    per_merchant[mid]["checkout_id"],
                    chat_platform,
                    chat_user_id,
                    resolved_channel_id,
                    request_text,
                    trace_id,
                )
                for mid in by_merchant
            )
        )

        final_per_merchant: dict[str, dict[str, Any]] = {}
        all_committed = True
        grand_total_minor = 0
        for mid, checkout_result in zip(by_merchant.keys(), checkout_results, strict=True):
            final_per_merchant[mid] = checkout_result
            if checkout_result.get("allowed"):
                grand_total_minor += checkout_result.get("amount_minor") or 0
            else:
                all_committed = False

        if not all_committed:
            return {
                "allowed": False,
                "reason_code": "buyer.federation_partial_failure",
                "trace_id": trace_id,
                "per_merchant": final_per_merchant,
            }

        return {
            "allowed": True,
            "trace_id": trace_id,
            "grand_total_minor": grand_total_minor,
            "per_merchant": final_per_merchant,
        }

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
        await DiscordNotifier(self.config).buyer_trace(
            trace_id, "hold_monitoring_start", {"checkout_id": checkout_id}
        )
        result = await self.mcp.get_order(checkout_id)
        return cast("dict[str, Any]", result)


def compute_cart_hash(cart: list[dict[str, Any]]) -> str:
    """Deterministic cart hash (server-side recomputation, R0.8)."""
    sorted_cart = sorted(cart, key=lambda x: (x.get("merchant_id", ""), x.get("sku", "")))
    serialized = json.dumps(sorted_cart, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


# Buyer-facing wording for the reason codes a denial can carry. Deliberately
# plain-language — the reason_code/transcript/trace_id go to the buyer-trace
# Discord channel instead (BuyerAgent.shop()'s await DiscordNotifier(...)
# .buyer_trace(...) calls), never into the buyer's own DM/channel.
_FRIENDLY_DENIAL_REASONS: dict[str, str] = {
    "policy.tag_violation": "one or more items didn't match the categories your policy allows",
    "policy.tag_violation_all": "one or more items didn't match the categories your policy allows",
    "policy.tag_violation_any": "none of the items matched a category your policy allows",
    "policy.sku_blocked": "your cart included an item your policy blocks",
    "policy.sku_duplicate": "that item is already in a pending order",
    "policy.spend_per_tx_exceeded": "this order goes over your per-purchase spending limit",
    "policy.spend_cumulative_exceeded": "this would put you over your total spending limit",
    "policy.spend_envelope_exceeded": "this would put you over your spending limit",
    "policy.aggregate_cap_exceeded": "this would put you over your spending limit",
    "policy.tx_count_exceeded": "you've hit the number of orders your policy allows",
    "policy.qty_invalid": "the quantity requested isn't valid for one of the items",
    "policy.no_human_authority": "I don't have a signed policy authorizing purchases for you yet",
    "policy.policy_expired": "your spending policy has expired",
    "policy.policy_not_yet_valid": "your spending policy isn't active yet",
    "policy.policy_version_unsupported": "your spending policy needs to be re-signed",
    "policy.campaign_inactive": "the offer applied to this cart isn't currently active",
    "policy.campaign_outside_window": "the offer applied to this cart isn't running right now",
    "policy.currency_mismatch": "there's a currency mismatch with this merchant",
    "policy.merchant_mismatch": "this cart doesn't match the merchant your policy authorizes",
    "buyer.no_cart": "I couldn't find anything matching what you asked for",
    "buyer.no_search_results": "I couldn't find anything matching what you asked for",
    "buyer.cart_failed": "something went wrong building your cart",
    "buyer.no_checkout_id": "something went wrong starting your checkout",
    "buyer.checkout_initiate_failed": "something went wrong starting your checkout",
    "buyer.cart_denied": "that order didn't fit within your current spending policy",
    "buyer.federation_partial_failure": "part of this order couldn't be placed across every store",
    "buyer.budget_exceeded": "this order goes over your total budget across stores",
    "buyer.exposure_unavailable": "I couldn't verify spending room across every store",
    "assertion_required": "I need a fresh authorization from you for this specific order",
}
_DEFAULT_DENIAL_REASON = "that didn't fit within your current spending policy"


def _friendly_denial_reason(reason_code: Any) -> str:
    return _FRIENDLY_DENIAL_REASONS.get(reason_code, _DEFAULT_DENIAL_REASON)


def render_shop_result(result: dict[str, Any]) -> str:
    """Plain-text rendering of a BuyerAgent.shop() outcome, for contexts that
    only support text (e.g. amendment-approval DMs). Conversational, no
    reason codes/transcript/trace ids — see build_shop_result_embed() for the
    richer Discord embed used by BuyerBot's chat replies; the technical
    detail this used to dump into the buyer's own DM/channel now goes to the
    buyer-trace channel instead (R0.5: still fully observable, just not to
    the buyer)."""
    if result.get("awaiting_reply"):
        return str(result.get("question", ""))
    if not result.get("allowed"):
        return (
            f"I couldn't place that order — {_friendly_denial_reason(result.get('reason_code'))}."
        )

    lines = ["Your order is placed."]
    amount_minor = result.get("amount_minor")
    if amount_minor is not None:
        lines.append(f"Total: ₹{amount_minor / 100:.2f}.")
    if result.get("short_url"):
        lines.append(f"Pay here to confirm it: {result['short_url']}")
    if result.get("expires_at"):
        lines.append(f"This hold expires at {result['expires_at']}.")
    return " ".join(lines)


def build_shop_result_embed(result: dict[str, Any]) -> dict[str, Any]:
    """Plain-dict embed (build_discord_embed()'s expected shape) for a
    BuyerAgent.shop() outcome — the rich, conversational card BuyerBot posts
    in chat. Carries only what a buyer needs to act (amount, pay link, hold
    window on success; a plain-language reason on denial) — checkout_id,
    aal_level, trace_id, and the compiler transcript stay in the buyer-trace
    channel, never here. That is why `!cancel` takes no argument: the id a
    buyer would have to quote is deliberately never shown to them."""
    if result.get("awaiting_reply"):
        return {"title": "One more thing…", "description": str(result.get("question", ""))}
    if not result.get("allowed"):
        return {
            "title": "Couldn't place that order",
            "description": f"This one didn't go through — {_friendly_denial_reason(result.get('reason_code'))}.",
        }

    fields = []
    amount_minor = result.get("amount_minor")
    if amount_minor is not None:
        fields.append({"name": "Total", "value": f"₹{amount_minor / 100:.2f}", "inline": True})
    if result.get("expires_at"):
        fields.append({"name": "Hold expires", "value": str(result["expires_at"]), "inline": True})
    if result.get("short_url"):
        fields.append({"name": "Pay here", "value": result["short_url"], "inline": False})
    return {
        "title": "Order placed",
        "description": "Pay within the hold window to confirm it. Reply `!cancel` to cancel it.",
        "fields": fields,
    }


def _campaign_discount_field(cart: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Show the buyer WHY the total is about to drop.

    The number here is a preview only. The merchant's compiler recomputes the
    discount server-side on the same per-campaign subtotal basis (check 12 then
    core/compiler.py's campaign_subtotals) and its `effective_amount_minor` is
    what is actually charged — R0.8 means the buyer-side agent never gets to
    assert a price.
    """
    by_campaign: dict[str, dict[str, Any]] = {}
    for item in cart:
        campaign_id = item.get("campaign_id")
        if not campaign_id:
            continue
        entry = by_campaign.setdefault(
            campaign_id,
            {
                "title": item.get("campaign_title") or campaign_id,
                "discount_bps": item.get("discount_bps", 0),
                "subtotal_minor": 0,
            },
        )
        entry["subtotal_minor"] += item["qty"] * item["unit_minor"]

    if not by_campaign:
        return None

    lines = []
    for entry in by_campaign.values():
        saving_minor = (entry["subtotal_minor"] * entry["discount_bps"]) // 10000
        lines.append(
            f"{entry['title']} — {entry['discount_bps'] / 100:g}% off (−₹{saving_minor / 100:.2f})"
        )
    return {"name": "Offers applied", "value": "\n".join(lines), "inline": False}


def build_cart_preview_embed(
    cart: list[dict[str, Any]], suggestions: list[dict[str, Any]]
) -> dict[str, Any]:
    """S14: sent once, right before checkout is submitted — the "here's what
    I'm buying" moment that makes the agent's work visible instead of a
    single opaque payment link. Suggestions come from a deterministic catalog
    lookup (surfaces.catalog.suggest_related_items), never fed back into the
    cart automatically — purely informational."""
    lines = [
        f"{item['qty']} × {item.get('name', item['sku'])} — ₹{item['unit_minor'] / 100:.2f}"
        for item in cart
    ]
    total_minor = sum(item["qty"] * item["unit_minor"] for item in cart)
    fields = [{"name": "Total so far", "value": f"₹{total_minor / 100:.2f}", "inline": True}]
    discount_field = _campaign_discount_field(cart)
    if discount_field is not None:
        fields.append(discount_field)
    if suggestions:
        fields.append(
            {
                "name": "You might also like",
                "value": "\n".join(
                    f"{item['name']} — ₹{item['unit_minor'] / 100:.2f}" for item in suggestions
                ),
                "inline": False,
            }
        )
    return {"title": "Building your cart", "description": "\n".join(lines), "fields": fields}


def build_federated_cart_preview_embed(
    cart: list[dict[str, Any]], suggestions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Federated sibling of build_cart_preview_embed (S12): groups cart lines
    by store (merchant_id), one field per store with its own subtotal, plus
    a grand-total field summing every store. Same per-line rendering and
    suggestions field as the single-merchant preview — a single-store cart
    still renders as one store field, identically to today."""
    by_merchant: dict[str, list[dict[str, Any]]] = {}
    for item in cart:
        by_merchant.setdefault(item["merchant_id"], []).append(item)

    fields = []
    grand_total_minor = 0
    for merchant, items in by_merchant.items():
        subtotal_minor = sum(item["qty"] * item["unit_minor"] for item in items)
        grand_total_minor += subtotal_minor
        lines = [
            f"{item['qty']} × {item.get('name', item['sku'])} — ₹{item['unit_minor'] / 100:.2f}"
            for item in items
        ]
        lines.append(f"Subtotal: ₹{subtotal_minor / 100:.2f}")
        fields.append({"name": merchant, "value": "\n".join(lines), "inline": False})

    fields.append(
        {"name": "Grand total", "value": f"₹{grand_total_minor / 100:.2f}", "inline": True}
    )
    discount_field = _campaign_discount_field(cart)
    if discount_field is not None:
        fields.append(discount_field)
    if suggestions:
        fields.append(
            {
                "name": "You might also like",
                "value": "\n".join(
                    f"{item['name']} — ₹{item['unit_minor'] / 100:.2f}" for item in suggestions
                ),
                "inline": False,
            }
        )
    return {
        "title": "Building your cart",
        "description": f"{len(by_merchant)} store" + ("s" if len(by_merchant) != 1 else ""),
        "fields": fields,
    }


def _text_matches(haystack: str, needle: str) -> bool:
    """Case-insensitive substring match used for the `!menu <store>` filter —
    "chai" matches "Chai House" by name, or "chai-house" by merchant_id."""
    return needle.strip().lower() in haystack.lower()


_ORDER_STATUS_TEXT = {
    "CREATED": "just started",
    "HELD": "awaiting payment",
    "PAID": "paid ✓",
    "RELEASED": "released (the payment window expired)",
    "CANCELLED": "cancelled",
    "REFUNDED": "refunded",
    "FAILED": "failed",
}


def _format_order_status(order: dict[str, Any], merchant_name: str | None = None) -> str:
    """check_order_status action (S14): renders one list_orders row. Amount/
    state always come from the tool result, never composed by the LLM."""
    where = f" at {merchant_name}" if merchant_name else ""
    status = _ORDER_STATUS_TEXT.get(order["state"], order["state"])
    return f"Your latest order{where} — ₹{order['amount_minor'] / 100:.2f} — is {status}."


async def _report_cancel_result(message: Any, result: dict[str, Any]) -> None:
    """cancel_order action (S14): shared by both bots once the cancel_order
    MCP call has been made — same wording as the pre-existing in-process
    !cancel path."""
    if not result.get("success"):
        reason = result.get("error", {}).get("reason_code", "")
        if reason == "psp.invalid_state":
            await message.channel.send("That one's already paid, so there's nothing to cancel.")
            return
        await message.channel.send("I couldn't cancel that order — please try again.")
        return
    if result.get("data", {}).get("status") == "REFUND":
        await message.channel.send("That was already paid, so I've refunded it.")
    else:
        await message.channel.send("Cancelled.")


def build_catalog_embed(merchant_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """One store's `!menu` page: every catalog item, name + price + tags.
    Deterministic catalog read (search_products with an empty query, which
    core/catalog.py's filter treats as "match everything") — no LLM call."""
    if not items:
        return {
            "title": f"{merchant_name} menu",
            "description": "Nothing in the catalog right now.",
        }
    lines = []
    for item in items:
        line = f"**{item['name']}** — ₹{item['unit_minor'] / 100:.2f}"
        if item.get("tags"):
            line += f"  _{', '.join(item['tags'])}_"
        lines.append(line)
    return {"title": f"{merchant_name} menu", "description": "\n".join(lines)}


async def _send_paginated_menu(channel: Any, embeds: list[Any], author_id: int) -> None:
    """One embed per store, paged with Prev/Next buttons — restricted to the
    buyer who asked, auto-disabled after 2 minutes idle. Local import: this
    is the only place in this module that touches discord.py's UI layer, so
    everything else here stays testable with plain duck-typed fakes."""
    import discord

    class _MenuPaginator(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=120)
            self.index = 0

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != author_id:
                await interaction.response.send_message(
                    "That's not your menu — run `!menu` yourself to browse.", ephemeral=True
                )
                return False
            return True

        async def _render(self, interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(embed=embeds[self.index], view=self)

        @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        async def prev(
            self, interaction: discord.Interaction, button: discord.ui.Button[Any]
        ) -> None:
            self.index = (self.index - 1) % len(embeds)
            await self._render(interaction)

        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
        async def next(
            self, interaction: discord.Interaction, button: discord.ui.Button[Any]
        ) -> None:
            self.index = (self.index + 1) % len(embeds)
            await self._render(interaction)

        async def on_timeout(self) -> None:
            for child in self.children:
                child.disabled = True  # type: ignore[attr-defined]

    await channel.send(embed=embeds[0], view=_MenuPaginator())


def build_federated_shop_result_embed(result: dict[str, Any]) -> dict[str, Any]:
    """Federated sibling of build_shop_result_embed (S12): one field per
    store — short_url + expires_at on success, the friendly denial reason
    (_friendly_denial_reason, reused from the single-merchant path) on
    denial. Title reads "N of M orders placed" when partial, so a buyer
    scanning chat can tell at a glance which stores went through."""
    per_merchant: dict[str, dict[str, Any]] = result.get("per_merchant", {})
    total = len(per_merchant)
    succeeded = sum(1 for slice_result in per_merchant.values() if slice_result.get("allowed"))

    fields = []
    if result.get("allowed") and result.get("grand_total_minor") is not None:
        fields.append(
            {
                "name": "Grand total",
                "value": f"₹{result['grand_total_minor'] / 100:.2f}",
                "inline": True,
            }
        )
    for merchant, slice_result in per_merchant.items():
        if slice_result.get("allowed"):
            lines = []
            if slice_result.get("short_url"):
                lines.append(f"Pay here: {slice_result['short_url']}")
            if slice_result.get("expires_at"):
                lines.append(f"Hold expires at {slice_result['expires_at']}.")
            fields.append(
                {"name": merchant, "value": "\n".join(lines) or "Order placed.", "inline": False}
            )
        else:
            fields.append(
                {
                    "name": merchant,
                    "value": (
                        "Couldn't place this one — "
                        f"{_friendly_denial_reason(slice_result.get('reason_code'))}."
                    ),
                    "inline": False,
                }
            )

    if total and succeeded == total:
        title = "Order placed" if total == 1 else f"All {total} orders placed"
    else:
        title = f"{succeeded} of {total} orders placed"

    return {"title": title, "fields": fields}


def build_upsell_nudge_embed(suggestions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """S14 cross-sell follow-up, sent once right after the order-placed embed,
    on success only. Stage 27 (DEF-13): titled "Goes well with" and driven by
    suggest_for_cart (rules + sellability) at the call site — this builder
    stays a pure renderer so the stage-11 pin on its shape holds.
    Returns None (skip sending) when there's nothing to suggest — no
    empty-field spam."""
    if not suggestions:
        return None
    return {
        "title": "Goes well with",
        "description": "\n".join(
            f"{item['name']} — ₹{item['unit_minor'] / 100:.2f}" for item in suggestions
        ),
    }


def build_upgrade_embed(suggestions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Stage 27: the real up-sell embed — UPGRADE suggestions only, each with
    its honest price delta. Never mutates the cart; purely informational, so
    a wrong tap costs nothing (the buyer still checks out the original cart).
    Returns None when no UPGRADE suggestion is present."""
    ups = [s for s in suggestions if s.get("kind") == "UPGRADE"]
    if not ups:
        return None
    lines = []
    for item in ups:
        delta = item.get("price_delta_minor")
        more = f" (+₹{delta / 100:.2f} more)" if isinstance(delta, int) and delta > 0 else ""
        lines.append(f"{item['name']} — ₹{item['unit_minor'] / 100:.2f}{more}")
    return {"title": "Upgrade to", "description": "\n".join(lines)}


def _describe_cart_delta(cart_delta: dict[str, Any]) -> str:
    """Human-readable one-liner for a negotiation round's cart_delta flag
    (S11 Phase 4: "stream the counter-offers into chat as they happen"). The
    raw cart_delta/reason_code go to the merchant-trace channel instead (see
    the await DiscordNotifier(...).merchant_trace(...) call in shop())."""
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


def stamp_order_message_id(config: Settings, checkout_id: str, message_id: str) -> None:
    """Q-032a: record the Discord message carrying the pay embed, best-effort.
    Never raises — stamping must not break the shop reply path (R0.5 fail-loud
    applies to money decisions, not to UX niceties)."""
    try:
        from sqlmodel import select

        from openstore.core.database import get_session
        from openstore.models import Checkout

        session = get_session(config)
        try:
            checkout = session.exec(
                select(Checkout).where(Checkout.id == checkout_id)
            ).first()
            if checkout is not None and not checkout.discord_message_id:
                checkout.discord_message_id = str(message_id)
                session.add(checkout)
                session.commit()
        finally:
            session.close()
    except Exception:
        pass


def _federated_signing_link(merchant_base_url: str, token: str) -> str:
    """S12: enrollment link for one merchant of a federated cart. Takes the
    merchant origin's base_url explicitly — _signing_link above reads the
    origin from merchant Settings, which the buyer process doesn't have
    (BuyerSettings carries origins, not a merchant block)."""
    return f"{merchant_base_url.rstrip('/')}/intent/studio?token={token}"


def _federation_enrollment_question(signing_links: dict[str, str]) -> str:
    """S12: one line per unenrolled merchant in a federated cart, all
    surfaced in the same turn — a cart spanning two unenrolled merchants
    asks for both signatures at once rather than one at a time (this only
    ever happens once per merchant, ever)."""
    lines = [
        f"I'll need a signed spending policy for {mid} before I can shop there — sign here: {link}"
        for mid, link in signing_links.items()
    ]
    return "\n".join(lines)


# Discord bot (S7.2) — DM-first command handling, wired to a client owned and
# started by server.py's lifespan (S11 plan: one discord.Client, shared with
# the notifier via notifier.set_discord_client). BuyerBot never starts its own
# connection so there is only ever one Discord login per merchant process.
_AGENT_FAILURE_MESSAGE = (
    "Something went wrong on my side while working on that — I've flagged it. "
    "Try again in a moment, or tell me what you're after in different words."
)


@asynccontextmanager
async def _report_agent_failures(message: Any, trace_id: str) -> AsyncIterator[None]:
    """Turn an agent-layer failure into something the buyer can see.

    `BuyerPlanError` (malformed plan JSON, hallucinated_sku, bad selection) and
    `LLMError` (provider down, out of quota) propagated out of the message
    handlers with no `except` anywhere: the typing indicator simply stopped and
    the buyer got nothing at all, indistinguishable from the bot ignoring them.
    R0.5's fail-loud applies most at the surface a human is watching.

    The raw error goes to logs and #alerts, never to the buyer — it can carry a
    model transcript.
    """
    try:
        yield
    except (BuyerPlanError, LLMError) as e:
        logger.warning("agent turn failed (trace_id=%s): %s", trace_id, e, exc_info=True)
        sync_alert(
            "agent_turn_failed",
            f"{type(e).__name__}: {e}",
            {"trace_id": trace_id},
        )
        await message.channel.send(_AGENT_FAILURE_MESSAGE)


class BuyerBot:
    def __init__(self, config: Settings, agent: BuyerAgent):
        self.config = config
        self.agent = agent

    def register(self, client: Any) -> None:
        """Attach the buyer message handler to a live, already-constructed
        discord.Client. Guild channels other than the configured
        shopping_channel_id require the "!shop " prefix and the (privileged)
        message_content intent the operator must enable in the developer
        portal. DMs, and the one configured shopping_channel_id if set,
        accept free text with no prefix at all (S14).

        Routing order: 1) "!shop "/"!cancel " prefix always wins anywhere;
        2) bare "shop "/"cancel " prefix in a free-text context (DM or the
        shopping channel); 3) an active ShoppingSession for this identity —
        bare cancel/nevermind/stop aborts it, anything else is the buyer's
        answer to BuyerGraph's pending question; 4) NEW — no session, in a
        free-text context, non-empty content: the whole message is treated
        as a fresh goal. Anything else (a guild channel that isn't DM or the
        shopping channel, no prefix, no session) is silently ignored, same
        as before."""

        # discord.py's Client.event is untyped, so mypy cannot see through it.
        @client.event  # type: ignore[misc]
        async def on_message(message: Any) -> None:
            if message.author == client.user:
                return
            content = (message.content or "").strip()
            lower = content.lower()
            is_dm = message.guild is None
            chat_user_id = str(message.author.id)
            chat_channel_id = str(message.channel.id)
            shopping_channel_id = self.config.discord.shopping_channel_id
            free_text_context = is_dm or (
                shopping_channel_id is not None and message.channel.id == shopping_channel_id
            )

            goal: str | None = None
            if lower.startswith("!shop "):
                goal = content[len("!shop ") :].strip()
            elif free_text_context and lower.startswith("shop "):
                goal = content[len("shop ") :].strip()
            if goal:
                await self._maybe_cancel_pending_session(chat_user_id, chat_channel_id)
                await self._handle_shop(message, goal)
                return

            # A bare "!cancel" targets the caller's most recent open checkout.
            # The embeds deliberately never print a checkout_id (it is not
            # something a human should have to copy), so requiring one as an
            # argument made the documented cancel path unusable from chat.
            checkout_id: str | None = None
            wants_cancel = False
            if lower.startswith("!cancel"):
                wants_cancel = True
                checkout_id = content[len("!cancel") :].strip() or None
            elif free_text_context and lower.startswith("cancel "):
                wants_cancel = True
                checkout_id = content[len("cancel ") :].strip() or None
            if wants_cancel:
                await self._handle_cancel(message, checkout_id)
                return

            # "!menu"/"!cart" always win anywhere, same as !shop/!cancel, and
            # never touch a parked ShoppingSession — a buyer mid-conversation
            # can check the menu or their cart without losing their place.
            if lower.startswith("!menu"):
                await self._handle_menu(message, content[len("!menu") :].strip() or None)
                return
            if lower.startswith("!cart"):
                await self._handle_cart(message, chat_user_id, chat_channel_id)
                return

            db_session = get_session(self.config)
            expired_goal: str | None = None
            try:
                pending = find_active_session(db_session, "discord", chat_user_id, chat_channel_id)
                if pending is None:
                    # Say so instead of silently re-reading the reply as a brand
                    # new goal, which is what a timed-out thread used to do.
                    stale = take_expired_session(
                        db_session, "discord", chat_user_id, chat_channel_id
                    )
                    if stale is not None:
                        expired_goal = stale.goal
                        db_session.commit()
            finally:
                db_session.close()
            if pending is not None:
                if lower in ("cancel", "nevermind", "never mind", "stop"):
                    await self._handle_cancel_conversation(message, pending.id)
                    return
                await self._handle_conversation_reply(message, pending.id)
                return

            if expired_goal is not None:
                await message.channel.send(
                    f"That thread timed out while I waited (I was still on "
                    f'"{expired_goal}"). Starting fresh with what you just said.'
                )

            if free_text_context and content:
                await self._handle_shop(message, content)

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
                f"I'll need a signed spending policy before I can shop for you — sign here: {link}"
            )
            return
        finally:
            session.close()

        async def _stream_round(round_num: int, negotiation: dict[str, Any]) -> None:
            await message.channel.send(
                f"One sec, {_describe_cart_delta(negotiation.get('cart_delta', {}))} and trying again…"
            )

        async def _show_cart(cart: list[dict[str, Any]]) -> None:
            suggestions = suggest_related_items(self.config, [item["sku"] for item in cart])
            await message.channel.send(
                embed=build_discord_embed(build_cart_preview_embed(cart, suggestions))
            )

        async def _announce_search(query: str) -> None:
            await message.channel.send(f"🔍 Looking for {query}…")

        async def _on_menu_ready(merchant_filter: str | None) -> None:
            await self._handle_menu(message, merchant_filter)

        async def _on_cart_shown(cart: list[dict[str, Any]]) -> None:
            await self._render_cart_embed(message, cart)

        async def _on_order_status() -> None:
            await self._report_order_status(message, "discord", chat_user_id)

        async def _on_cancel_requested() -> None:
            await self._handle_cancel_request(message, "discord", chat_user_id)

        async with _report_agent_failures(message, trace_id), message.channel.typing():
            result = await self.agent.start_shop(
                goal,
                policy_id,
                trace_id,
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                on_negotiation_round=_stream_round,
                on_cart_ready=_show_cart,
                on_search=_announce_search,
                on_menu_ready=_on_menu_ready,
                on_cart_shown=_on_cart_shown,
                on_order_status=_on_order_status,
                on_cancel_requested=_on_cancel_requested,
            )

            if result.get("awaiting_reply"):
                db_session = get_session(self.config)
                try:
                    create_session(
                        db_session,
                        chat_platform="discord",
                        chat_user_id=chat_user_id,
                        chat_channel_id=chat_channel_id,
                        policy_id=policy_id,
                        trace_id=trace_id,
                        goal=goal,
                        messages=result["messages"],
                    )
                    db_session.commit()
                finally:
                    db_session.close()
                await message.channel.send(result["question"])
                return

            _sent = await message.channel.send(embed=build_discord_embed(build_shop_result_embed(result)))
            try:
                if result.get("checkout_id") and getattr(_sent, "id", None) is not None:
                    stamp_order_message_id(self.config, result["checkout_id"], str(_sent.id))
            except Exception:
                pass

            if result.get("allowed"):
                await self._maybe_send_upsell_nudge(message, result)
            else:
                await self._maybe_offer_amendment(
                    message, result, policy_id, chat_user_id, chat_channel_id, goal
                )

    async def _maybe_cancel_pending_session(self, chat_user_id: str, chat_channel_id: str) -> None:
        """A fresh !shop command supersedes any unanswered question — the
        buyer changed their mind or is starting over. Silent no-op if there's
        no pending session."""
        db_session = get_session(self.config)
        try:
            pending = find_active_session(db_session, "discord", chat_user_id, chat_channel_id)
            if pending is not None:
                close_session(db_session, pending, ShoppingSessionState.CANCELLED)
                db_session.commit()
        finally:
            db_session.close()

    async def _handle_cancel_conversation(self, message: Any, session_id: str) -> None:
        """Bare "cancel"/"nevermind"/"stop" while a ShoppingSession is
        AWAITING_REPLY aborts it — distinct from `!cancel <checkout_id>`,
        which cancels a paid/held order and never touches a ShoppingSession."""
        db_session = get_session(self.config)
        try:
            sess = db_session.get(ShoppingSession, session_id)
            if sess is not None and sess.state == ShoppingSessionState.AWAITING_REPLY:
                close_session(db_session, sess, ShoppingSessionState.CANCELLED)
                db_session.commit()
        finally:
            db_session.close()
        await message.channel.send("No problem, order cancelled.")

    async def _handle_conversation_reply(self, message: Any, session_id: str) -> None:
        """Resumes a parked ShoppingSession with the buyer's next message
        (S13). Enforces MAX_CONVERSATION_TURNS — a hard cap outside the LLM's
        reach (R0.5) — before calling back into BuyerGraph via
        BuyerAgent.continue_shop()."""
        chat_user_id = str(message.author.id)
        chat_channel_id = str(message.channel.id)

        db_session = get_session(self.config)
        try:
            sess = db_session.get(ShoppingSession, session_id)
            if sess is None or sess.state != ShoppingSessionState.AWAITING_REPLY:
                return
            # A plain "yes" to an already-built cart is the zero-LLM-cost fast
            # path in continue_shop() below — it finishes the interaction
            # rather than extending it, so it must never be blocked by the
            # turn cap (a confirmation arriving on the Nth turn was killed by
            # this check before continue_shop ever got to interpret it).
            is_free_confirm = pending_cart_from_messages(
                sess.messages
            ) is not None and is_affirmative_reply(message.content)
            turns_used = sess.turns_used if is_free_confirm else sess.turns_used + 1
            if not is_free_confirm and turns_used >= MAX_CONVERSATION_TURNS:
                close_session(db_session, sess, ShoppingSessionState.EXPIRED)
                db_session.commit()
                await message.channel.send(
                    "Let's start over — type `!shop <what you're looking for>` again."
                )
                return
            messages = sess.messages
            policy_id = sess.policy_id
            trace_id = sess.trace_id
        finally:
            db_session.close()

        async def _stream_round(round_num: int, negotiation: dict[str, Any]) -> None:
            await message.channel.send(
                f"One sec, {_describe_cart_delta(negotiation.get('cart_delta', {}))} and trying again…"
            )

        async def _show_cart(cart: list[dict[str, Any]]) -> None:
            suggestions = suggest_related_items(self.config, [item["sku"] for item in cart])
            await message.channel.send(
                embed=build_discord_embed(build_cart_preview_embed(cart, suggestions))
            )

        async def _announce_search(query: str) -> None:
            await message.channel.send(f"🔍 Looking for {query}…")

        async def _on_menu_ready(merchant_filter: str | None) -> None:
            await self._handle_menu(message, merchant_filter)

        async def _on_cart_shown(cart: list[dict[str, Any]]) -> None:
            await self._render_cart_embed(message, cart)

        async def _on_order_status() -> None:
            await self._report_order_status(message, "discord", chat_user_id)

        async def _on_cancel_requested() -> None:
            await self._handle_cancel_request(message, "discord", chat_user_id)

        async with _report_agent_failures(message, trace_id), message.channel.typing():
            result = await self.agent.continue_shop(
                messages,
                message.content,
                policy_id,
                trace_id,
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                on_negotiation_round=_stream_round,
                on_cart_ready=_show_cart,
                on_menu_ready=_on_menu_ready,
                on_cart_shown=_on_cart_shown,
                on_order_status=_on_order_status,
                on_cancel_requested=_on_cancel_requested,
                on_search=_announce_search,
            )

            db_session = get_session(self.config)
            try:
                sess = db_session.get(ShoppingSession, session_id)
                if sess is None:
                    return
                original_goal = sess.goal
                if result.get("awaiting_reply"):
                    advance_session(
                        db_session, sess, new_messages=result["messages"], turns_used=turns_used
                    )
                    db_session.commit()
                    await message.channel.send(result["question"])
                    return
                close_session(db_session, sess, ShoppingSessionState.COMPLETED)
                db_session.commit()
            finally:
                db_session.close()

            _sent = await message.channel.send(embed=build_discord_embed(build_shop_result_embed(result)))
            try:
                if result.get("checkout_id") and getattr(_sent, "id", None) is not None:
                    stamp_order_message_id(self.config, result["checkout_id"], str(_sent.id))
            except Exception:
                pass
            if result.get("allowed"):
                await self._maybe_send_upsell_nudge(message, result)
            else:
                await self._maybe_offer_amendment(
                    message, result, policy_id, chat_user_id, chat_channel_id, original_goal
                )

    async def _maybe_send_upsell_nudge(self, message: Any, result: dict[str, Any]) -> None:
        """S14 cross-sell follow-up + stage-27 up-sell embed, sent after a
        successful order. Suggestions come from suggest_for_cart (ACTIVE rules
        first, sellability-filtered) — never an out-of-stock or hallucinated
        SKU. Silent no-op if there's nothing to suggest."""
        from openstore.core.database import get_session
        from openstore.core.merchandising import suggest_for_cart

        cart = result.get("cart") or []
        db_session = get_session(self.config)
        try:
            suggestions = suggest_for_cart(
                db_session,
                self.config,
                merchant_id(self.config),
                [item["sku"] for item in cart],
            )
        finally:
            db_session.close()
        cross = [s for s in suggestions if s.get("kind") != "UPGRADE"]
        embed = build_upsell_nudge_embed(cross)
        if embed is not None:
            await message.channel.send(embed=build_discord_embed(embed))
        upgrade = build_upgrade_embed(suggestions)
        if upgrade is not None:
            await message.channel.send(embed=build_discord_embed(upgrade))

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
        await DiscordNotifier(self.config).merchant_trace(trace_id, "draft_amendment", draft)

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
            "I can't make that fit your current policy — I've drafted a one-time "
            f"exception for you to approve: {link}"
        )

    @staticmethod
    def _latest_cancellable_checkout(session: Any, chat_user_id: str) -> Checkout | None:
        """Newest checkout of this chat user that is still HELD or PAID — the
        two states cancel_checkout_by_id can act on (it cancels a hold, or
        refunds an already-paid order per INV-8)."""
        latest: Checkout | None = session.exec(
            select(Checkout)
            .where(
                Checkout.chat_user_id == chat_user_id,
                Checkout.state.in_([OrderState.HELD, OrderState.PAID]),  # type: ignore[attr-defined]
            )
            .order_by(Checkout.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        return latest

    async def _handle_cancel(self, message: Any, checkout_id: str | None) -> None:
        """`cancel [<checkout_id>]` (PRD §3.7, plan item #16): the cancel_token
        never reaches the buyer as a raw link — the bot resolves it
        internally and replies in chat. Verifies the checkout belongs to the
        caller before touching it (fail loud, never leak another user's
        checkout — R0.5).

        With no id, targets the caller's most recent cancellable checkout.
        """
        chat_user_id = str(message.author.id)

        session = get_session(self.config)
        try:
            if checkout_id is None:
                checkout = self._latest_cancellable_checkout(session, chat_user_id)
                if checkout is None:
                    await message.channel.send("You have no open orders to cancel.")
                    return
                checkout_id = checkout.id
            else:
                checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
            if checkout is None:
                await message.channel.send(f"I don't see a checkout called {checkout_id}.")
                return
            if checkout.chat_user_id != chat_user_id:
                await message.channel.send("That checkout doesn't belong to you.")
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
                    await message.channel.send(
                        "That one's already paid, so there's nothing to cancel."
                    )
                    return
                raise
            session.commit()
            await DiscordNotifier(self.config).buyer_trace(
                trace_id,
                "checkout_cancelled",
                {"checkout_id": checkout_id, "status": result.get("status")},
            )
            if result.get("status") == "REFUND":
                await message.channel.send("That was already paid, so I've refunded it.")
            else:
                await message.channel.send("Cancelled.")
        finally:
            session.close()

    async def _handle_menu(self, message: Any, filter_text: str | None) -> None:
        """`!menu [store]` — single-merchant path: one store, so a filter
        either matches it or there's nothing else to show."""
        merchant_name = self.config.merchant.name
        if filter_text and not _text_matches(merchant_name, filter_text):
            await message.channel.send(
                f"I don't recognize that store — I only know {merchant_name}."
            )
            return
        result = await self.agent.mcp.search_products("", limit=100)
        items = result.get("data", {}).get("items", []) if result.get("success") else []
        await message.channel.send(
            embed=build_discord_embed(build_catalog_embed(merchant_name, items))
        )

    async def _render_cart_embed(self, message: Any, cart: list[dict[str, Any]]) -> None:
        """Shared by `!cart` and the free-text show_cart action
        (buyer_graph.py) — one rendering path so both surfaces stay
        identical."""
        if not cart:
            await message.channel.send(
                "Nothing's in your cart yet — keep replying to build one, "
                "or `!shop <something>` to start fresh."
            )
            return
        suggestions = suggest_related_items(self.config, [item["sku"] for item in cart])
        await message.channel.send(
            embed=build_discord_embed(build_cart_preview_embed(cart, suggestions))
        )

    async def _handle_cart(self, message: Any, chat_user_id: str, chat_channel_id: str) -> None:
        """`!cart` — shows the cart from the buyer's currently parked
        ShoppingSession, if it's paused at a confirmation (pending_cart_from_
        messages). Read-only: never touches session state."""
        db_session = get_session(self.config)
        try:
            sess = find_active_session(db_session, "discord", chat_user_id, chat_channel_id)
            messages = sess.messages if sess is not None else []
        finally:
            db_session.close()
        await self._render_cart_embed(message, pending_cart_from_messages(messages) or [])

    async def _report_order_status(
        self, message: Any, chat_platform: str, chat_user_id: str
    ) -> None:
        """check_order_status action (S14): single-merchant path — one
        list_orders call, report the most recent."""
        result = await self.agent.mcp.call(
            "list_orders",
            {"chat_platform": chat_platform, "chat_user_id": chat_user_id, "limit": 1},
        )
        orders = result.get("data", {}).get("orders", []) if result.get("success") else []
        if not orders:
            await message.channel.send("I don't see any orders for you yet.")
            return
        await message.channel.send(_format_order_status(orders[0]))

    async def _handle_cancel_request(
        self, message: Any, chat_platform: str, chat_user_id: str
    ) -> None:
        """cancel_order action (S14): single-merchant path — cancels the
        most recent HELD order, same as the existing `!cancel` bang command
        (_latest_cancellable_checkout also just takes the latest; there's
        only one store, so no "which store" ambiguity is possible here)."""
        result = await self.agent.mcp.call(
            "list_orders",
            {"chat_platform": chat_platform, "chat_user_id": chat_user_id, "limit": 5},
        )
        orders = result.get("data", {}).get("orders", []) if result.get("success") else []
        held = [o for o in orders if o["state"] == "HELD"]
        if not held:
            await message.channel.send("You have no open orders to cancel.")
            return
        result = await self.agent.mcp.call(
            "cancel_order",
            {
                "checkout_id": held[0]["checkout_id"],
                "chat_platform": chat_platform,
                "chat_user_id": chat_user_id,
            },
        )
        await _report_cancel_result(message, result)


# S12 step 7: ShoppingSession.policy_id is meaningless per-session once a
# buyer process shops across merchants — a federated cart resolves each
# merchant's policy independently at submit time
# (BuyerAgent._resolve_remote_policy / _submit_federated_cart), not from one
# policy_id handed down by the caller. This sentinel documents that at the DB
# row instead of adding a nullable column/migration for something that was
# never optional before federation existed.
FEDERATED_POLICY_SENTINEL = "federated"


class FederatedBuyerBot(BuyerBot):
    """S12 step 7: buyer bot for the out-of-process, federated buyer agent
    (buyer_cli.py's `openstore-buyer serve`). Inherits BuyerBot's
    register() verbatim — same !shop/!cancel prefixes, same free-text-in-DM-
    or-configured-channel rule, same active-session-reply-wins routing — so
    only what actually differs is overridden here:

    - No local IntentPolicy check before shopping (BuyerBot.require_active_
      policy is a merchant-side concept; a federated cart enrolls each
      merchant's policy on demand instead, surfaced as signing links in the
      awaiting_reply pause from BuyerAgent._submit_federated_cart).
    - Renders with the federated cart-preview/result embeds (one field per
      store) instead of the single-merchant ones.
    - No upsell nudge / policy-amendment offer — both are merchant-scoped
      concepts (suggest_related_items reads one merchant's catalog_path,
      draft_amendment negotiates against one merchant's policy) that don't
      have a federated equivalent yet.
    """

    async def _handle_shop(self, message: Any, goal: str) -> None:
        chat_user_id = str(message.author.id)
        chat_channel_id = str(message.channel.id)
        trace_id = f"trace_{message.id}"

        async def _stream_round(round_num: int, negotiation: dict[str, Any]) -> None:
            await message.channel.send(
                f"One sec, {_describe_cart_delta(negotiation.get('cart_delta', {}))} and trying again…"
            )

        async def _show_cart(cart: list[dict[str, Any]]) -> None:
            await message.channel.send(
                embed=build_discord_embed(build_federated_cart_preview_embed(cart, []))
            )

        async def _announce_search(query: str) -> None:
            await message.channel.send(f"🔍 Looking for {query}…")

        async def _on_menu_ready(merchant_filter: str | None) -> None:
            await self._handle_menu(message, merchant_filter)

        async def _on_cart_shown(cart: list[dict[str, Any]]) -> None:
            await self._render_cart_embed(message, cart)

        async def _on_order_status() -> None:
            await self._report_order_status(message, "discord", chat_user_id)

        async def _on_cancel_requested() -> None:
            await self._handle_cancel_request(message, "discord", chat_user_id)

        async with _report_agent_failures(message, trace_id), message.channel.typing():
            result = await self.agent.start_shop(
                goal,
                FEDERATED_POLICY_SENTINEL,
                trace_id,
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                on_negotiation_round=_stream_round,
                on_cart_ready=_show_cart,
                on_search=_announce_search,
                on_menu_ready=_on_menu_ready,
                on_cart_shown=_on_cart_shown,
                on_order_status=_on_order_status,
                on_cancel_requested=_on_cancel_requested,
            )

            if result.get("awaiting_reply"):
                db_session = get_session(self.config)
                try:
                    create_session(
                        db_session,
                        chat_platform="discord",
                        chat_user_id=chat_user_id,
                        chat_channel_id=chat_channel_id,
                        policy_id=FEDERATED_POLICY_SENTINEL,
                        trace_id=trace_id,
                        goal=goal,
                        messages=result["messages"],
                    )
                    db_session.commit()
                finally:
                    db_session.close()
                await message.channel.send(result["question"])
                return

            _sent = await message.channel.send(
                embed=build_discord_embed(build_federated_shop_result_embed(result))
            )
            try:
                _mid = getattr(_sent, "id", None)
                if _mid is not None:
                    _pm = result.get("per_merchant", {}) or {}
                    for _fmid, _sl in _pm.items():
                        if isinstance(_sl, dict) and _sl.get("checkout_id"):
                            try:
                                _fclient = self.agent.mcp.client_for(_fmid)
                                await _fclient.call(
                                    "set_order_message",
                                    {
                                        "checkout_id": _sl["checkout_id"],
                                        "chat_user_id": chat_user_id,
                                        "discord_message_id": str(_mid),
                                    },
                                    require_auth=True,
                                )
                            except Exception:
                                pass
            except Exception:
                pass

    async def _handle_conversation_reply(self, message: Any, session_id: str) -> None:
        chat_user_id = str(message.author.id)
        chat_channel_id = str(message.channel.id)

        db_session = get_session(self.config)
        try:
            sess = db_session.get(ShoppingSession, session_id)
            if sess is None or sess.state != ShoppingSessionState.AWAITING_REPLY:
                return
            # See BuyerBot._handle_conversation_reply: a free confirmation of
            # an already-built cart must never be blocked by the turn cap.
            is_free_confirm = pending_cart_from_messages(
                sess.messages
            ) is not None and is_affirmative_reply(message.content)
            turns_used = sess.turns_used if is_free_confirm else sess.turns_used + 1
            if not is_free_confirm and turns_used >= MAX_CONVERSATION_TURNS:
                close_session(db_session, sess, ShoppingSessionState.EXPIRED)
                db_session.commit()
                await message.channel.send(
                    "Let's start over — type `!shop <what you're looking for>` again."
                )
                return
            messages = sess.messages
            trace_id = sess.trace_id
        finally:
            db_session.close()

        async def _stream_round(round_num: int, negotiation: dict[str, Any]) -> None:
            await message.channel.send(
                f"One sec, {_describe_cart_delta(negotiation.get('cart_delta', {}))} and trying again…"
            )

        async def _show_cart(cart: list[dict[str, Any]]) -> None:
            await message.channel.send(
                embed=build_discord_embed(build_federated_cart_preview_embed(cart, []))
            )

        async def _announce_search(query: str) -> None:
            await message.channel.send(f"🔍 Looking for {query}…")

        async def _on_menu_ready(merchant_filter: str | None) -> None:
            await self._handle_menu(message, merchant_filter)

        async def _on_cart_shown(cart: list[dict[str, Any]]) -> None:
            await self._render_cart_embed(message, cart)

        async def _on_order_status() -> None:
            await self._report_order_status(message, "discord", chat_user_id)

        async def _on_cancel_requested() -> None:
            await self._handle_cancel_request(message, "discord", chat_user_id)

        async with _report_agent_failures(message, trace_id), message.channel.typing():
            result = await self.agent.continue_shop(
                messages,
                message.content,
                FEDERATED_POLICY_SENTINEL,
                trace_id,
                chat_platform="discord",
                chat_user_id=chat_user_id,
                chat_channel_id=chat_channel_id,
                on_negotiation_round=_stream_round,
                on_cart_ready=_show_cart,
                on_search=_announce_search,
                on_menu_ready=_on_menu_ready,
                on_cart_shown=_on_cart_shown,
                on_order_status=_on_order_status,
                on_cancel_requested=_on_cancel_requested,
            )

            db_session = get_session(self.config)
            try:
                sess = db_session.get(ShoppingSession, session_id)
                if sess is None:
                    return
                if result.get("awaiting_reply"):
                    advance_session(
                        db_session, sess, new_messages=result["messages"], turns_used=turns_used
                    )
                    db_session.commit()
                    await message.channel.send(result["question"])
                    return
                close_session(db_session, sess, ShoppingSessionState.COMPLETED)
                db_session.commit()
            finally:
                db_session.close()

            _sent = await message.channel.send(
                embed=build_discord_embed(build_federated_shop_result_embed(result))
            )
            try:
                _mid = getattr(_sent, "id", None)
                if _mid is not None:
                    _pm = result.get("per_merchant", {}) or {}
                    for _fmid, _sl in _pm.items():
                        if isinstance(_sl, dict) and _sl.get("checkout_id"):
                            try:
                                _fclient = self.agent.mcp.client_for(_fmid)
                                await _fclient.call(
                                    "set_order_message",
                                    {
                                        "checkout_id": _sl["checkout_id"],
                                        "chat_user_id": chat_user_id,
                                        "discord_message_id": str(_mid),
                                    },
                                    require_auth=True,
                                )
                            except Exception:
                                pass
            except Exception:
                pass

    async def _handle_menu(self, message: Any, filter_text: str | None) -> None:
        """`!menu [store]` — bare `!menu` pages through every merchant's
        catalog (one embed per store, Prev/Next buttons); `!menu <text>`
        filters to the one store whose name or merchant_id matches."""
        merchants = self.agent.mcp.merchants()
        selected = merchants
        if filter_text:
            selected = [
                m
                for m in merchants
                if _text_matches(m.name, filter_text) or _text_matches(m.merchant_id, filter_text)
            ]
            if not selected:
                known = ", ".join(m.name for m in merchants)
                await message.channel.send(f"I don't recognize that store — I know: {known}.")
                return

        embeds = []
        for merchant in selected:
            result = await self.agent.mcp.client_for(merchant.merchant_id).search_products(
                "", limit=100
            )
            items = result.get("data", {}).get("items", []) if result.get("success") else []
            embeds.append(build_discord_embed(build_catalog_embed(merchant.name, items)))

        if len(embeds) == 1:
            await message.channel.send(embed=embeds[0])
            return
        await _send_paginated_menu(message.channel, embeds, int(message.author.id))

    async def _render_cart_embed(self, message: Any, cart: list[dict[str, Any]]) -> None:
        """Federated sibling of BuyerBot._render_cart_embed — multi-store
        embed, no suggestions (FederatedBuyerBot has no cross-sell path yet,
        same as its shop-result embed). Shared by `!cart` and show_cart."""
        if not cart:
            await message.channel.send(
                "Nothing's in your cart yet — keep replying to build one, "
                "or `!shop <something>` to start fresh."
            )
            return
        await message.channel.send(
            embed=build_discord_embed(build_federated_cart_preview_embed(cart, []))
        )

    async def _handle_cart(self, message: Any, chat_user_id: str, chat_channel_id: str) -> None:
        db_session = get_session(self.config)
        try:
            sess = find_active_session(db_session, "discord", chat_user_id, chat_channel_id)
            messages = sess.messages if sess is not None else []
        finally:
            db_session.close()
        await self._render_cart_embed(message, pending_cart_from_messages(messages) or [])

    async def _report_order_status(
        self, message: Any, chat_platform: str, chat_user_id: str
    ) -> None:
        """check_order_status action (S14): federated path — fans out
        list_orders across every merchant the buyer can reach (there's no
        local record of which merchant(s) the buyer has actually ordered
        from), reports the single most recent overall."""
        merchants = self.agent.mcp.merchants()

        async def _one(merchant: Any) -> tuple[Any, dict[str, Any]]:
            result = await self.agent.mcp.client_for(merchant.merchant_id).call(
                "list_orders",
                {"chat_platform": chat_platform, "chat_user_id": chat_user_id, "limit": 1},
                require_auth=True,
            )
            return merchant, result

        results = await asyncio.gather(*(_one(m) for m in merchants))
        candidates = [
            (merchant, orders[0])
            for merchant, result in results
            if result.get("success")
            for orders in [result.get("data", {}).get("orders", [])]
            if orders
        ]
        if not candidates:
            await message.channel.send("I don't see any orders for you yet.")
            return
        merchant, order = max(candidates, key=lambda pair: pair[1]["created_at"] or "")
        await message.channel.send(_format_order_status(order, merchant.name))

    async def _handle_cancel_request(
        self, message: Any, chat_platform: str, chat_user_id: str
    ) -> None:
        """cancel_order action (S14): federated path — a HELD order at more
        than one DISTINCT merchant is genuinely ambiguous (unlike the
        single-merchant path, where "latest" is unambiguous even with
        multiple holds at the same store) — ask which store rather than
        guessing which one the buyer means."""
        merchants = self.agent.mcp.merchants()

        async def _one(merchant: Any) -> tuple[Any, list[dict[str, Any]]]:
            result = await self.agent.mcp.client_for(merchant.merchant_id).call(
                "list_orders",
                {"chat_platform": chat_platform, "chat_user_id": chat_user_id, "limit": 5},
                require_auth=True,
            )
            orders = result.get("data", {}).get("orders", []) if result.get("success") else []
            return merchant, [o for o in orders if o["state"] == "HELD"]

        results = await asyncio.gather(*(_one(m) for m in merchants))
        with_held = [(merchant, held) for merchant, held in results if held]

        if not with_held:
            await message.channel.send("You have no open orders to cancel.")
            return
        if len(with_held) > 1:
            names = ", ".join(merchant.name for merchant, _ in with_held)
            await message.channel.send(
                f"You have open orders at more than one store ({names}) — which one do you mean?"
            )
            return
        merchant, held = with_held[0]
        result = await self.agent.mcp.client_for(merchant.merchant_id).call(
            "cancel_order",
            {
                "checkout_id": held[0]["checkout_id"],
                "chat_platform": chat_platform,
                "chat_user_id": chat_user_id,
            },
            require_auth=True,
        )
        await _report_cancel_result(message, result)


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
    hold-release loop.

    S16: shop() can now come back awaiting_reply (a clarifying question OR a
    cart-confirmation pause) instead of a terminal outcome — a ShoppingSession
    is created here too, exactly like BuyerBot._handle_shop does, so the
    buyer's next DM reply resumes this conversation the same way it would
    from the Discord bot."""
    agent = BuyerAgent(config, mcp_client)
    resolved_channel_id = chat_channel_id or chat_user_id
    result = await agent.shop(
        request_text,
        policy_id,
        trace_id,
        chat_platform=chat_platform or "discord",
        chat_user_id=chat_user_id,
        chat_channel_id=resolved_channel_id,
    )
    if result.get("awaiting_reply"):
        db_session = get_session(config)
        try:
            create_session(
                db_session,
                chat_platform=chat_platform or "discord",
                chat_user_id=chat_user_id,
                chat_channel_id=resolved_channel_id,
                policy_id=policy_id,
                trace_id=trace_id,
                goal=request_text,
                messages=result["messages"],
            )
            db_session.commit()
        finally:
            db_session.close()

    await send_dm(
        config, chat_user_id, render_shop_result(result), embed=build_shop_result_embed(result)
    )
    return result
