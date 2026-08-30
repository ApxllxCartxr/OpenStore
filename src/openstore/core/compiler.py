# OpenStore core — Intent Compiler (12 checks, normative order)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openstore.models import IntentPolicy


@dataclass(frozen=True)
class CompilerContext:
    """Input context for compile_decision()."""
    cart_items: list[dict[str, Any]]  # [{"sku": str, "qty": int, "unit_minor": int, "tags": list[str], "campaign_id": Optional[str]}]
    policy: IntentPolicy
    merchant_id: str
    currency: str
    checkout_count: int  # current transaction count for this policy
    cumulative_spend_minor: int  # current cumulative spend for this policy
    has_webauthn_assertion: bool
    assertion_age_seconds: int
    now_unix: int  # current time as Unix seconds
    campaign_lookup: dict[str, dict[str, Any]]  # campaign_id -> campaign dict with state, window


@dataclass(frozen=True)
class CompilerResult:
    """Output of compile_decision()."""
    allowed: bool
    reason_code: str | None
    transcript: list[dict[str, Any]]  # PoAI §3.3.6: {"check": str, "result": "pass"|"fail", "reason_code": Optional[str]}
    aal_level: int
    spend_per_tx_minor: int
    spend_cumulative_minor: int
    effective_amount_minor: int  # after campaign discounts


# Reason codes (closed set from REGISTRY.json, Part 7 — policy.* namespaced)
REASON_CODES = {
    "assertion_required",
    "policy.currency_mismatch",
    "policy.merchant_mismatch",
    "policy.policy_not_yet_valid",
    "policy.policy_expired",
    "policy.tx_count_exceeded",
    "policy.qty_invalid",
    "policy.sku_duplicate",
    "policy.sku_blocked",
    "policy.tag_violation",
    "policy.spend_per_tx_exceeded",
    "policy.spend_envelope_exceeded",
    "policy.spend_cumulative_exceeded",
    "policy.campaign_inactive",
    "policy.campaign_outside_window",
}


