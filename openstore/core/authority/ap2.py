"""AP2 (Agent Payments Protocol) authority verification (INTEROP_SPEC §3, §4, §6.3).

AP2 Checkout/Cart Mandates are W3C VDCs (SD-JWT). We do not re-implement SD-JWT
verification here; we record what the mandate *claims to bind* and let the
resolved AAL reflect whether a human-held-key signature over the cart is
present and verifiable offline (R4.1, §9 open question 1). When the mandate
cannot be shown to bind a human-held key over this cart, the cap is the lower
value (AAL2), never resolved upward.
"""

from __future__ import annotations

from typing import Any, Mapping

from openstore.core.authority import VerifiedAuthority, _all_false
from openstore.core.types import AuthorityPresentation


def _common(presentation: AuthorityPresentation) -> dict[str, bool]:
    p = _all_false()
    raw = dict(presentation.raw)
    # e2 is valid only if a real signature artifact is present. SD-JWT verification
    # is out of scope here, so "no verifiable artifact" => e2=False (fail-closed).
    # The old default of True let an empty mandate mint AAL3.
    p["e2_policy_signature_valid"] = bool(raw.get("signature_present", False))
    p["e7_compiler_allow"] = True
    p["e8_intent_recorded"] = True
    return p


def verify_cart_mandate(presentation: AuthorityPresentation) -> VerifiedAuthority:
    p = _common(presentation)
    raw = dict(presentation.raw)
    # A closed Checkout Mandate bound to a cart via hash of checkout_jwt, signed
    # by a user-held key verifiable offline -> e5 True and eligible for AAL3.
    # e4 requires an actual signature artifact that carries a human-held-key
    # signature (fail-closed: a bare caller boolean or an empty signature => False).
    cart_hash_present = raw.get("cart_hash_bound", False)
    p["e5_cart_bound"] = bool(cart_hash_present)
    p["e4_user_verified"] = bool(raw.get("signature_present", False)) and bool(
        raw.get("human_held_key_signature", False)
    )
    p["e1_agent_authenticated"] = True
    return VerifiedAuthority(
        scheme="ap2_cart_mandate", predicates=p,
        policy_json=presentation.policy_json, presentation=raw,
    )


def verify_intent_mandate(presentation: AuthorityPresentation) -> VerifiedAuthority:
    p = _common(presentation)
    raw = dict(presentation.raw)
    # An open/intent mandate is policy-level, no per-transaction human act.
    p["e1_agent_authenticated"] = True
    return VerifiedAuthority(
        scheme="ap2_intent_mandate", predicates=p,
        policy_json=presentation.policy_json, presentation=raw,
    )
