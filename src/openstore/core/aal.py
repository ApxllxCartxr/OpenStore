# OpenStore core — AAL ladder predicates (e1–e9)

from __future__ import annotations

from dataclasses import dataclass

from openstore.core.holdcancel import AALLevel


@dataclass(frozen=True)
class AALPredicates:
    """AAL predicates e1–e9 from PRD Part 3.5."""
    e1: bool  # Has WebAuthn assertion
    e2: bool  # Assertion verified (uv=true)
    e3: bool  # Assertion within max_age
    e4: bool  # Policy has human authority (not policy.no_human_authority)
    e5: bool  # Amount <= AAL3 threshold
    e6: bool  # Amount <= AAL2 threshold
    e7: bool  # Amount <= AAL1 threshold
    e8: bool  # Policy allows no_human_authority (AAL0 path)
    e9: bool  # Delegated policy with envelope


def evaluate_predicates(
    has_webauthn_assertion: bool,
    assertion_verified: bool,
    assertion_age_seconds: int,
    assertion_max_age_seconds: int,
    policy_allows_no_human_authority: bool,
    amount_minor: int,
    aal3_threshold: int = 10000,
    aal2_threshold: int = 50000,
    aal1_threshold: int = 200000,
    is_delegated: bool = False,
    has_envelope: bool = False,
) -> AALPredicates:
    """Evaluate all AAL predicates."""
    return AALPredicates(
        e1=has_webauthn_assertion,
        e2=assertion_verified,
        e3=assertion_age_seconds <= assertion_max_age_seconds,
        e4=not policy_allows_no_human_authority,
        e5=amount_minor <= aal3_threshold,
        e6=amount_minor <= aal2_threshold,
        e7=amount_minor <= aal1_threshold,
        e8=policy_allows_no_human_authority,
        e9=is_delegated and has_envelope,
    )


def compute_aal_from_predicates(predicates: AALPredicates) -> AALLevel:
    """
    Compute AAL level from predicates (first-match-wins).

    Per PRD Part 3.5:
    - AAL3 if e1∧e2∧e3∧e4∧e5
    - AAL2 if e1∧e2∧e3∧e4∧e6
    - AAL1 if e1∧e2∧e3∧e4∧e7
    - AAL0 if e8 (policy.no_human_authority)
    """
    if predicates.e8:
        return AALLevel.AAL0

    if predicates.e1 and predicates.e2 and predicates.e3 and predicates.e4:
        if predicates.e5:
            return AALLevel.AAL3
        if predicates.e6:
            return AALLevel.AAL2
        if predicates.e7:
            return AALLevel.AAL1

    # Default to AAL1 if authorized but amount exceeds thresholds
    return AALLevel.AAL1


def get_aal_reasons(
    aal_level: AALLevel,
    predicates: AALPredicates,
    aal3_threshold: int = 10000,
    aal2_threshold: int = 50000,
    aal1_threshold: int = 200000,
) -> list[str]:
    """Get human-readable reasons for AAL level."""
    reasons = []

    if aal_level == AALLevel.AAL0:
        reasons.append("policy.no_human_authority")
    elif aal_level == AALLevel.AAL3:
        reasons.append("WebAuthn assertion verified (uv=true)")
        reasons.append("Assertion within max_age")
        reasons.append("Policy requires human authority")
        reasons.append(f"Amount ≤ AAL3 threshold (₹{aal3_threshold/100:.0f})")
    elif aal_level == AALLevel.AAL2:
        reasons.append("WebAuthn assertion verified (uv=true)")
        reasons.append("Assertion within max_age")
        reasons.append("Policy requires human authority")
        reasons.append(f"Amount ≤ AAL2 threshold (₹{aal2_threshold/100:.0f})")
    elif aal_level == AALLevel.AAL1:
        reasons.append("WebAuthn assertion verified (uv=true)")
        reasons.append("Assertion within max_age")
        reasons.append("Policy requires human authority")
        reasons.append(f"Amount ≤ AAL1 threshold (₹{aal1_threshold/100:.0f})")

    if not predicates.e1:
        reasons.append("No WebAuthn assertion provided")
    if not predicates.e2:
        reasons.append("WebAuthn assertion verification failed")
    if not predicates.e3:
        reasons.append("WebAuthn assertion exceeded max_age")
    if not predicates.e4:
        reasons.append("Policy allows no human authority")

    return reasons
