# OpenStore agents — Merchant agent (S7.3 / §5.2)
# Negotiation, recovery, bundler, narrator.
# R0.9: LLM output is a proposal, never a command.

from __future__ import annotations

import json
import logging
from typing import Any

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

        Returns a counter-cart_delta proposal. Final state is either
        ACCEPTED (cart compiles) or NO_COMPLIANT_PATH.
        """
        sync_merchant_trace(trace_id, "negotiate", {"reason_code": reason_code})

        if reason_code == "policy.tag_violation":
            return {
                "negotiation_id": f"neg_{trace_id[:8]}",
                "round": 1,
                "from": "merchant_agent",
                "state": "COUNTERED",
                "cart_delta": {"remove_violating_tags": True},
                "reason_code": reason_code,
                "trace_id": trace_id,
            }
        elif reason_code == "policy.sku_blocked":
            return {
                "negotiation_id": f"neg_{trace_id[:8]}",
                "round": 1,
                "from": "merchant_agent",
                "state": "COUNTERED",
                "cart_delta": {"swap_sku": True},
                "reason_code": reason_code,
                "trace_id": trace_id,
            }
        elif reason_code == "policy.spend_per_tx_exceeded":
            return {
                "negotiation_id": f"neg_{trace_id[:8]}",
                "round": 1,
                "from": "merchant_agent",
                "state": "COUNTERED",
                "cart_delta": {"reduce_qty": True},
                "reason_code": reason_code,
                "trace_id": trace_id,
            }
        else:
            return {
                "negotiation_id": f"neg_{trace_id[:8]}",
                "round": 1,
                "from": "merchant_agent",
                "state": "NO_COMPLIANT_PATH",
                "cart_delta": {},
                "reason_code": reason_code,
                "trace_id": trace_id,
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
        """
        aal = bundle.get("aal", {}).get("level", 0)
        verdict = bundle.get("adjudication", {}).get("verdict", "UNKNOWN")
        amount = bundle.get("transaction", {}).get("amount_minor", 0)
        merchant = bundle.get("transaction", {}).get("merchant_id", "unknown")
        checkout_id = bundle.get("transaction", {}).get("checkout_id", "unknown")

        notes = [
            f"Cover note for evidence bundle covering checkout {checkout_id}.",
            f"Merchant: {merchant}.",
            f"Transaction amount: ₹{amount / 100:.2f}.",
            f"Compiler verdict: {verdict}.",
            f"AAL level achieved: {aal}.",
        ]

        if aal == 3:
            notes.append(
                "The human's authenticator signed this exact cart with user verification; this is the strongest merchant-side evidence of authorized intent available."
            )
        elif aal == 2:
            notes.append(
                "The human authorized a standing policy with a fresh, user-verified signature, and this cart compiled clean against it; this is evidence of authorized intent, with final allocation resting with the network and issuer."
            )
        elif aal == 1:
            notes.append(
                "Authority was presented but one or more freshness, attestation, or verification predicates failed; treat the transaction as contested."
            )
        elif aal == 0:
            notes.append("No verifiable human authority exists; no order is created at this level.")

        return " ".join(notes)
