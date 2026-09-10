# OpenStore core — webhook processing (INV-6)

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from openstore.core.ledger import create_capture_entry, create_release_entry
from openstore.models import Checkout, OrderState, WebhookEvent, WebhookStatus


class WebhookError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


MAX_RETRIES = 5
RETRY_DELAYS = [60, 300, 900, 3600, 21600]  # 1m, 5m, 15m, 1h, 6h


def verify_razorpay_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Razorpay webhook signature."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def process_webhook_event(
    session: Session,
    psp_provider: str,
    psp_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str,
    client_id: str,
    processor: Callable[[Session, dict[str, Any]], None],
) -> WebhookEvent:
    """
    INV-6: Process webhook with at-least-once delivery guarantee.

    - Deduplicates by psp_event_id (idempotent)
    - Stores event for audit/replay
    - Calls processor which MUST be idempotent
    - Retries on failure with exponential backoff
    """
    # Check for duplicate (idempotency)
    existing = session.exec(
        select(WebhookEvent).where(
            WebhookEvent.psp_provider == psp_provider,
            WebhookEvent.psp_event_id == psp_event_id
        )
    ).first()

    if existing:
        if existing.status == WebhookStatus.COMPLETED:
            return existing
        # If previously failed, allow retry
        if existing.status == WebhookStatus.FAILED:
            existing.status = WebhookStatus.PROCESSING
            existing.retry_count += 1
            session.add(existing)
            session.flush()
            event = existing
        else:
            # Currently processing - return existing
            return existing
    else:
        # New event
        event = WebhookEvent(
            trace_id=trace_id,
            client_id=client_id,
            psp_provider=psp_provider,
            psp_event_id=psp_event_id,
            event_type=event_type,
            payload=payload,
            status=WebhookStatus.PROCESSING,
            retry_count=0,
        )
        session.add(event)
        session.flush()

    try:
        # Call the processor (must be idempotent)
        processor(session, payload)

        event.status = WebhookStatus.COMPLETED
        event.processed_at = datetime.now(UTC).replace(tzinfo=None)
        event.last_error = None
        session.add(event)
        session.flush()

    except Exception as e:
        event.status = WebhookStatus.FAILED
        event.last_error = str(e)
        session.add(event)
        session.flush()
        raise

    return event


def handle_razorpay_payment_captured(session: Session, payload: dict[str, Any]) -> None:
    """
    INV-6: Processor for Razorpay payment.captured webhook.

    Must be idempotent - safe to call multiple times for same payment.
    """
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not payment:
        raise WebhookError("webhook.invalid_payload", "Missing payment entity in payload")

    payment_id = payment.get("id")
    order_id = payment.get("order_id")
    amount = payment.get("amount")  # in paise
    currency = payment.get("currency", "INR")
    status = payment.get("status")

    if status != "captured":
        # Only process captured payments
        return

    if not all([payment_id, order_id, amount]):
        raise WebhookError("webhook.invalid_payload", "Missing required payment fields")

    # Find checkout by psp_order_id
    checkout = session.exec(
        select(Checkout).where(Checkout.psp_order_id == order_id)
    ).first()

    if not checkout:
        # Order not found - log but don't fail (may be race condition)
        raise WebhookError("checkout.not_found", f"No checkout found for order_id: {order_id}")

    if checkout.state != OrderState.HELD:
        # Already processed or wrong state - idempotent: just return
        return

    # Verify amount matches
    if checkout.amount_minor != amount:
        raise WebhookError(
            "webhook.amount_mismatch",
            f"Payment amount {amount} != checkout amount {checkout.amount_minor}"
        )

    # Verify currency
    if checkout.currency != currency:
        raise WebhookError(
            "webhook.currency_mismatch",
            f"Payment currency {currency} != checkout currency {checkout.currency}"
        )

    # Create CAPTURE ledger entry (idempotent via ledger idempotency key)
    create_capture_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount,
        currency=currency,
        description=f"Razorpay payment captured: {payment_id}",
    )

    # Update checkout state
    checkout.state = OrderState.RELEASED
    checkout.paid_at = datetime.now(UTC).replace(tzinfo=None)
    checkout.released_at = datetime.now(UTC).replace(tzinfo=None)
    checkout.psp_payment_link_id = payment_id
    checkout.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(checkout)
    session.flush()


def handle_razorpay_payment_failed(session: Session, payload: dict[str, Any]) -> None:
    """
    INV-6: Processor for Razorpay payment.failed webhook.

    Releases hold, returns funds to customer.
    """
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not payment:
        raise WebhookError("webhook.invalid_payload", "Missing payment entity in payload")

    order_id = payment.get("order_id")
    amount = payment.get("amount")
    currency = payment.get("currency", "INR")

    if not all([order_id, amount]):
        raise WebhookError("webhook.invalid_payload", "Missing required payment fields")

    checkout = session.exec(
        select(Checkout).where(Checkout.psp_order_id == order_id)
    ).first()

    if not checkout:
        raise WebhookError("checkout.not_found", f"No checkout found for order_id: {order_id}")

    if checkout.state not in (OrderState.CREATED, OrderState.HELD):
        return  # Already processed

    # Release hold
    create_release_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount,
        currency=currency,
        description=f"Razorpay payment failed: {payment.get('id')}",
    )

    checkout.state = OrderState.CANCELLED
    checkout.cancelled_at = datetime.now(UTC).replace(tzinfo=None)
    checkout.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(checkout)
    session.flush()


def process_webhook_retry_queue(session: Session) -> int:
    """
    INV-7: Reconciliation sweeper for failed webhooks.

    Processes failed webhooks that are due for retry.
    """
    from sqlmodel import and_

    now = datetime.now(UTC).replace(tzinfo=None)
    failed_events = session.exec(
        select(WebhookEvent).where(
            and_(
                WebhookEvent.status == WebhookStatus.FAILED,
                WebhookEvent.retry_count < MAX_RETRIES,
            )
        )
    ).all()

    processed = 0
    for event in failed_events:
        if event.retry_count >= len(RETRY_DELAYS):
            continue

        # Check if enough time has passed since last attempt
        if event.processed_at:
            delay = RETRY_DELAYS[min(event.retry_count, len(RETRY_DELAYS) - 1)]
            if (now - event.processed_at).total_seconds() < delay:
                continue
        elif event.created_at:
            delay = RETRY_DELAYS[min(event.retry_count, len(RETRY_DELAYS) - 1)]
            if (now - event.created_at).total_seconds() < delay:
                continue

        # Retry
        event.status = WebhookStatus.PROCESSING
        session.add(event)
        session.flush()

        try:
            # Re-process based on event type
            if event.psp_provider == "razorpay":
                if event.event_type == "payment.captured":
                    handle_razorpay_payment_captured(session, event.payload)
                elif event.event_type == "payment.failed":
                    handle_razorpay_payment_failed(session, event.payload)

            event.status = WebhookStatus.COMPLETED
            event.processed_at = datetime.now(UTC).replace(tzinfo=None)
            event.last_error = None
            processed += 1
        except Exception as e:
            event.status = WebhookStatus.FAILED
            event.last_error = str(e)
            event.retry_count += 1

        session.add(event)
        session.flush()

    return processed
