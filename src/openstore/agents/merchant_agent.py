# OpenStore agents — Merchant agent (S7.3 / §5.2)
# Negotiation, recovery, bundler, narrator.
# R0.9: LLM output is a proposal, never a command.

from __future__ import annotations

import json
import logging
from typing import Any

from openstore.agents.llm import llm_chat
from openstore.config import Settings
from openstore.notifier import sync_merchant_trace

logger = logging.getLogger("openstore.merchant_agent")

NEGOTIATION_STATES = frozenset(
    {
        "PROPOSED",
        "COUNTERED",
        "ACCEPTED",
        "NO_COMPLIANT_PATH",
        "AMENDMENT_REQUESTED",
    }
)

NEGOTIATE_ACTIONS = frozenset(
    {"remove_violating_tags", "swap_sku", "reduce_qty", "no_compliant_path"}
)

_ACTION_TO_CART_DELTA: dict[str, dict[str, Any]] = {
    "remove_violating_tags": {"remove_violating_tags": True},
    "swap_sku": {"swap_sku": True},
    "reduce_qty": {"reduce_qty": True},
}


class MerchantAgentError(Exception):
    """Raised when the LLM's response violates the closed output contract for a
    merchant-agent reasoning step (invalid JSON, unrecognized action, etc). Distinct
    from llm.LLMError, which means the provider/transport itself failed."""


class NegotiationMessage:
    """S7.5 Negotiation message schema."""

    def __init__(
        self,
        negotiation_id: str,
        round: int,
        from_: str,
        state: str,
        cart_delta: dict[str, Any],
        reason_code: str,
        trace_id: str,
    ):
        if state not in NEGOTIATION_STATES:
            raise ValueError(f"Invalid negotiation state: {state}")
        if from_ not in ("buyer_agent", "merchant_agent"):
            raise ValueError(f"Invalid from: {from_}")
        self.negotiation_id = negotiation_id
        self.round = round
        self.from_ = from_
        self.state = state
        self.cart_delta = cart_delta
        self.reason_code = reason_code
        self.trace_id = trace_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "negotiation_id": self.negotiation_id,
            "round": self.round,
            "from": self.from_,
            "state": self.state,
            "cart_delta": self.cart_delta,
            "reason_code": self.reason_code,
            "trace_id": self.trace_id,
        }


