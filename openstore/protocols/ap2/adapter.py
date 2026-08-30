"""AP2 (Agent Payments Protocol) adapter — ingest (INTEROP_SPEC §6.3).

AP2 is a mandate vocabulary, not a transport. We ingest an AP2 Checkout/Cart
Mandate as an `AuthorityPresentation` (`ap2_cart_mandate` or `ap2_intent_mandate`),
verify it, and map its constraints onto a v2 `IntentPolicy`. Emission (core ->
AP2-shaped) lives in `emit.py`.

SD-JWT / VDC verification is out of scope here; we record what the mandate
*claims to bind* (R4.1, §9 open question 1) and let the resolved AAL reflect
whether a human-held-key signature over the cart is present and verifiable
offline. When it is not, the cap is the lower value (AAL2), never resolved
upward.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from openstore.core.types import AuthorityPresentation

# External AP2 mandate field names. Every one MUST appear in mapping.md.
AP2_EXTERNAL_FIELDS: Tuple[str, ...] = (
    "vct", "type", "constraints", "allowed_merchants", "line_items", "budget",
    "amount_range", "reference", "execution_date", "cart_hash_bound", "signature",
)

# Constraints AP2 may carry that we CAN map onto an OpenStore v2 IntentPolicy.
MAPPABLE_CONSTRAINTS: Tuple[str, ...] = (
    "allowed_merchants", "line_items", "budget", "amount_range",
    "reference", "execution_date", "cart_hash_bound",
)

# Constraints that have no OpenStore equivalent. A mandate carrying one MUST
# fail closed (R6.3a) — silently dropping a constraint the human signed would
# widen an authority the human narrowed.
UNMAPPABLE_CONSTRAINTS: Tuple[str, ...] = (
    "recurrence", "allowed_payment_instruments", "allowed_pisp", "notification_url",
)


class AP2UnmappableConstraint(ValueError):
    """Raised when a mandate constraint has no OpenStore equivalent (R6.3a)."""


def _scheme_for(mandate: Mapping[str, Any]) -> str:
    mtype = str(mandate.get("type", "cart_mandate")).lower()
    if "intent" in mtype:
        return "ap2_intent_mandate"
    return "ap2_cart_mandate"


def ingest_mandate(mandate: Mapping[str, Any]) -> AuthorityPresentation:
    constraints = dict(mandate.get("constraints", {}))
    # §6.3a — any unmappable constraint fails closed, naming the field.
    for key in constraints:
        if key in UNMAPPABLE_CONSTRAINTS:
            raise AP2UnmappableConstraint(
                f"ap2.unmappable_constraint: {key} has no OpenStore equivalent")
        if key not in MAPPABLE_CONSTRAINTS:
            raise AP2UnmappableConstraint(
                f"ap2.unmappable_constraint: unknown constraint {key}")

    signature = dict(mandate.get("signature", {}))
    scheme = _scheme_for(mandate)

    # §6.3c — policy_version 2 only if every v2 field can be populated. We can
    # always populate the v2 fields we know; legacy fallthrough is reserved for
    # mandates we cannot fully interpret (handled by the caller's policy builder).
    policy_json = dict(mandate.get("policy", {})) or None

    raw = {
        "vct": mandate.get("vct"),
        "type": mandate.get("type"),
        "cart_hash_bound": constraints.get("cart_hash_bound") is not None,
        "human_held_key_signature": bool(signature.get("human_held_key_signature", False)),
        # Fail-closed: a signature artifact must actually be present. The old
        # `or mandate.get("signature_present", True)` let a caller claim a
        # signature without one, minting AAL3.
        "signature_present": bool(signature),
    }
    return AuthorityPresentation(scheme=scheme, raw=raw, policy_json=policy_json)
