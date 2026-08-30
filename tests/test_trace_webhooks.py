"""Tests that the Discord tracing webhooks are wired into the request paths.

These exercise the wiring (channels + call sites) without hitting a real
Discord server: ``openstore.trace.emit_later`` / ``emit_background`` are
swapped for a recorder.
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlmodel import Session

from openstore.ledger import Ledger
from openstore.runtime import MerchantRuntime
from openstore.server import create_http_app
from openstore.gateway import FakeGateway
from openstore.models import PspIntent


SECRET = "test-secret-123"
REF = "pay_TRACE1"
CHECKOUT = "TRACE1"
ORDER = "O_TRACE1"


@pytest.fixture
def captured(monkeypatch):
    seen = []

    def fake(channel, title, fields, trace_id, level="info"):
        seen.append((channel, title, level))

    monkeypatch.setattr("openstore.trace.emit_later", fake)
    monkeypatch.setattr("openstore.trace.emit_background", fake)
    return seen


def _signed_body(event_type, ref, pay_id, created_at):
    import hmac
    import hashlib
    body = json.dumps({
        "event": event_type,
        "created_at": created_at,
        "payload": {"payment": {"reference_id": ref, "id": pay_id}},
    }).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, sig


def _seed(rt):
    with Session(rt.ledger.engine) as s:
        s.add(PspIntent(
            checkout_id=CHECKOUT, order_id=ORDER, amount_minor=21000,
            currency="INR", reference_id=REF, state="CREATED",
            created_at=int(time.time()), updated_at=int(time.time()),
        ))
        s.commit()


def _app():
    key = Ed25519PrivateKey.generate()
    rt = MerchantRuntime(
        merchant_signing_key=key, legal_name="T", country="IN",
        per_txn_limit=100000, daily_limit=1000000, catalog={},
        policy=None, gateway=FakeGateway(),
    )
    return rt, create_http_app(rt)


def test_webhook_emits_to_agent_and_audit_channels(captured, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    rt, app = _app()
    _seed(rt)
    client = TestClient(app)
    body, sig = _signed_body("payment.captured", REF, "pay_xyz", int(time.time()))
    r = client.post(
        "/webhooks/razorpay", content=body,
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "evt-t1"},
    )
    assert r.status_code == 200

    channels = {c for c, _, _ in captured}
    assert "audit-trail" in channels
    assert "merchant-agent" in channels
    assert "buyer-agent" in channels


def test_rejection_emits_to_merchant_and_audit_channels(captured):
    rt, _ = _app()
    rt.record_rejection("O_X", "policy.spend_cap_exceeded", "over limit", "client1")

    channels = {c for c, _, _ in captured}
    assert "merchant-server" in channels
    assert "audit-trail" in channels
