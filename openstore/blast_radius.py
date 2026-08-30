"""Policy Studio blast-radius computation (IMPLEMENTATION_SPEC §9.1).

Given an effective policy and the live catalog, compute the worst-case monetary
and categorical exposure the policy would *allow* — i.e. what a compromised or
reckless agent could still do. Reuses the real `compile_decision` so the
blast-radius never diverges from the enforcement path.
"""

from __future__ import annotations

from typing import Iterable

from openstore import compiler as compiler_mod
from openstore.canonical import canonical_json_bytes, digest


def _allowed(policy: dict, prod: dict) -> bool:
    if prod["sku"] in set(policy.get("blocked_skus", [])):
        return False
    tags = set(prod.get("tags", []))
    allow = set(policy.get("allowed_tags", []))
    mode = policy.get("tag_mode", "all")
    if not allow:
        return True
    if mode == "all":
        return allow.issubset(tags)
    return bool(allow & tags)


def compute_blast_radius(policy: dict, catalog: Iterable[dict]) -> dict:
    # normalise tuple-valued fields so the policy is canonicalisable (§1.1)
    policy = {k: (list(v) if isinstance(v, tuple) else v) for k, v in policy.items()}
    cpolicy = compiler_mod.CompilerPolicy(
        policy_version=policy.get("policy_version", 2),
        merchant_id=policy.get("merchant_id", ""),
        currency=policy.get("currency", "INR"),
        max_spend_per_tx_minor=policy.get("max_spend_per_tx_minor", 0),
        max_spend_total_minor=policy.get("max_spend_total_minor", 0),
        max_transactions=policy.get("max_transactions", 0),
        allowed_tags=tuple(policy.get("allowed_tags", [])),
        tag_mode=policy.get("tag_mode", "all"),
        blocked_skus=tuple(policy.get("blocked_skus", [])),
        not_before=policy.get("not_before", 0),
        expires_at=policy.get("expires_at", 0),
        assertion_max_age_seconds=policy.get("assertion_max_age_seconds", 86400),
    )
    ctx = compiler_mod.CompilerContext(
        merchant_id=cpolicy.merchant_id, currency=cpolicy.currency,
        evaluated_at_unix=0, spent_minor=0, transactions_count=0,
    )

    allowed = [p for p in catalog if _allowed(policy, p)]
    allowed.sort(key=lambda p: p["sku"])

    # per-tx exposure: greedily fill the most expensive allowed items up to the
    # per-transaction ceiling, then ask the compiler if it would ALLOW it.
    per_tx = 0
    chosen = []
    for p in sorted(allowed, key=lambda p: p["price_minor"], reverse=True):
        if per_tx + p["price_minor"] > cpolicy.max_spend_per_tx_minor and per_tx > 0:
            break
        chosen.append(p)
        per_tx += p["price_minor"]
    if chosen:
        items = tuple(compiler_mod.CompilerItem(sku=p["sku"], qty=1,
                                                unit_minor=p["price_minor"],
                                                tags=tuple(p.get("tags", []))) for p in chosen)
        verdict = compiler_mod.compile_decision(items, cpolicy, ctx)
        per_tx_allowed = verdict.verdict == "ALLOW"
    else:
        per_tx_allowed = False

    return {
        "policy_version": cpolicy.policy_version,
        "currency": cpolicy.currency,
        "allowed_sku_count": len(allowed),
        "allowed_skus": [p["sku"] for p in allowed],
        "blocked_skus": list(cpolicy.blocked_skus),
        "tag_mode": cpolicy.tag_mode,
        "allowed_tags": list(cpolicy.allowed_tags),
        "per_tx_limit_minor": cpolicy.max_spend_per_tx_minor,
        "per_tx_worst_case_minor": per_tx,
        "per_tx_worst_case_allowed": per_tx_allowed,
        "cumulative_limit_minor": cpolicy.max_spend_total_minor,
        "max_transactions": cpolicy.max_transactions,
        "worst_case_total_exposure_minor": cpolicy.max_spend_total_minor,
        "policy_digest": digest(policy),
    }
