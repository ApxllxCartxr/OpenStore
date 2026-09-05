# tests/stage05/test_inv4_recovery.py
# Stage 5 — INV-4 kill-recovery proof: a crash after the first commit must leave a
# recoverable PENDING intent, and the recovery path must adopt the existing Razorpay
# payment link by reference_id rather than creating a second one.

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import (
    get_or_create_checkout,
    init_database,
    update_checkout_state,
)
from openstore.core.idempotency import compute_request_hash, generate_idempotency_key
from openstore.core.ledger import create_reserve_entry
from openstore.models import Checkout, IdempotencyKey, OrderState
from openstore.psp import razorpay_driver as driver

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"

WEBHOOK_SECRET = "whsec_test_replay"

COUNTER = 0


def _config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant"),
        razorpay=RazorpayConfig(
            key_id="rzp_test_xxxxxxxxxxxxxxxx",
            key_secret="test_secret",
            webhook_secret=WEBHOOK_SECRET,
        ),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def psp_session():
    import openstore.core.database as db_mod
    from openstore.core.database import get_session

    db_mod._engine = None
    init_database(_config())
    session = get_session(_config())
    yield session
    session.close()


def _make_held_checkout(session, *, link_id: str, amount_minor: int = 21000) -> Checkout:
    checkout, _ = get_or_create_checkout(
        session=session,
        checkout_id=f"chk_{link_id}",
        trace_id=f"trace_{link_id}",
        client_id="oc_test",
        merchant_id="gelateria-roma",
        cart_hash=f"cart_{link_id}",
        cart_version=1,
        amount_minor=amount_minor,
        currency="INR",
        policy_id=None,
        policy_hash=None,
        aal_level=2,
        expires_at=datetime.now(UTC),
        idempotency_key=f"idem_{link_id}",
        cart_snapshot={"items": []},
    )
    create_reserve_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount_minor,
        currency="INR",
    )
    update_checkout_state(
        session=session,
        checkout_id=checkout.id,
        new_state=OrderState.HELD,
        psp_payment_link_id=link_id,
    )
    session.commit()
    return checkout


