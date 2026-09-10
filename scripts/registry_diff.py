#!/usr/bin/env python3
# scripts/registry_diff.py
# Prints any identifier mismatch between code and REGISTRY.json
# MUST print nothing for a clean build (per AGENTS.md)

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

# Exception classes whose FIRST positional argument is a closed-set reason code.
# Q-028: nine campaign.* codes lived in the tree unregistered because this script
# only ever validated REGISTRY.json's shape and never read source. Q-035 widens
# the scan to every reason-code-carrying exception. OAuth protocol errors
# (invalid_client/invalid_grant/invalid_token per RFC 6749) live in the HTTP
# `error` field, not REGISTRY — only auth.*-prefixed OAuthError codes are
# checked. WebAuthnError is checked only for registered codes
# (assertion_required/webauthn_unsupported_alg); audit-only failure_type
# strings (Q-005 AMENDMENT) are never reason codes.
_REASON_CODE_EXCEPTIONS = {
    "CampaignValidationError",
    "RazorpayError",
    "CommerceError",
    "HandoffError",
    "WebhookError",
}
# Prefix-gated: only codes starting with one of these prefixes are checked for
# these exception types (lets protocol-local names pass through).
_PREFIX_GATED: dict[str, tuple[str, ...]] = {
    "OAuthError": ("auth.",),
    "WebAuthnError": ("assertion_required", "webauthn_unsupported_alg"),
}

_SRC = Path("src/openstore")


def load_registry() -> dict:
    with open("REGISTRY.json") as f:
        return json.load(f)


def check_raised_reason_codes(registry: dict) -> int:
    """Diff every literal reason code constructed in src/ against REGISTRY.json.

    Only literal first arguments are checked; a computed one is skipped rather
    than guessed at (R0.3 — never invent a value, including here)."""
    known = set(registry["reason_codes"])
    failed = 0
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            print(f"UNPARSEABLE {path}: {e}", file=sys.stderr)
            return 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            code = first.value
            if name in _REASON_CODE_EXCEPTIONS:
                pass
            elif name in _PREFIX_GATED:
                prefixes = _PREFIX_GATED[name]
                if not code.startswith(prefixes):
                    continue
            else:
                continue
            if code not in known:
                print(
                    f"UNREGISTERED REASON CODE {code!r} "
                    f"raised at {path}:{node.lineno}",
                    file=sys.stderr,
                )
                failed = 1
    return failed


def main() -> int:
    registry = load_registry()

    # In Stage 1, we just validate REGISTRY.json is valid JSON and has required keys
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
        if key not in registry:
            print(f"MISSING KEY IN REGISTRY: {key}", file=sys.stderr)
            return 1

    if not registry.get("enums_are_exhaustive"):
        print("enums_are_exhaustive must be true", file=sys.stderr)
        return 1

    # Check for duplicates in each list
    for key, value in registry.items():
        if isinstance(value, list):
            seen = set()
            for item in value:
                if item in seen:
                    print(f"DUPLICATE IN {key}: {item}", file=sys.stderr)
                    return 1
                seen.add(item)

    return check_raised_reason_codes(registry)


if __name__ == "__main__":
    sys.exit(main())