def apply_cart_delta(
    cart: list[dict[str, Any]],
    cart_delta: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """S11 Phase 4 (plan item #24): the missing executor. `negotiate()` above
    returns intent flags (`{"remove_violating_tags": True}` etc.), not a real
    cart — nothing ever applied them. This mechanically mutates `cart`
    against the flag(s) set in `cart_delta`, using only the policy fields the
    flag names, never an invented heuristic (R0.3):

      - remove_violating_tags: drop line items whose tags fail the same
        allowed_tags/tag_mode rule compile_decision's check 8 enforces.
      - swap_sku: the PRD has no SKU-substitution algorithm (no catalog-
        matching spec exists) — implemented as "drop the blocked SKU's line
        item" (Q-021), the same mechanical effect as remove_violating_tags,
        just keyed by policy.blocked_skus instead of tags.
      - reduce_qty: deterministic. Sorts by sku for a stable order, then
        repeatedly removes one unit from the last item (or drops it once its
        qty reaches 1) until the recomputed total is at or under
        max_spend_per_tx_minor.

    Returns a new list; `cart` is never mutated in place. Unknown/absent flags
    are a no-op (returns `cart` unchanged) — `negotiate()`'s three flags are
    the only ones this executor knows how to apply."""
    if cart_delta.get("remove_violating_tags"):
        allowed = set(policy.get("allowed_tags") or [])
        tag_mode = policy.get("tag_mode", "all")

        def _tags_ok(item: dict[str, Any]) -> bool:
            if not allowed:
                return True
            tags = set(item.get("tags", []))
            if tag_mode == "all":
                return tags.issubset(allowed)
            return bool(tags & allowed)

        return [dict(item) for item in cart if _tags_ok(item)]

    if cart_delta.get("swap_sku"):
        blocked = set(policy.get("blocked_skus") or [])
        return [dict(item) for item in cart if item.get("sku") not in blocked]

    if cart_delta.get("reduce_qty"):
        cap = policy.get("max_spend_per_tx_minor", 0)
        new_cart = sorted((dict(item) for item in cart), key=lambda i: i.get("sku", ""))

        def _total(c: list[dict[str, Any]]) -> int:
            return sum(i.get("qty", 0) * i.get("unit_minor", 0) for i in c)

        while new_cart and _total(new_cart) > cap:
            last = new_cart[-1]
            if last.get("qty", 0) > 1:
                last["qty"] -= 1
            else:
                new_cart.pop()
        return new_cart

    return cart


class MerchantAgent:
    """
    S7.3-S7.5: Merchant reasoning agent.
    - Negotiation loop (S7.3)
    - Policy amendment drafting (S7.4)
    - Evidence narrator (S7.5)

    LLM output is a proposal, never a command (R0.9). No payment keys (R0.10).
    """

    def __init__(self, config: Settings):
        self.config = config

    def negotiate(
        self,
        cart: list[dict[str, Any]],
        reason_code: str,
        trace_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        """
        S7.3: On a recoverable DENY, propose a counter-offer.

        Reasons over the actual cart/policy contents (not just reason_code) via an
        LLM call constrained to the closed action space `apply_cart_delta` already
        knows how to execute. LLM proposes which action; Python maps it to the exact
        cart_delta shape and never lets the LLM invent a mutation type (R0.9).

        Returns a counter-cart_delta proposal. Final state is either
        COUNTERED or NO_COMPLIANT_PATH.

        Raises MerchantAgentError if the LLM's response violates the closed output
        contract, or llm.LLMError if the provider call itself fails. Neither is
        caught here — fail loud (R0.5), no fallback to a rule-based branch.
        """
        sync_merchant_trace(trace_id, "negotiate", {"reason_code": reason_code})

        prompt_payload = {
            "reason_code": reason_code,
            "cart": cart,
            "policy": {
                "allowed_tags": policy.get("allowed_tags"),
                "tag_mode": policy.get("tag_mode"),
                "blocked_skus": policy.get("blocked_skus"),
                "max_spend_per_tx_minor": policy.get("max_spend_per_tx_minor"),
            },
        }
        system_prompt = (
            "You are a merchant negotiation agent. Given a denied cart and the "
            "reason it was denied, decide which ONE of exactly three corrective "
            "actions to propose: remove_violating_tags (drop items whose tags fail "
            "the policy), swap_sku (drop blocked SKUs), reduce_qty (reduce "
            "quantities to fit the spend cap). If none of these three actions can "
            "plausibly resolve reason_code, respond with no_compliant_path. "
            'Respond with JSON only: {"action": '
            '"remove_violating_tags"|"swap_sku"|"reduce_qty"|"no_compliant_path", '
            '"rationale": "<one sentence>"}.'
        )
        response = llm_chat(
            self.config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload)},
            ],
        )

        try:
            draft = json.loads(response)
        except json.JSONDecodeError as e:
            raise MerchantAgentError(f"negotiate: invalid_json_response: {e}") from e

        action = draft.get("action") if isinstance(draft, dict) else None
        if action not in NEGOTIATE_ACTIONS:
            raise MerchantAgentError(f"negotiate: invalid_action:{action!r}")

        rationale = draft.get("rationale", "") if isinstance(draft, dict) else ""
        state = "NO_COMPLIANT_PATH" if action == "no_compliant_path" else "COUNTERED"
        cart_delta = dict(_ACTION_TO_CART_DELTA.get(action, {}))

        return {
            "negotiation_id": f"neg_{trace_id[:8]}",
            "round": 1,
            "from": "merchant_agent",
            "state": state,
            "cart_delta": cart_delta,
            "reason_code": reason_code,
            "trace_id": trace_id,
            "rationale": rationale,
        }

    def draft_amendment(
        self,
        base_policy_hash: str,
        reason_code: str,
        cart: list[dict[str, Any]],
        trace_id: str,
    ) -> dict[str, Any]:
        """
        S7.4: Draft a PolicyAmendment (proposal, never a command).
        R0.5: NO self-approval. The human signs the amendment.
        """
        sync_merchant_trace(trace_id, "draft_amendment", {"reason_code": reason_code})

        skus = [item.get("sku") for item in cart if item.get("sku")]
        amount_minor = sum(item.get("unit_minor", 0) * item.get("qty", 0) for item in cart)

        delta = {
            "add_allowed_skus": skus,
            "bump_max_spend_per_tx_minor": amount_minor if amount_minor > 0 else 0,
            "one_time": True,
            "max_amount_minor": amount_minor,
        }

        draft = {
            "amendment_id": f"amend_{trace_id[:16]}",
            "base_policy_hash": base_policy_hash,
            "delta": delta,
            "reason_code_triggered": reason_code,
            "drafted_by": "merchant_agent",
            "draft_digest": "",
            "approval": {
                "approver_credential_id": None,
                "approved_at": None,
                "webauthn_assertion": None,
            },
            "state": "PENDING_APPROVAL",
        }

        import hashlib

        draft_bytes = json.dumps(draft, sort_keys=True, separators=(",", ":")).encode("utf-8")
        draft["draft_digest"] = f"sha256:{hashlib.sha256(draft_bytes).hexdigest()}"

        return draft

    def narrate(
        self,
        bundle: dict[str, Any],
        verifier_output: dict[str, Any] | None = None,
    ) -> str:
        """
        S7.5: Evidence narrator. Writes the human-readable cover note.
        Prose ONLY. The bundle is never modified.

        The fact list below is assembled deterministically from the bundle; an LLM
        call adds ONE further sentence characterizing the strength of the evidence,
        explicitly instructed not to invent facts beyond what it's given. Raises
        on LLM failure — fail loud (R0.5), no fallback to a canned sentence.
        """
        aal = bundle.get("aal", {}).get("level", 0)
        verdict = bundle.get("adjudication", {}).get("verdict", "UNKNOWN")
        amount = bundle.get("transaction", {}).get("amount_minor", 0)
        merchant = bundle.get("transaction", {}).get("merchant_id", "unknown")
        checkout_id = bundle.get("transaction", {}).get("checkout_id", "unknown")

        facts = {
            "checkout_id": checkout_id,
            "merchant": merchant,
            "amount_minor": amount,
            "verdict": verdict,
            "aal_level": aal,
        }
        notes = [
            f"Cover note for evidence bundle covering checkout {checkout_id}.",
            f"Merchant: {merchant}.",
            f"Transaction amount: ₹{amount / 100:.2f}.",
            f"Compiler verdict: {verdict}.",
            f"AAL level achieved: {aal}.",
        ]

        system_prompt = (
            "You are a payments evidence narrator. Given these facts about a "
            "transaction's authorization level, write ONE additional sentence "
            "characterizing the strength of the evidence. Do not invent any fact "
            "not given to you. Respond with plain text, one sentence."
        )
        sentence = llm_chat(
            self.config,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(facts)},
            ],
        ).strip()
        notes.append(sentence)

        return " ".join(notes)
