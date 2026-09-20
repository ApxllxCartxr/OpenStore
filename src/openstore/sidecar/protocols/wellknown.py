"""The cards a stranger reads before it knows anything about this shop.

Three documents, all public and unauthenticated by design:

- `/.well-known/agent-commerce.json` — who this Merchant is, what protocols the
  sidecar speaks, where the agent endpoints are, and **what it refuses**.
- `/.well-known/ucp.json` — the UCP manifest.
- `/.well-known/jwks.json` — the Merchant's signing keys, so a receipt can be
  verified by anyone holding it.

Public is the point. A card behind authentication is a card no new agent can
read, and the whole admission story (ADR-0012) starts with a stranger fetching
one without asking permission first.

**The card states the refusals inline.** An agent that reads it knows before it
starts that there is no delegated-credential path here and that every spend
terminates in a human tap — which is kinder than finding out at the last step.
"""

from __future__ import annotations

from typing import Any

from openstore.sidecar.core.codes import AuthorityKind, PaymentMethod, Protocol, Scope
from openstore.sidecar.protocols.registry import TOGGLE_ORDER, badge, spec_for


def agent_commerce_card(
    *,
    merchant_domain: str,
    merchant_name: str,
    enabled_methods: frozenset[PaymentMethod],
    enabled_authority_kinds: frozenset[AuthorityKind],
    demo: bool,
) -> dict[str, Any]:
    origin = f"https://{merchant_domain}"
    return {
        "version": "1",
        "merchant": {"name": merchant_name, "domain": merchant_domain},
        "demo": demo,
        "endpoints": {
            "mcp": f"{origin}/agent/mcp",
            "tools": f"{origin}/agent/tools",
            "register": f"{origin}/agent/register",
            "token": f"{origin}/agent/token",
            "feed": f"{origin}/agent/feed.json",
            "approve": f"{origin}/agentic/approve",
            "jwks": f"{origin}/.well-known/jwks.json",
        },
        "protocols": [spec_for(p).protocol.value for p in TOGGLE_ORDER],
        "scopes": [s.value for s in Scope],
        "admission": {
            # Two routes, one authority. No Merchant action is needed for the
            # second: that is the product, not a convenience.
            "self_registration": True,
            "oauth_client_credentials": True,
            "signature": "RFC 9421 over @method, @target-uri and content-digest",
            "agent_id": "RFC 7638 JWK thumbprint",
        },
        "authority": {
            "kinds_accepted": sorted(k.value for k in enabled_authority_kinds),
            "completion": "redirect-only",
            "note": (
                "Every spend terminates in a fresh human Authority at the approve URL on "
                "this domain. An agent may build a basket; it may not spend."
            ),
        },
        "payment": {
            "methods_enabled": sorted(m.value for m in enabled_methods),
            "delegated_credentials": "refused",
            "note": (
                "This Merchant does not accept agent-held payment credentials. Their "
                "absence is deliberate (ADR-0008, ADR-0013), not unimplemented."
            ),
        },
        "conformance": [badge(protocol, enabled_methods) for protocol in TOGGLE_ORDER],
    }


def ucp_manifest(*, merchant_domain: str, merchant_name: str) -> dict[str, Any]:
    origin = f"https://{merchant_domain}"
    spec = spec_for(Protocol.UCP)
    return {
        "ucp_version": spec.version,
        "merchant": {"name": merchant_name, "domain": merchant_domain},
        "capabilities": {
            "catalog": True,
            "checkout": True,
            # Completion is a buyer escalation to this Merchant's own approve
            # page, which is UCP's own path rather than a deviation from it.
            "direct_completion": False,
            "buyer_escalation": True,
        },
        "escalation_url": f"{origin}/agentic/approve",
        "deviations": badge(Protocol.UCP, frozenset())["deviations"],
    }


def jwks_document(keyring_jwks: dict[str, Any]) -> dict[str, Any]:
    """The Merchant's public keys, revoked ones included.

    A revoked key stays published because a receipt it signed **before**
    revocation must still verify, and a verifier with no copy of the key cannot
    check that at all (ADR-0014).
    """
    return keyring_jwks
