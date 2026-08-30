"""Agent reasoning capture (AGENT_LAYER.md §5).

`agent_plan` is an *unsigned* claim the buyer agent attaches to `human_intent`.
The verifier labels it `merchant_asserted` (R5.1c): it is evidence for a human
adjudicator, never an input to any predicate or to `resolve_aal`.
"""

from __future__ import annotations

from typing import Optional

from .canonical import canonical_json_bytes, digest


def make_agent_plan(
    model: str,
    interpretation: str,
    constraints_extracted: list[str],
    candidates_considered: list[dict],
) -> dict:
    """Build the `agent_plan` object with a tamper-evident `plan_digest`.

    R5.1d: `plan_digest` = digest(agent_plan without plan_digest), so an
    arbitrator can detect post-hoc edits to an unsigned plan.
    """
    plan = {
        "model": model,
        "interpretation": interpretation,
        "constraints_extracted": list(constraints_extracted),
        "candidates_considered": [dict(c) for c in candidates_considered],
    }
    plan_digest = digest_json_without(plan, "plan_digest")
    plan["plan_digest"] = plan_digest
    return plan


def digest_json_without(obj: dict, *skip_keys: str) -> str:
    stripped = {k: v for k, v in obj.items() if k not in skip_keys}
    return "sha256:" + _sha256(stripped)


def _sha256(obj: dict) -> str:
    import hashlib

    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def verify_plan_digest(plan: dict) -> bool:
    """True iff `plan.plan_digest` matches digest(plan without plan_digest)."""
    if not plan or "plan_digest" not in plan:
        return False
    expected = digest_json_without(plan, "plan_digest")
    return expected == plan["plan_digest"]
