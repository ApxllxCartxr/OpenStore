# OpenStore core — Policy signing ceremony (S3.5, PRD §3.1 / §3.2a).
#
# The signing ceremony is the single place an IntentPolicy moves from draft to
# enforceable: it validates policy_version (R0.5, closed set), enforces the
# per-user aggregate cap (PRD §3.2a), and produces a signed policy anchored to
# the human operator. No LLM, no parsing of agent output here (R0.9).

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openstore.models import IntentPolicy

# Per-user aggregate cap (PRD §3.2a). Demo default; config.py is out of scope for
# this stage, so the value lives here as a module constant.
PER_USER_AGGREGATE_CAP_MINOR = 500_000


class LegacyPolicyError(Exception):
    """Raised when a policy carries a policy_version != 2 (R0.5: never mapped,
    never defaulted). Policy version is a closed set: only 2 is current."""


def aggregate_cap_exceeds(
    *,
    max_spend_total_minor: int,
    aggregate_spent_minor: int,
    per_user_aggregate_cap_minor: int,
) -> bool:
    """True if committing this policy's max spend would push the enrolled user's
    aggregate above the per-user cap (PRD §3.2a, signing-time check)."""
    if max_spend_total_minor < 0 or aggregate_spent_minor < 0 or per_user_aggregate_cap_minor < 0:
        raise ValueError("amounts must be non-negative integer minor units")
    return aggregate_spent_minor + max_spend_total_minor > per_user_aggregate_cap_minor


def validate_policy_version(policy_version: int) -> None:
    """policy_version must be 2; anything else is a hard LegacyPolicyError (R0.5)."""
    if policy_version != 2:
        raise LegacyPolicyError(
            f"unsupported policy_version {policy_version}; only 2 is current"
        )


@dataclass(frozen=True)
class SigningValidation:
    ok: bool
    reason_code: str | None = None


def validate_for_signing(
    *,
    policy: IntentPolicy,
    aggregate_spent_minor: int,
    per_user_aggregate_cap_minor: int = PER_USER_AGGREGATE_CAP_MINOR,
) -> SigningValidation:
    """Validate a draft policy before it can be signed.

    Returns a SigningValidation. The aggregate-cap breach is returned as a
    rejection carrying policy.aggregate_cap_exceeded (R0.5 — returned, not
    raised, matching the compiler's reason-coded contract). Invalid policy
    version raises LegacyPolicyError (never mapped to a reason code).
    """
    validate_policy_version(policy.policy_version)
    if aggregate_cap_exceeds(
        max_spend_total_minor=policy.max_spend_total_minor,
        aggregate_spent_minor=aggregate_spent_minor,
        per_user_aggregate_cap_minor=per_user_aggregate_cap_minor,
    ):
        return SigningValidation(ok=False, reason_code="policy.aggregate_cap_exceeded")
    return SigningValidation(ok=True)


def compute_user_aggregate(policies: list[IntentPolicy]) -> int:
    """Sum of the enrolled user's current active policies' max_spend_total_minor.

    Used as the 'aggregate_spent_minor' input to the signing-time aggregate cap
    check (PRD §3.2a). Totals are recomputed server-side; never trusted from the
    client (R0.8).
    """
    total = 0
    for p in policies:
        if p.is_active:
            total += p.max_spend_total_minor
    return total


def blast_radius(policies: list[IntentPolicy], envelope_ids: list[str]) -> dict[str, Any]:
    """Compute the operator's blast radius (S3.5): counts of signed policies and
    enrolled credentials, the envelope ids they govern, and whether a projected
    aggregate-cap freeze would trigger for the current active portfolio."""
    active = [p for p in policies if p.is_active]
    aggregate = compute_user_aggregate(active)
    projected_freeze = aggregate_cap_exceeds(
        max_spend_total_minor=0,
        aggregate_spent_minor=aggregate,
        per_user_aggregate_cap_minor=PER_USER_AGGREGATE_CAP_MINOR,
    )
    return {
        "policies": len(active),
        "credentials": len({p.webauthn_credential_id for p in active if p.webauthn_credential_id}),
        "envelope_ids": sorted(set(envelope_ids)),
        "projected_freeze": projected_freeze,
    }