def _seed_pending_intent(
    session, *, checkout_id: str, trace_id: str, client_id: str
) -> IdempotencyKey:
    """Simulate the first INV-4 commit: IdempotencyKey(IN_FLIGHT, response_status=0).

    A crash after BEGIN/write/PspIntent(PENDING)/COMMIT leaves exactly this row
    recoverable; matching request_hash means the retry is the same operation.
    """
    request_body = {
        "checkout_id": checkout_id,
        "amount_minor": 21000,
        "currency": "INR",
        "description": "",
    }
    idem = IdempotencyKey(
        key=generate_idempotency_key("razorpay_create", trace_id, client_id, checkout_id),
        trace_id=trace_id,
        client_id=client_id,
        request_hash=compute_request_hash(request_body),
        response_status=0,
        response_body={},
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    session.add(idem)
    session.commit()
    return idem


def _duplicate_error() -> Exception:
    dup = Exception("duplicate reference")
    dup.code = driver.RAZORPAY_DUPLICATE_REFERENCE_ID_ERROR_CODE  # type: ignore[attr-defined]
    return dup


def test_crash_mid_create_recovers_by_adopting_existing_link(psp_session):
    """A retry after a crash mid-create must adopt the existing link, never create a second."""
    checkout = _make_held_checkout(psp_session, link_id="plink_CRASH_MID")

    trace_id = "trace_crash_mid"
    client_id = "oc_test"
    idem = _seed_pending_intent(
        psp_session, checkout_id=checkout.id, trace_id=trace_id, client_id=client_id
    )

    # The first create DID reach Razorpay before the crash: the link exists under
    # reference_id=checkout.id. The PSP rejects the retry's duplicate create with the
    # pinned code, and the reference_id index resolves it.
    existing_link = {
        "id": "plink_ALREADY_EXISTS",
        "reference_id": checkout.id,
        "status": "created",
        "short_url": "https://rzp.io/i/existing",
    }
    mock_client = MagicMock()
    mock_client.payment_link.create.side_effect = _duplicate_error()
    mock_client.payment_link.all.return_value = {"items": [existing_link]}

    recovered = driver.create_payment_link(
        config=_config(),
        session=psp_session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout.id,
        amount_minor=21000,
        currency="INR",
        mock_razorpay=mock_client,
    )
    psp_session.commit()

    # INV-4: the existing Razorpay object is adopted by reference_id, not a new one.
    assert recovered.id == checkout.id
    psp_session.refresh(recovered)
    assert recovered.psp_payment_link_id == "plink_ALREADY_EXISTS"
    assert recovered.psp_payment_link_id != "plink_CRASH_MID"
    assert recovered.psp_order_id == checkout.id

    # The idempotency record moved IN_FLIGHT -> COMPLETED with the adopted link.
    psp_session.refresh(idem)
    assert idem.response_status == 200
    assert idem.response_body["psp_payment_link_id"] == "plink_ALREADY_EXISTS"

    # Recovery consulted the reference_id index exactly once and did not loop.
    mock_client.payment_link.create.assert_called_once()
    mock_client.payment_link.all.assert_called_once()


def test_recovery_rejects_when_reference_id_not_findable(psp_session):
    """When the duplicate-code arrives but no link resolves by reference_id, fail loud."""
    checkout = _make_held_checkout(psp_session, link_id="plink_CRASH_UNRECOVERABLE")
    trace_id = "trace_crash_unrec"
    client_id = "oc_test"
    _seed_pending_intent(
        psp_session, checkout_id=checkout.id, trace_id=trace_id, client_id=client_id
    )

    mock_client = MagicMock()
    mock_client.payment_link.create.side_effect = _duplicate_error()
    mock_client.payment_link.all.return_value = {"items": []}

    with pytest.raises(driver.RazorpayError) as ei:
        driver.create_payment_link(
            config=_config(),
            session=psp_session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout.id,
            amount_minor=21000,
            currency="INR",
            mock_razorpay=mock_client,
        )
    assert ei.value.error_code == "psp.duplicate_unrecoverable"


def test_idempotent_replay_restores_short_url_and_cancel_token(psp_session):
    """Bug (S14): a second create_payment_link call with the SAME
    (trace_id, client_id, checkout_id) — a legitimate idempotent replay, not
    a crash — used to restore only psp_order_id/psp_payment_link_id from
    the cached response, silently dropping short_url and cancel_token. The
    checkout came back HELD with "success" and no way to pay or cancel."""
    checkout = _make_held_checkout(psp_session, link_id="plink_REPLAY")
    trace_id = "trace_replay"
    client_id = "oc_test"

    mock_client = MagicMock()
    mock_client.payment_link.create.return_value = {
        "id": "plink_REPLAY_REAL",
        "reference_id": checkout.id,
        "status": "created",
        "short_url": "https://rzp.io/i/replay_real",
    }

    first = driver.create_payment_link(
        config=_config(),
        session=psp_session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout.id,
        amount_minor=21000,
        currency="INR",
        mock_razorpay=mock_client,
    )
    psp_session.commit()
    assert first.short_url == "https://rzp.io/i/replay_real"
    assert first.cancel_token

    # Same trace_id/client_id/checkout_id/amount/currency -> same idempotency
    # key -> hits the response_status==200 replay branch, NOT a fresh create.
    replayed = driver.create_payment_link(
        config=_config(),
        session=psp_session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout.id,
        amount_minor=21000,
        currency="INR",
        mock_razorpay=mock_client,
    )
    psp_session.commit()

    mock_client.payment_link.create.assert_called_once()  # replay never re-hit Razorpay
    assert replayed.short_url == "https://rzp.io/i/replay_real"
    assert replayed.cancel_token == first.cancel_token
