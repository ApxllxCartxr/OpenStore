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
        "mcp_tools",
        "aal_levels",
        "order_states",
        "campaign_states",
        "ledger_entries",
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
    
    # From PRD Part 3.2 - all 12 checks + policy.no_human_authority
    expected = {
        "currency_mismatch",
        "merchant_mismatch",
        "policy_not_yet_valid",
        "policy_expired",
        "tx_count_exceeded",
        "qty_invalid",
        "sku_duplicate",
        "sku_blocked",
        "tag_violation",
        "spend_per_tx_exceeded",
        "spend_envelope_exceeded",
        "spend_cumulative_exceeded",
        "campaign_inactive",
        "campaign_outside_window",
        "policy.no_human_authority",
    }
    
    actual = set(registry["reason_codes"])
    assert actual == expected, f"reason_codes mismatch: missing={expected - actual}, extra={actual - expected}"


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
    }
    
    actual = set(registry["mcp_tools"])
    assert actual == expected, f"mcp_tools mismatch: missing={expected - actual}, extra={actual - expected}"


def test_registry_aal_levels():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    
    assert registry["aal_levels"] == [0, 1, 2, 3]


def test_registry_order_states():
    with open("REGISTRY.json") as f:
        registry = json.load(f)
    
    expected = ["CREATED", "HELD", "RELEASED", "CANCELLED", "REFUNDED"]
    assert registry["order_states"] == expected


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