# ---------------------------------------------------------------------------
# Signing ceremony (S3.5): build a deterministic policy_hash and, given a
# verified human assertion, produce the signed IntentPolicy.
# ---------------------------------------------------------------------------
# The deterministic §3.1 field set that the server-side policy hash is computed
# over (R0.8: hash is recomputed here, never trusted from the client). These are
# exactly the IntentPolicy fields the form authorizes; id / policy_hash /
# webauthn_* / signed_at / is_active are derived server-side and excluded.
POLICY_HASH_FIELDS = (
    "merchant_id",
    "policy_version",
    "currency",
    "max_spend_per_tx_minor",
    "max_spend_total_minor",
    "max_transactions",
    "allowed_tags",
    "tag_mode",
    "blocked_skus",
    "not_before",
    "expires_at",
    "assertion_max_age_seconds",
    "fulfilment_mode",
    "required_skus",
)


def compute_policy_hash(fields: dict[str, Any]) -> str:
    """Deterministic sha256 (hex) over the canonical JSON of the authorizeable
    §3.1 fields. A field missing from the supplied form is treated as absent,
    but a whitelist guarantees the hash never includes client-invented keys."""
    from openstore.core.poai import canonical_json

    payload = {k: fields[k] for k in POLICY_HASH_FIELDS if k in fields}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class SigningOutcome:
    ok: bool
    reason_code: str | None = None
    policy: IntentPolicy | None = None


def complete_policy_signing(
    *,
    fields: dict[str, Any],
    merchant_id: str,
    user_id: str,
    credential_id: str,
    webauthn_sign_count: int,
    aggregate_spent_minor: int,
    per_user_aggregate_cap_minor: int = PER_USER_AGGREGATE_CAP_MINOR,
    signed_at: str | None = None,
) -> SigningOutcome:
    """Construct and validate the signed IntentPolicy after a verified
    `{"mode": "policy"}` assertion.

    Raises LegacyPolicyError if policy_version != 2 (R0.5: never mapped).
    Returns SigningOutcome(ok=False, reason_code="policy.aggregate_cap_exceeded")
    if committing this policy's max spend breaches the per-user aggregate cap
    (§3.2a). Otherwise returns SigningOutcome(ok=True, policy=...) with the
    server-derived id, policy_hash, and signing anchor filled in. No LLM, no
    client-trusted totals (R0.8/R0.9).
    """
    version = fields.get("policy_version", 2)
    validate_policy_version(version)

    policy_hash = compute_policy_hash(fields)
    policy_id = "pol_" + hashlib.sha256((merchant_id + ":" + policy_hash).encode()).hexdigest()[:24]

    policy = IntentPolicy(
        id=policy_id,
        merchant_id=merchant_id,
        policy_version=version,
        policy_hash=policy_hash,
        currency=fields.get("currency", "INR"),
        max_spend_per_tx_minor=int(fields["max_spend_per_tx_minor"]),
        max_spend_total_minor=int(fields["max_spend_total_minor"]),
        max_transactions=int(fields["max_transactions"]),
        allowed_tags=list(fields.get("allowed_tags", [])),
        tag_mode=fields.get("tag_mode", "all"),
        blocked_skus=list(fields.get("blocked_skus", [])),
        not_before=int(fields["not_before"]),
        expires_at=int(fields["expires_at"]),
        assertion_max_age_seconds=int(fields.get("assertion_max_age_seconds", 86400)),
        fulfilment_mode=fields.get("fulfilment_mode", "all_or_nothing"),
        required_skus=list(fields.get("required_skus", [])),
        webauthn_credential_id=credential_id,
        webauthn_sign_count=webauthn_sign_count,
        signed_at=datetime.fromisoformat(signed_at.replace("Z", "+00:00")) if signed_at else datetime.utcnow(),
        is_active=True,
    )

    validation = validate_for_signing(
        policy=policy,
        aggregate_spent_minor=aggregate_spent_minor,
        per_user_aggregate_cap_minor=per_user_aggregate_cap_minor,
    )
    if not validation.ok:
        return SigningOutcome(ok=False, reason_code=validation.reason_code)
    return SigningOutcome(ok=True, policy=policy)
