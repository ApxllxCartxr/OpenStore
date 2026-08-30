"""Authority verification dispatch + scheme caps (INTEROP_SPEC §3, §4).

The verifier for a third-party bundle lives here too, so `openstore.verify`
can evaluate an AP2/ACP authority without the merchant runtime. The final AAL
is `min(resolve_aal(predicates, version), MAX_AAL_BY_SCHEME[scheme])` and, when
the cap binds, `scheme_capped_<scheme>` is appended to the reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from openstore.core.types import VALID_SCHEMES, AuthorityPresentation

# §4.1 — the strongest claim the evidence supports per scheme.
MAX_AAL_BY_SCHEME: dict[str, int] = {
    "native_webauthn": 3,
    "ap2_cart_mandate": 3,
    "ap2_intent_mandate": 2,
    "acp_delegated_token": 1,
    "none": 0,
}

# §4.2 — five reason values, one per scheme (extends IMPLEMENTATION_SPEC §5.3a).
SCHEME_CAP_REASONS: tuple[str, ...] = tuple(
    f"scheme_capped_{s}" for s in MAX_AAL_BY_SCHEME
)

# DELEGATION_AND_ORCHESTRATION §6.1 — the human signed the root; a leaf agent's
# evidence is weaker in proportion to how far it is removed from that signature.
MAX_AAL_BY_DEPTH: dict[int, int] = {0: 3, 1: 2, 2: 2, 3: 1}

# §6.2 — reasons appended when the depth cap, or the unsequenced multi-merchant
# cap (§4.2), binds.
DELEGATION_AAL_REASONS: tuple[str, ...] = (
    "delegated_depth_capped",
    "multi_merchant_envelope_unsequenced",
)


class UnknownSchemeError(ValueError):
    """Raised when a presentation carries a scheme outside the closed set (R3.1)."""


@dataclass(frozen=True, slots=True)
class VerifiedAuthority:
    scheme: str
    predicates: dict[str, bool]   # keys: e1_agent_authenticated, e2_policy_signature_valid,
                                  # e3_assertion_fresh, e4_user_verified, e5_cart_bound,
                                  # e6_catalog_attested, e7_compiler_allow, e8_intent_recorded,
                                  # e9_notified
    policy_json: Mapping[str, Any] | None
    presentation: Mapping[str, Any]


def _all_false() -> dict[str, bool]:
    return {
        "e1_agent_authenticated": False,
        "e2_policy_signature_valid": False,
        "e3_assertion_fresh": False,
        "e4_user_verified": False,
        "e5_cart_bound": False,
        "e6_catalog_attested": False,
        "e7_compiler_allow": False,
        "e8_intent_recorded": False,
        "e9_notified": False,
    }


def verify(presentation: AuthorityPresentation) -> VerifiedAuthority:
    """Dispatch on scheme to the per-scheme verifier (R3.2)."""
    if presentation.scheme not in VALID_SCHEMES:
        raise UnknownSchemeError(
            f"authority.unknown_scheme: {presentation.scheme!r} is not in the closed set"
        )
    from openstore.core.authority import acp, ap2, native_webauthn

    if presentation.scheme == "native_webauthn":
        return native_webauthn.verify(presentation)
    if presentation.scheme == "ap2_cart_mandate":
        return ap2.verify_cart_mandate(presentation)
    if presentation.scheme == "ap2_intent_mandate":
        return ap2.verify_intent_mandate(presentation)
    if presentation.scheme == "acp_delegated_token":
        return acp.verify(presentation)
    # scheme == "none"
    return VerifiedAuthority(
        scheme="none", predicates=_all_false(),
        policy_json=presentation.policy_json, presentation=dict(presentation.raw),
    )


def cap_aal(level: int, reasons: tuple[str, ...], scheme: str) -> tuple[int, tuple[str, ...]]:
    """Apply the per-scheme AAL cap (R4.1/R4.2)."""
    cap = MAX_AAL_BY_SCHEME.get(scheme, 0)
    if level > cap:
        return cap, tuple(reasons) + (f"scheme_capped_{scheme}",)
    return level, tuple(reasons)


def cap_aal_by_depth(level: int, reasons: tuple[str, ...], depth: int) -> tuple[int, tuple[str, ...]]:
    """Apply the per-depth AAL cap (DELEGATION §6.1)."""
    cap = MAX_AAL_BY_DEPTH.get(depth, 0)
    if level > cap:
        return cap, tuple(reasons) + ("delegated_depth_capped",)
    return level, tuple(reasons)


def cap_aal_multi_merchant(
    level: int, reasons: tuple[str, ...], *, merchant_count: int, sequencer_type: str
) -> tuple[int, tuple[str, ...]]:
    """DELEGATION §4.2 — an unsequenced multi-merchant envelope caps at AAL1."""
    if merchant_count > 1 and sequencer_type == "none":
        if level > 1:
            return 1, tuple(reasons) + ("multi_merchant_envelope_unsequenced",)
    return level, tuple(reasons)
