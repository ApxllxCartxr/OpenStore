# tests/test_registry_compliance.py
# Ensures code identifiers match REGISTRY.json in both directions

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_registry_json_exists():
    assert Path("REGISTRY.json").exists(), "REGISTRY.json must exist at repo root"


def test_registry_json_valid():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    required_keys = [
        "reason_codes",
        "error_namespaces",
        "authority_reason_codes",
        "mcp_tools",
        "oauth_scopes",
        "aal_levels",
        "checkout_states",
        "order_states",
        "campaign_states",
        "ledger_entries",
        "ledger_accounts",
        "verifier_exit_codes",
        "discord_channels",
        "negotiation_states",
        "enums_are_exhaustive",
        "routes",
    ]

    for key in required_keys:
        assert key in registry, f"Missing required key: {key}"

    assert registry["enums_are_exhaustive"] is True


def test_registry_no_duplicates():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    for key, value in registry.items():
        if isinstance(value, list):
            assert len(value) == len(set(value)), f"Duplicates found in {key}"


def test_registry_reason_codes_complete():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    # From PRD Part 3.2 + Part 7 (policy.* namespaced; assertion_required unprefixed)
    expected = {
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
        "policy.no_human_authority",
        "policy.aggregate_cap_exceeded",
        "policy.campaign_inactive",
        "policy.campaign_outside_window",
        "policy.policy_version_unsupported",
        "idempotency_key_reuse_with_different_payload",
        "webauthn_unsupported_alg",
        "unsupported_compiler_digest",
        "request_in_progress",
        "auth.token_verification_failed",
        # DECISION-023 (Q-028): campaign.* was raised by core/campaigns.py from
        # Stage 8 onward while absent from the closed set — registry_diff.py only
        # validated REGISTRY.json's shape and never read source, so nothing caught it.
        "campaign.not_found",
        "campaign.sku_not_found",
        "campaign.discount_out_of_bounds",
        "campaign.invalid_window",
        "campaign.sku_on_blocked_list",
        "campaign.empty_content",
        "campaign.injection_content",
        "campaign.no_webauthn_approval",
        "campaign.webauthn_verification_failed",
        "campaign.max_active_exceeded",
        "campaign.invalid_state_transition",
        # DECISION-027: list_orders/cancel_order (S14) reuse checkout.not_found
        # (already raised by get_order, itself never registered — same class
        # of gap as campaign.* above, scoped narrowly to what this pair
        # touches) and psp.invalid_state (cancel_checkout_by_id, likewise
        # pre-existing and unregistered); checkout.not_owned is new.
        "checkout.not_found",
        "checkout.not_owned",
        "checkout.invalid_state",
        "checkout.invalid_cancel_token",
        "policy.not_found",
        "psp.invalid_state",
        "psp.checkout_not_found",
        "psp.no_payment_link",
        "psp.no_payment",
        "psp.no_payment_id",
        "psp.create_failed",
        "psp.cancel_failed",
        "psp.refund_failed",
        "psp.amount_invalid",
        "psp.currency_mismatch",
        "psp.live_key_forbidden",
        "psp.duplicate_unrecoverable",
        "webhook.unknown_event",
        "webhook.missing_reference_id",
        "webhook.invalid_transition",
        "webhook.invalid_payload",
        "webhook.amount_mismatch",
        "webhook.currency_mismatch",
        "auth.unknown_tool",
        "internal_error",
        # cancel_order's _require_scope("checkout:initiate") call raises this —
        # already used by create_cart/update_cart/checkout_initiate/checkout_confirm,
        # also unregistered until now.
        "auth.insufficient_scope",
    }

    actual = set(registry["reason_codes"])
    assert actual == expected, (
        f"reason_codes mismatch: missing={expected - actual}, extra={actual - expected}"
    )


def test_registry_mcp_tools_complete():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = {
        "search_products",
        "get_product",
        "create_cart",
        "update_cart",
        "checkout_initiate",
        "checkout_confirm",
        "get_order",
        "get_audit_log",
        "webauthn_register_begin",
        "webauthn_register_complete",
        "webauthn_begin_assertion",
        "webauthn_complete_assertion",
        "list_campaigns",
        "get_campaign",
        "resolve_policy",
        "create_policy_handoff",
        "create_cart_handoff",
        "list_orders",
        "cancel_order",
        "set_order_message",
    }

    actual = set(registry["mcp_tools"])
    assert actual == expected, (
        f"mcp_tools mismatch: missing={expected - actual}, extra={actual - expected}"
    )


