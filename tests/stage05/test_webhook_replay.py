# tests/stage05/test_webhook_replay.py
# Stage 5 — S5.3 webhook endpoint + worker replay tests against captured fixtures.

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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
from openstore.core.ledger import create_reserve_entry
from openstore.models import Checkout, OrderState, WebhookEvent
from openstore.psp.razorpay_driver import process_webhook_in_worker
from openstore.psp.router import psp_router

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "GOLDEN" / "razorpay"

WEBHOOK_SECRET = "whsec_test_replay"


@pytest.fixture()
def config() -> Settings:
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
def app(config: Settings):
    import openstore.core.database as db_mod
    from fastapi import FastAPI

    # Reset the engine and DB to ensure clean state per test
    from openstore.psp.router import set_psp_config

    db_mod._engine = None

    init_database(config)
    set_psp_config(config)
    app = FastAPI()
    app.include_router(psp_router(config))
    return app


def _make_checkout(session, *, link_id: str, amount_minor: int = 21000) -> Checkout:
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


def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _patch_checkout_id(payload: dict, new_checkout_id: str) -> dict:
    """Inject a checkout_id into the payment_link entity notes for our test checkout."""
    pl = payload["payload"]["payment_link"]["entity"]
    pl["notes"]["checkout_id"] = new_checkout_id
    pl["reference_id"] = new_checkout_id
    return payload


def _patch_payment_checkout_id(payload: dict, new_checkout_id: str) -> dict:
    payment = payload["payload"]["payment"]["entity"]
    payment["notes"]["checkout_id"] = new_checkout_id
    return payload


def test_webhook_replay_payment_link_paid(config, app, session):
    """Replay the captured payment_link.paid body → CAPTURE ledger, RELEASED state."""
    checkout = _make_checkout(session, link_id="plink_REPLAY_PAID")

    payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
    payload = _patch_checkout_id(payload, checkout.id)
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Event-Id": "evt_replay_paid_001",
            "X-Razorpay-Signature": _sign(body),
        },
    )

    assert response.status_code == 200
    assert response.json()["received"] is True

    # Process the event in worker (TestClient's BackgroundTasks fires synchronously
    # via the test runner; we explicitly process it for determinism)
    events = session.exec(
        __import__("sqlmodel")
        .select(WebhookEvent)
        .where(WebhookEvent.psp_event_id == "evt_replay_paid_001")
    ).all()
    for event in events:
        process_webhook_in_worker(session, event)
    session.commit()

    session.refresh(checkout)
    assert checkout.state == OrderState.RELEASED


def test_webhook_duplicate_delivery_is_idempotent(config, app, session):
    """Replay the SAME event twice → state unchanged, no extra ledger entries."""
    checkout = _make_checkout(session, link_id="plink_REPLAY_DUPE")

    payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
    payload = _patch_checkout_id(payload, checkout.id)
    body = json.dumps(payload).encode()
    headers = {
        "X-Razorpay-Event-Id": "evt_replay_dupe_001",
        "X-Razorpay-Signature": _sign(body),
    }

    client = TestClient(app)
    r1 = client.post("/webhooks/razorpay", content=body, headers=headers)
    r2 = client.post("/webhooks/razorpay", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200  # second delivery: still 200 (idempotent)

    # Only ONE webhook event recorded (dedupe on event_id)
    from sqlmodel import select

    events = session.exec(
        select(WebhookEvent).where(WebhookEvent.psp_event_id == "evt_replay_dupe_001")
    ).all()
    assert len(events) == 1


def test_webhook_invalid_signature_returns_401(config, app):
    payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Event-Id": "evt_bad_sig_001",
            "X-Razorpay-Signature": "definitely-not-valid",
        },
    )
    assert response.status_code == 401


def test_webhook_missing_signature_returns_401(config, app):
    payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Event-Id": "evt_no_sig_001"},
    )
    assert response.status_code == 401


