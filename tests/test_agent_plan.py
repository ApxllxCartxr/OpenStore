"""Tests for agent reasoning capture (AGENT_LAYER.md §5)."""

from openstore.agent_plan import make_agent_plan, verify_plan_digest
from tests.test_verifier import _mint_bundle


def test_plan_digest_is_over_plan_without_plan_digest():
    plan = make_agent_plan(
        model="gemini-2.5-flash",
        interpretation="vegan gift under budget",
        constraints_extracted=["tag:vegan", "max_minor:50000"],
        candidates_considered=[{"sku": "GEL-VAN-500", "selected": True}],
    )
    assert plan["plan_digest"].startswith("sha256:")
    assert verify_plan_digest(plan) is True
    # Tampering with an unsigned claim is detectable.
    tampered = dict(plan)
    tampered["interpretation"] = "changed after the fact"
    assert verify_plan_digest(tampered) is False


def test_verifier_labels_agent_plan_as_merchant_asserted():
    from openstore.evidence import SECTION_ORDER, compute_chain, sign_and_anchor
    from reference.merchant import poai_keys

    bundle, jwks = _mint_bundle()
    plan = make_agent_plan(
        model="gemini-2.5-flash",
        interpretation="vegan gift under budget",
        constraints_extracted=["tag:vegan"],
        candidates_considered=[{"sku": "GEL-VAN-500", "selected": True}],
    )
    # Add the claim BEFORE sealing the chain (it must be inside the signed sections).
    bundle["human_intent"]["agent_plan"] = plan
    bundle["chain"] = compute_chain({k: bundle.get(k) for k in SECTION_ORDER})
    sign_and_anchor(bundle, poai_keys.signing_key(), poai_keys.kid(), "2026-08-27T09:15:04Z")

    from openstore.verify import verify_bundle

    res = verify_bundle(bundle, jwks)
    assert "human_intent.agent_plan" in res.merchant_asserted