def test_registry_aal_levels():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    assert registry["aal_levels"] == [0, 1, 2, 3]


def test_registry_order_states():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = ["CREATED", "HELD", "RELEASED", "CANCELLED", "PAID", "FAILED", "REFUNDED"]
    assert registry["order_states"] == expected


def test_registry_error_namespaces():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    assert registry["error_namespaces"] == [
        "auth.*",
        "policy.*",
        "checkout.*",
        "psp.*",
        "ratelimit.*",
        "agent.*",
        "hold.*",
        "authority.*",
        "orchestration.*",
        # DECISION-023: orchestration.* stays reserved and unused; campaign.* is
        # the namespace core/campaigns.py actually raises into.
        "campaign.*",
    ]


def test_registry_authority_reason_codes():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    assert registry["authority_reason_codes"] == [
        "authority.unknown_scheme",
        "authority.scheme_capped_native_webauthn",
        "authority.scheme_capped_ap2_intent_mandate",
        "authority.scheme_capped_ap2_cart_mandate",
        "authority.scheme_capped_acp_delegated_token",
        "authority.scheme_capped_none",
        "authority.handoff_not_found",
        "authority.handoff_expired",
        "authority.handoff_consumed",
        "authority.policy_unsigned",
    ]


def test_registry_oauth_scopes():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    assert registry["oauth_scopes"] == [
        "catalog:read",
        "cart:write",
        "checkout:initiate",
        "checkout:confirm",
    ]


def test_registry_checkout_states():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    assert registry["checkout_states"] == [
        "PENDING",
        "POLICY_VERIFIED",
        "ORDER_CREATED",
        "REJECTED",
    ]


def test_registry_ledger_accounts():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    assert registry["ledger_accounts"] == {
        "escrow": ["customer_hold", "merchant_pending"],
        "economic": ["merchant_revenue", "platform"],
    }


def test_registry_campaign_states():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = ["DRAFT", "PENDING_APPROVAL", "ACTIVE", "PAUSED", "EXPIRED", "REJECTED"]
    assert registry["campaign_states"] == expected


def test_registry_ledger_entries():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = ["RESERVE", "CAPTURE", "RELEASE", "REFUND"]
    assert registry["ledger_entries"] == expected


def test_registry_verifier_exit_codes():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    assert registry["verifier_exit_codes"] == [0, 1, 2, 3, 4]


def test_registry_discord_channels():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = ["#buyer-trace", "#merchant-trace", "#money-trace", "#alerts"]
    assert registry["discord_channels"] == expected


def test_registry_negotiation_states():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    expected = ["PROPOSED", "COUNTERED", "ACCEPTED", "NO_COMPLIANT_PATH", "AMENDMENT_REQUESTED"]
    assert registry["negotiation_states"] == expected


def test_registry_routes_complete():
    with open("REGISTRY.json") as f:
        registry = json.load(f)

    # Core routes from PRD Part 6
    expected_routes = {
        "/.well-known/agent-commerce.json",
        "/.well-known/agent-policy.json",
        "/.well-known/agent-card.json",
        "/.well-known/oauth-authorization-server",
        "/.well-known/poai-jwks.json",
        "/.well-known/agent-campaigns.json",
        "/agent/catalog",
        "/agent/mcp",
        "/agent/acp",
        "/agent/campaigns",
        "/protocols/<name>/spec-excerpt",
        "/intent/studio",
        "/campaign/studio",
        "/campaign/<campaign_id>/approve",
        "/campaign/<campaign_id>/reject",
        "/admin/agents",
        "/hold/<cancel_token>/cancel",
        "/orders/<checkout_id>/evidence",
        "/orders/<id>/evidence/view",
        "/orders/intent/<intent_id>/evidence",
        "/internal/webauthn/*",
        "/internal/policy/blast-radius",
        "/admin/*",
    }

    actual = set(registry["routes"])
    assert expected_routes.issubset(actual), f"Missing routes: {expected_routes - actual}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