def compile_decision(ctx: CompilerContext) -> CompilerResult:
    """
    Intent Compiler — 12 checks in normative order.

    Stops at first failure. Returns CompilerResult with full transcript.

    Per PRD Part 3.2:
    0. human_authority_present (assertion_required)
    1. currency_match
    2. merchant_lock
    3. policy_not_before
    4. policy_expiry
    5. transaction_count
    6. item_qty
    7. item_blocked_sku
    8. item_tag_allowlist
    9. spend_per_tx
    10. spend_envelope (delegated only)
    11. spend_cumulative (root budget)
    12. campaign_validity (v3.0, appended)
    """
    transcript: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, reason_code: str | None = None) -> None:
        """Record check result (PoAI §3.3.6: check name / pass-fail / reason code)."""
        transcript.append({
            "check": name,
            "result": "pass" if passed else "fail",
            "reason_code": reason_code,
        })

    def fail(reason_code: str, details: dict[str, Any] | None = None) -> CompilerResult:
        """Early return on failure."""
        return CompilerResult(
            allowed=False,
            reason_code=reason_code,
            transcript=transcript,
            aal_level=0,
            spend_per_tx_minor=0,
            spend_cumulative_minor=ctx.cumulative_spend_minor,
            effective_amount_minor=0,
        )

    # 0. human_authority_present (Q-004, §3.2)
    # Runs BEFORE check 1. When the policy requires human authority and no valid
    # WebAuthn assertion accompanies the cart, deny with assertion_required —
    # returned, never raised (R0.5). One no_human_authority field does not exist on
    # the policy model; treat authority as required (matches the hardcoded False in
    # the AAL path below).
    if not ctx.has_webauthn_assertion:
        add_check("human_authority_present", False, "assertion_required")
        return fail("assertion_required")
    add_check("human_authority_present", True)

    # 1. currency_match
    if ctx.currency != ctx.policy.currency:
        add_check("currency_match", False, "policy.currency_mismatch")
        return fail("policy.currency_mismatch")
    add_check("currency_match", True)

    # 2. merchant_lock
    if ctx.merchant_id != ctx.policy.merchant_id:
        add_check("merchant_lock", False, "policy.merchant_mismatch")
        return fail("policy.merchant_mismatch")
    add_check("merchant_lock", True)

    # 3. policy_not_before
    if ctx.now_unix < ctx.policy.not_before:
        add_check("policy_not_before", False, "policy.policy_not_yet_valid")
        return fail("policy.policy_not_yet_valid")
    add_check("policy_not_before", True)

    # 4. policy_expiry
    if ctx.now_unix > ctx.policy.expires_at:
        add_check("policy_expiry", False, "policy.policy_expired")
        return fail("policy.policy_expired")
    add_check("policy_expiry", True)

    # 5. transaction_count
    if ctx.checkout_count >= ctx.policy.max_transactions:
        add_check("transaction_count", False, "policy.tx_count_exceeded")
        return fail("policy.tx_count_exceeded")
    add_check("transaction_count", True)

    # 6. item_qty (and sku_duplicate)
    total_qty = 0
    seen_skus = set()
    for item in ctx.cart_items:
        qty = item.get("qty", 0)
        if qty <= 0:
            add_check("item_qty", False, "policy.qty_invalid")
            return fail("policy.qty_invalid")
        total_qty += qty

        sku = item.get("sku")
        if sku in seen_skus:
            add_check("item_qty", False, "policy.sku_duplicate")
            return fail("policy.sku_duplicate")
        seen_skus.add(sku)

    add_check("item_qty", True)

    # 7. item_blocked_sku
    for item in ctx.cart_items:
        sku = item.get("sku")
        if sku in ctx.policy.blocked_skus:
            add_check("item_blocked_sku", False, "policy.sku_blocked")
            return fail("policy.sku_blocked")
    add_check("item_blocked_sku", True)

    # 8. item_tag_allowlist
    if ctx.policy.allowed_tags:  # Empty = unconstrained
        for item in ctx.cart_items:
            sku = item.get("sku")
            tags = set(item.get("tags", []))
            allowed = set(ctx.policy.allowed_tags)

            if ctx.policy.tag_mode == "all":
                # All tags must be in allowed_tags
                if not tags.issubset(allowed):
                    add_check("item_tag_allowlist", False, "policy.tag_violation")
                    return fail("policy.tag_violation")
            else:  # "any"
                # At least one tag must be in allowed_tags
                if not tags & allowed:
                    add_check("item_tag_allowlist", False, "policy.tag_violation")
                    return fail("policy.tag_violation")
    add_check("item_tag_allowlist", True)

    # 9. spend_per_tx
    cart_total = sum(item.get("qty", 0) * item.get("unit_minor", 0) for item in ctx.cart_items)

    # Apply campaign discounts if present
    effective_total = cart_total
    for item in ctx.cart_items:
        campaign_id = item.get("campaign_id")
        if campaign_id and campaign_id in ctx.campaign_lookup:
            campaign = ctx.campaign_lookup[campaign_id]
            discount_bps = campaign.get("offer_terms", {}).get("discount_bps", 0)
            if discount_bps > 0:
                item_total = item.get("qty", 0) * item.get("unit_minor", 0)
                discount = (item_total * discount_bps) // 10000
                effective_total -= discount

    if effective_total > ctx.policy.max_spend_per_tx_minor:
        add_check("spend_per_tx", False, "policy.spend_per_tx_exceeded")
        return fail("policy.spend_per_tx_exceeded")
    add_check("spend_per_tx", True)

    # 10. spend_envelope (delegated only - not implemented in MVP)
    # For root policy, this check is skipped
    # In delegation, would check against envelope budget
    add_check("spend_envelope", True)

    # 11. spend_cumulative (root budget)
    if ctx.cumulative_spend_minor + effective_total > ctx.policy.max_spend_total_minor:
        add_check("spend_cumulative", False, "policy.spend_cumulative_exceeded")
        return fail("policy.spend_cumulative_exceeded")
    add_check("spend_cumulative", True)

    # 12. campaign_validity (v3.0 - appended)
    for item in ctx.cart_items:
        campaign_id = item.get("campaign_id")
        if campaign_id:
            if campaign_id not in ctx.campaign_lookup:
                add_check("campaign_validity", False, "policy.campaign_inactive")
                return fail("policy.campaign_inactive")

            campaign = ctx.campaign_lookup[campaign_id]
            if campaign.get("state") != "ACTIVE":
                add_check("campaign_validity", False, "policy.campaign_inactive")
                return fail("policy.campaign_inactive")

            # Check window
            starts_at = campaign.get("offer_terms", {}).get("starts_at")
            ends_at = campaign.get("offer_terms", {}).get("ends_at")
            if starts_at and ends_at:
                start_ts = int(datetime.fromisoformat(starts_at.replace("Z", "+00:00")).timestamp())
                end_ts = int(datetime.fromisoformat(ends_at.replace("Z", "+00:00")).timestamp())

                if ctx.now_unix < start_ts or ctx.now_unix >= end_ts:
                    add_check("campaign_validity", False, "policy.campaign_outside_window")
                    return fail("policy.campaign_outside_window")

    add_check("campaign_validity", True)

    # All checks passed - compute AAL level
    from openstore.core.holdcancel import compute_aal_level as compute_aal
    aal_level = compute_aal(
        has_webauthn_assertion=ctx.has_webauthn_assertion,
        policy_allows_no_human_authority=False,  # Would come from policy
        amount_minor=effective_total,
    )

    return CompilerResult(
        allowed=True,
        reason_code=None,
        transcript=transcript,
        aal_level=int(aal_level),
        spend_per_tx_minor=effective_total,
        spend_cumulative_minor=ctx.cumulative_spend_minor + effective_total,
        effective_amount_minor=effective_total,
    )


def get_compiler_version() -> str:
    """Return compiler version for PoAI bundle."""
    return "1.0.0"


def get_compiler_digest() -> str:
    """Return compiler code digest for PoAI bundle."""
    # In production, this would be a hash of the compiler source
    return "sha256:placeholder-compiler-digest"
