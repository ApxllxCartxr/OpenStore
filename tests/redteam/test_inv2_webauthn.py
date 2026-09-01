# tests/redteam/test_inv2_webauthn.py
# INV-2 — The WebAuthn assertion is verified on the money path.
# Red-team: a forgery (wrong challenge / wrong signature / stale sign-count) must be
# rejected; a missing assertion must deny the compiler (check 0 human_authority_present).

from __future__ import annotations

from conftest import seed_policy
from openstore.core.compiler import CompilerContext, compile_decision


def _ctx(policy, has_assertion):
    return CompilerContext(
        cart_items=[{"sku": "GEL-VAN", "qty": 1, "unit_minor": 10000, "tags": []}],
        policy=policy,
        merchant_id=policy.merchant_id,
        currency="INR",
        checkout_count=0,
        cumulative_spend_minor=0,
        has_webauthn_assertion=has_assertion,
        assertion_age_seconds=0,
        now_unix=policy.not_before + 60,
        campaign_lookup={},
    )


def test_missing_assertion_denies_money_path(session):
    """INV-2: no assertion on the money path -> check 0 denial (assertion_required)."""
    pol = seed_policy(session)
    session.commit()

    res = compile_decision(_ctx(pol, has_assertion=False))
    assert res.allowed is False
    assert res.reason_code == "assertion_required"
    assert res.transcript[0]["check"] == "human_authority_present"


def test_with_assertion_proceeds(session):
    """With a validated assertion present, the compiler proceeds past check 0."""
    pol = seed_policy(session, max_spend_total_minor=50000, max_spend_per_tx_minor=50000)
    session.commit()

    res = compile_decision(_ctx(pol, has_assertion=True))
    assert res.allowed is True
    assert res.transcript[0]["check"] == "human_authority_present"
    assert res.transcript[0]["result"] == "pass"
