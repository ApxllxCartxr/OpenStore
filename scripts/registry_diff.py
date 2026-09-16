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
# Q-045 (2026-09-16): AdapterError carves the same blindness — errors.py
# already claimed subclassing CommerceError made its catalog.* codes scanned,
# but this matcher is syntactic (call name), not inheritance-aware, so eleven
# raised codes sat unregistered with a green build. HTTPException
# detail={"reason_code": ...} (evidence.py's checkout.evidence_not_found) was
# the second blind spot. Both shapes are scanned now. buyer.* codes stay OUT
# by construction: they are plain dict returns in agents/buyer_agent.py, never
# exception constructions, and Q-043 keeps them buyer-process-local.
_REASON_CODE_EXCEPTIONS = {
    "CampaignValidationError",
    "RazorpayError",
    "CommerceError",
    "AdapterError",
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


def check_http_exception_details(registry: dict) -> int:
    """Diff HTTPException(detail={"reason_code": ...}) literals.

    Surface handlers (evidence.py, webcart.py) answer HTTP errors with a
    reason_code inside the detail dict rather than a typed exception — the
    same closed set applies (R0.2/R0.5). Only literal dicts are checked; a
    computed detail (e.g. merchant.py's _fail passthrough of an already-
    checked exception's code) is skipped rather than guessed at.
    authority.* codes are checked against authority_reason_codes.
    """
    known = set(registry["reason_codes"]) | set(registry["authority_reason_codes"])
    failed = 0
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            print(f"UNPARSEABLE {path}: {e}", file=sys.stderr)
            return 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args and not node.keywords:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "HTTPException":
                continue
            for kw in node.keywords:
                if kw.arg != "detail" or not isinstance(kw.value, ast.Dict):
                    continue
                for k, v in zip(kw.value.keys, kw.value.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "reason_code"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                        and v.value not in known
                    ):
                        print(
                            f"UNREGISTERED REASON CODE {v.value!r} "
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

    return check_raised_reason_codes(registry) | check_http_exception_details(registry)


if __name__ == "__main__":
    sys.exit(main())
