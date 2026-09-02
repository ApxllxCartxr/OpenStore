# tests/test_checkout_flow.py
# End-to-end money-path test: policy -> create_checkout -> hold -> ledger.

from __future__ import annotations

import base64
from datetime import UTC, datetime

from openstore.core.api import create_checkout
from openstore.core.webauthn_rp import (
    begin_assertion,
    begin_registration,
    complete_registration,
)
from openstore.models import Checkout, IntentPolicy, LedgerEntry
from sqlmodel import Session, select


def _b64d(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def _seed_policy(
    session: Session,
    *,
    no_human_authority: bool = False,
    webauthn_credential_id: str = "cred",
) -> IntentPolicy:
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
        webauthn_credential_id=webauthn_credential_id,
        webauthn_sign_count=0,
        no_human_authority=no_human_authority,
        signed_at=datetime.now(UTC).replace(tzinfo=None),
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


def _assert_checkout_held(session: Session) -> Checkout:
    checkouts = list(session.exec(select(Checkout)).all())
    assert len(checkouts) == 1
    assert checkouts[0].state.value == "HELD"

    reserves = list(
        session.exec(select(LedgerEntry).where(LedgerEntry.reference_id == checkouts[0].id)).all()
    )
    assert len(reserves) == 2  # double-entry: customer_hold + merchant_pending
    return checkouts[0]


def test_create_checkout_happy_path_aal0(settings, session):
    """AAL0 (no_human_authority) authorizes the agent to transact autonomously:
    no per-cart WebAuthn assertion is required (compiler check 0 passes)."""
    _seed_policy(session, no_human_authority=True)

    result = create_checkout(
        config=settings,
        session=session,
        trace_id="trace_aal0",
        client_id="client_1",
        merchant_id="m_test",
        cart_items=[
            {"sku": "SKU_A", "qty": 1, "unit_minor": 10000, "tags": [], "campaign_id": None}
        ],
        cart_hash="hash_1",
        cart_version=1,
        policy_id="pol_active",
    )

    assert result.allowed is True, f"checkout should be allowed: {result.reason_code}"
    assert result.aal_level == 0
    _assert_checkout_held(session)


def test_create_checkout_happy_path_webauthn(settings, session):
    """AAL1+ (no_human_authority=False) still requires a valid WebAuthn assertion;
    with a real (virtual-authenticator) assertion the checkout is allowed."""
    _seed_policy(session, no_human_authority=False)

    from cryptography.hazmat.primitives.asymmetric import ec
    from openstore.devtools.virtual_authenticator import VirtualAuthenticator

    ec_key = ec.generate_private_key(ec.SECP256R1())
    cred_id = b"E" + b"W" * 42
    rp_id = settings.webauthn.rp_id
    origin = settings.webauthn.origin
    va = VirtualAuthenticator(
        credential_id=cred_id, key=ec_key, rp_id=rp_id, origin=origin, sign_count=3
    )

    # Enrol the credential for this merchant (user_handle == merchant_id).
    reg_options = begin_registration(settings, "m_test", "tester", "Tester")
    challenge = reg_options["challenge"]
    reg = va.register(_b64d(challenge))
    complete_registration(
        session=session,
        config=settings,
        user_handle="m_test",
        credential_id=reg.credential_id,
        client_data_json=reg.client_data_json,
        attestation_object=reg.attestation_object,
        challenge_b64url=challenge,
    )

    begin = begin_assertion(settings, "m_test", binding={"mode": "cart", "cart_hash": "hash_w"})
    achal = begin["challenge"]
    asr = va.assert_credential(_b64d(achal), sign_count=4)

    result = create_checkout(
        config=settings,
        session=session,
        trace_id="trace_webauthn",
        client_id="client_1",
        merchant_id="m_test",
        cart_items=[
            {"sku": "SKU_A", "qty": 1, "unit_minor": 10000, "tags": [], "campaign_id": None}
        ],
        cart_hash="hash_w",
        cart_version=1,
        policy_id="pol_active",
        webauthn_assertion={
            "credential_id": asr.credential_id,
            "client_data_json": asr.client_data_json,
            "authenticator_data": asr.authenticator_data,
            "signature": asr.signature,
            "challenge": achal,
        },
    )

    assert result.allowed is True, f"checkout should be allowed: {result.reason_code}"
    assert result.aal_level >= 1
    _assert_checkout_held(session)
