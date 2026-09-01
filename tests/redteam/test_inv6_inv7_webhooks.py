# tests/redteam/test_inv6_inv7_webhooks.py
# INV-6 — Webhooks assume at-least-once, out-of-order, occasionally never.
#   Signature is verified; a replay is idempotent; a lost event is reconciled.
# INV-7 — Reconciliation (sweeper) because webhooks get lost.
# Red-team: forged/replayed/duplicate webhooks must not double-charge or forgery-charge.

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from conftest import seed_policy
from openstore.core.webhooks import (
    process_webhook_event,
    process_webhook_retry_queue,
    verify_razorpay_signature,
)
from openstore.models import WebhookEvent, WebhookStatus


def _sig(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_signature_verification_happy(session):
    payload = b'{"event_id":"evt_1"}'
    secret = "s3cr3t"
    assert verify_razorpay_signature(payload, _sig(payload, secret), secret) is True


def test_signature_verification_wrong_secret_fails(session):
    payload = b'{"event_id":"evt_1"}'
    assert verify_razorpay_signature(payload, "deadbeef", "wrong") is False


def test_signature_verification_wrong_payload_fails(session):
    """Tampering with the payload invalidates the signature (INV-6)."""
    payload = b'{"event_id":"evt_1","amount":100}'
    secret = "s3cr3t"
    good = _sig(payload, secret)
    # Attacker swaps the amount but reuses the captured signature.
    tampered = b'{"event_id":"evt_1","amount":999999}'
    assert verify_razorpay_signature(tampered, good, secret) is False


def test_duplicate_event_is_idempotent(session):
    """INV-6: the same psp_event_id delivered twice processes ONCE."""
    seed_policy(session)
    session.commit()

    payload = {"payload": {"id": "pay_1"}}
    calls: list[dict] = []

    def proc(s, payload):
        calls.append(payload)

    e1 = process_webhook_event(session, "razorpay", "evt_dup", "payment.captured",
                               payload, "tr_1", "cli_1", proc)
    e2 = process_webhook_event(session, "razorpay", "evt_dup", "payment.captured",
                               payload, "tr_1", "cli_1", proc)
    session.commit()

    assert e1.id == e2.id
    assert e1.status == WebhookStatus.COMPLETED
    assert len(calls) == 1


def test_duplicate_with_different_payload_not_reprocessed(session):
    """INV-6: a replay with a DIFFERENT payload must not reprocess the event."""
    payload_a = {"payload": {"id": "pay_1", "amount": 100}}
    events = []

    def proc(s, payload):
        events.append(payload)

    e1 = process_webhook_event(session, "razorpay", "evt_x", "payment.captured",
                               payload_a, "tr_1", "cli_1", proc)
    session.commit()

    payload_b = {"payload": {"id": "pay_1", "amount": 999999}}
    e2 = process_webhook_event(session, "razorpay", "evt_x", "payment.captured",
                               payload_b, "tr_1", "cli_1", proc)
    session.commit()

    assert e1.id == e2.id
    assert len(events) == 1


def test_sweeper_retries_due_failed_event(session):
    """INV-7: a failed, due event is reconciled by the sweeper."""
    from openstore.core.ledger import create_reserve_entry
    from openstore.models import Checkout, OrderState

    pol = seed_policy(session, max_spend_total_minor=50000, max_spend_per_tx_minor=50000)
    now = datetime.now(UTC)
    ck = Checkout(
        id="chk_web", trace_id="tr_web", client_id="cli_1", merchant_id="gelateria-milano",
        cart_hash="h_web", cart_version=1, amount_minor=40000, currency="INR",
        state=OrderState.HELD, policy_id=pol.id, policy_hash="ph_web", aal_level=2,
        expires_at=now + timedelta(hours=1), idempotency_key="idem_web",
        psp_order_id="order_web", cart_snapshot={"items": []},
        created_at=now - timedelta(minutes=1), updated_at=now - timedelta(minutes=1),
    )
    session.add(ck)
    create_reserve_entry(session, "tr_web", "cli_1", "chk_web", 40000)
    session.commit()

    evt = WebhookEvent(
        trace_id="tr_web", client_id="cli_1", psp_provider="razorpay",
        psp_event_id="evt_lost", event_type="payment.captured",
        payload={
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_web", "order_id": "order_web",
                        "amount": 40000, "currency": "INR", "status": "captured",
                    }
                }
            }
        },
        status=WebhookStatus.FAILED, retry_count=1,
        processed_at=datetime.now(UTC) - timedelta(seconds=400),  # > 300s backoff
    )
    session.add(evt)
    session.commit()

    count = process_webhook_retry_queue(session)
    session.commit()
    assert count == 1
    evt2 = session.get(WebhookEvent, evt.id)
    assert evt2.status == WebhookStatus.COMPLETED
    state = session.get(Checkout, "chk_web").state
    assert state == OrderState.RELEASED


def test_sweeper_skips_event_not_yet_due(session):
    """INV-7: backoff is respected; an event not yet due is NOT retried."""
    evt = WebhookEvent(
        trace_id="tr_1", client_id="cli_1", psp_provider="razorpay",
        psp_event_id="evt_recent", event_type="payment.failed",
        payload={"payload": {"id": "pay_recent"}},
        status=WebhookStatus.FAILED, retry_count=1,
        processed_at=datetime.now(UTC) - timedelta(seconds=10),  # < 300s backoff
    )
    session.add(evt)
    session.commit()

    count = process_webhook_retry_queue(session)
    session.commit()
    assert count == 0
    evt2 = session.get(WebhookEvent, evt.id)
    assert evt2.status == WebhookStatus.FAILED


def test_sweeper_stops_at_max_retries(session):
    """INV-7: an event at MAX_RETRIES is not retried endlessly."""
    from openstore.core.webhooks import MAX_RETRIES

    evt = WebhookEvent(
        trace_id="tr_1", client_id="cli_1", psp_provider="razorpay",
        psp_event_id="evt_max", event_type="payment.captured",
        payload={"payload": {"id": "pay_max"}},
        status=WebhookStatus.FAILED, retry_count=MAX_RETRIES,
        processed_at=datetime.now(UTC) - timedelta(seconds=99999),
    )
    session.add(evt)
    session.commit()

    count = process_webhook_retry_queue(session)
    session.commit()
    assert count == 0