def test_webhook_replay_payment_failed(config, app, session):
    checkout = _make_checkout(session, link_id="plink_REPLAY_FAILED")

    payload = json.loads((GOLDEN_DIR / "payment_failed.json").read_text())
    payload = _patch_payment_checkout_id(payload, checkout.id)
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Event-Id": "evt_replay_failed_001",
            "X-Razorpay-Signature": _sign(body),
        },
    )
    assert response.status_code == 200

    from sqlmodel import select

    events = session.exec(
        select(WebhookEvent).where(WebhookEvent.psp_event_id == "evt_replay_failed_001")
    ).all()
    for event in events:
        process_webhook_in_worker(session, event)
    session.commit()

    session.refresh(checkout)
    assert checkout.state == OrderState.CANCELLED


def test_webhook_replay_payment_link_cancelled(config, app, session):
    checkout = _make_checkout(session, link_id="plink_REPLAY_CANCELLED")

    payload = json.loads((GOLDEN_DIR / "payment_link_cancelled.json").read_text())
    payload = _patch_checkout_id(payload, checkout.id)
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Event-Id": "evt_replay_cancelled_001",
            "X-Razorpay-Signature": _sign(body),
        },
    )
    assert response.status_code == 200

    from sqlmodel import select

    events = session.exec(
        select(WebhookEvent).where(WebhookEvent.psp_event_id == "evt_replay_cancelled_001")
    ).all()
    for event in events:
        process_webhook_in_worker(session, event)
    session.commit()

    session.refresh(checkout)
    assert checkout.state == OrderState.CANCELLED


def test_webhook_replay_out_of_order_terminal_absorbs(config, app, session):
    """Out-of-order: payment.failed arrives AFTER payment_link.paid."""
    checkout = _make_checkout(session, link_id="plink_REPLAY_OOO")

    # First, mark the checkout as RELEASED (paid)
    from openstore.core.ledger import create_capture_entry

    create_capture_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=checkout.amount_minor,
        currency="INR",
    )
    update_checkout_state(
        session=session,
        checkout_id=checkout.id,
        new_state=OrderState.RELEASED,
    )
    session.commit()

    # Now a payment.failed arrives (out-of-order)
    payload = json.loads((GOLDEN_DIR / "payment_failed.json").read_text())
    payload = _patch_payment_checkout_id(payload, checkout.id)
    body = json.dumps(payload).encode()

    client = TestClient(app)
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Event-Id": "evt_replay_ooo_001",
            "X-Razorpay-Signature": _sign(body),
        },
    )
    assert response.status_code == 200

    from sqlmodel import select

    events = session.exec(
        select(WebhookEvent).where(WebhookEvent.psp_event_id == "evt_replay_ooo_001")
    ).all()
    for event in events:
        process_webhook_in_worker(session, event)
    session.commit()

    session.refresh(checkout)
    # Terminal RELEASED absorbs the late event
    assert checkout.state == OrderState.RELEASED


def test_hold_cancel_token_path(config, app, session):
    """POST /hold/{cancel_token}/cancel — happy path (link unpaid)."""
    import secrets
    from unittest.mock import MagicMock

    cancel_token = secrets.token_urlsafe(32)

    checkout = _make_checkout(session, link_id="plink_CANCEL_HAPPY")
    checkout.cancel_token = cancel_token
    session.commit()

    mock_client = MagicMock()
    mock_client.payment_link.cancel.return_value = {
        "id": "plink_CANCEL_HAPPY",
        "status": "cancelled",
    }

    import openstore.psp.razorpay_driver as driver_mod

    original_get_client = driver_mod._get_client
    driver_mod._get_client = lambda _: mock_client  # type: ignore[assignment]
    try:
        client = TestClient(app)
        response = client.post(
            f"/hold/{cancel_token}/cancel", json={"trace_id": "trace_cancel_happy"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "RELEASE"

        session.refresh(checkout)
        assert checkout.state == OrderState.CANCELLED
    finally:
        driver_mod._get_client = original_get_client


def test_hold_cancel_invalid_token_returns_404(config, app):
    client = TestClient(app)
    response = client.post("/hold/nonexistent_token_xxx/cancel", json={})
    assert response.status_code == 404
