# tests/test_compiler_check0.py
# Q-004 / §3.2: check 0 human_authority_present runs before checks 1-12 and
# returns assertion_required (never raises) when no valid WebAuthn assertion
# accompanies the cart.

from __future__ import annotations

from datetime import datetime

from openstore.core.compiler import CompilerContext, CompilerResult, compile_decision
from openstore.models import IntentPolicy

AGGREGATE = 4102444800  # far-future Unix seconds


def _policy() -> IntentPolicy:
    return IntentPolicy(
        id="pol",
        merchant_id="m_test",
        policy_version=2,
        policy_hash="hash",
        currency="INR",
        max_spend_per_tx_minor=50000,
        max_spend_total_minor=100000,
        max_transactions=10,
        allowed_tags=[],
        tag_mode="all",
        blocked_skus=[],
        not_before=0,
        expires_at=AGGREGATE,
        assertion_max_age_seconds=86400,
        fulfilment_mode="all_or_nothing",
        required_skus=[],
        webauthn_credential_id="cred",
        webauthn_sign_count=0,
        signed_at=datetime.utcnow(),
        is_active=True,
    )


def _ctx(has_assertion: bool) -> CompilerContext:
    return CompilerContext(
        cart_items=[{"sku": "SKU_A", "qty": 1, "unit_minor": 10000, "tags": [], "campaign_id": None}],
        policy=_policy(),
        merchant_id="m_test",
        currency="INR",
        checkout_count=0,
        cumulative_spend_minor=0,
        has_webauthn_assertion=has_assertion,
        assertion_age_seconds=0,
        now_unix=int(datetime.utcnow().timestamp()),
        campaign_lookup={},
    )


def test_missing_assertion_denies_assertion_required():
    result = compile_decision(_ctx(has_assertion=False))
    assert isinstance(result, CompilerResult)
    assert result.allowed is False
    assert result.reason_code == "assertion_required"
    assert result.transcript[0]["check"] == 0
    assert result.transcript[0]["name"] == "human_authority_present"
    assert result.transcript[0]["passed"] is False


def test_present_assertion_passes_check0_and_proceeds():
    result = compile_decision(_ctx(has_assertion=True))
    assert result.transcript[0] == {
        "check": 0,
        "name": "human_authority_present",
        "passed": True,
        "reason_code": None,
        "details": {"has_webauthn_assertion": True},
    }
    # With authority present, the compiler proceeds: this cart passes checks 0-11.
    assert result.allowed is True
    assert result.transcript[-1]["check"] == 12
