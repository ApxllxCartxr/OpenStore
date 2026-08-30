"""Assurance tier resolution (IMPLEMENTATION_SPEC §5).

`resolve_aal` is a pure function of `Predicates` + `policy_version`. It is
byte-identical between the issuer and the verifier (no divergence allowed — a
silent tier change is the one failure mode that discredits the format).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from openstore.core.authority import (
    MAX_AAL_BY_SCHEME,
    SCHEME_CAP_REASONS,
    DELEGATION_AAL_REASONS,
)

AAL_REASONS: Tuple[str, ...] = (
    "policy_signature_invalid",
    "compiler_denied_or_transcript_mismatch",
    "agent_unauthenticated",
    "policy_schema_legacy",
    "assertion_stale",
    "catalog_unattested",
    "user_not_verified",
    "intent_unrecorded",
    "notification_missing",
    "no_per_transaction_binding",
    *SCHEME_CAP_REASONS,   # INTEROP_SPEC §4.2 — five scheme-cap reasons
    *DELEGATION_AAL_REASONS,  # DELEGATION §6.2 — depth / multi-merchant caps
)


@dataclass(frozen=True, slots=True)
class Predicates:
    e1_agent_authenticated: bool = False
    e2_policy_signature_valid: bool = False
    e3_assertion_fresh: bool = False
    e4_user_verified: bool = False
    e5_cart_bound: bool = False
    e6_catalog_attested: bool = False
    e7_compiler_allow: bool = False
    e8_intent_recorded: bool = False
    e9_notified: bool = False


def resolve_aal(p: Predicates, policy_version: int) -> Tuple[int, Tuple[str, ...]]:
    if not p.e2_policy_signature_valid:
        return (0, ("policy_signature_invalid",))
    if not p.e7_compiler_allow:
        return (0, ("compiler_denied_or_transcript_mismatch",))
    if not p.e1_agent_authenticated:
        return (0, ("agent_unauthenticated",))
    if policy_version != 2:
        return (1, ("policy_schema_legacy",))
    if not p.e3_assertion_fresh:
        return (1, ("assertion_stale",))
    if not p.e6_catalog_attested:
        return (1, ("catalog_unattested",))
    if not p.e4_user_verified:
        return (1, ("user_not_verified",))
    if not (p.e8_intent_recorded and p.e9_notified):
        reasons = []
        if not p.e8_intent_recorded:
            reasons.append("intent_unrecorded")
        if not p.e9_notified:
            reasons.append("notification_missing")
        return (1, tuple(reasons))
    if p.e5_cart_bound:
        return (3, ())
    return (2, ("no_per_transaction_binding",))
