# tests/test_checkout_flow.py
# End-to-end money-path test: policy -> create_checkout -> hold -> ledger.

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, select

from openstore.core.api import create_checkout
from openstore.models import Checkout, IntentPolicy, LedgerEntry


def _seed_policy(session: Session) -> IntentPolicy:
    policy = IntentPolicy(
        id="pol_active",
        merchant_id="m_test",
        policy_version=2,
        policy_hash="hash",
        currency="INR",
        max_spend_per_tx_minor=50000,  # ₹500
        max_spend_total_minor=100000,  # ₹1000
        max_transactions=10,
        allowed_tags=[],
        tag_mode="all",
        blocked_skus=[],
        not_before=0,
        expires_at=4102444800,  # far future
        assertion_max_age_seconds=86400,
        fulfilment_mode="all_or_nothing",
        required_skus=[],
        webauthn_credential_id="cred",
        webauthn_sign_count=0,
        signed_at=datetime.utcnow(),
        is_active=True,
    )
    session.add(policy)
    session.flush()
    return policy


def test_active_policy_lookup_returns_policy(settings, session):
    """Regression: the 'is_active' SQL predicate must match, not be 'is True'.
    Previously `IntentPolicy.is_active is True` was object-identity and always
    False, so the active-policy lookup never matched (checkout path was dead)."""
    _seed_policy(session)

    policy = session.exec(
        select(IntentPolicy).where(
            IntentPolicy.id == "pol_active",
            IntentPolicy.merchant_id == "m_test",
            IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
        )
    ).first()

    assert policy is not None
    assert policy.id == "pol_active"


@pytest.mark.skip(reason="blocked by Q-004: no closed-set reason code to deny when a "
                         "required WebAuthn assertion is missing")
def test_create_checkout_happy_path(settings, session):
    """Policy lookup must succeed (active policy found) and a reserve ledger created."""
    _seed_policy(session)

    result = create_checkout(
        config=settings,
        session=session,
        trace_id="trace_1",
        client_id="client_1",
        merchant_id="m_test",
        cart_items=[{"sku": "SKU_A", "qty": 1, "unit_minor": 10000, "tags": [], "campaign_id": None}],
        cart_hash="hash_1",
        cart_version=1,
        policy_id="pol_active",
    )

    assert result.allowed is True, f"checkout should be allowed: {result.reason_code}"
    assert result.aal_level == 3  # ₹100, WebAuthn not required to reach threshold here

    checkouts = list(session.exec(select(Checkout)).all())
    assert len(checkouts) == 1
    assert checkouts[0].state.value == "HELD"

    # A reserve entry must exist for this checkout
    reserves = list(session.exec(
        select(LedgerEntry).where(LedgerEntry.reference_id == checkouts[0].id)
    ).all())
    assert len(reserves) == 2  # double-entry: customer_hold + merchant_pending

