"""Webhook handling + reconciliation (PRODUCTION_READINESS §1.4, §1.5).

Razorpay webhook handler with:
- Signature verification (raw body, HMAC-SHA256, hmac.compare_digest)
- Event ID from X-Razorpay-Event-Id header
- Ordering guard with terminal states
- Reconciliation sweeper with drift metric
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Header, HTTPException, Request
from sqlmodel import Session, select

from openstore.errors import PSPError, PSP_WEBHOOK_SIGNATURE_INVALID, PSP_WEBHOOK_STALE
from openstore.ledger import Ledger
from openstore.models import PspIntent, ReceiptKind, TrustReceipt


# Terminal states that absorb all transitions
TERMINAL_STATES = {"PAID", "REFUNDED", "FAILED", "CANCELLED"}

# Allowed state transitions
ALLOWED_TRANSITIONS = {
    ("CREATED", "PAID"),
    ("CREATED", "FAILED"),
    ("CREATED", "CANCELLED"),
    ("PAID", "REFUNDED"),
    ("PAID", "CANCELLED"),
}


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """Parsed webhook event."""
    event_id: str
    event_type: str
    payload: Dict[str, Any]
    created_at: int
    raw_body: bytes


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """Verify Razorpay webhook signature using HMAC-SHA256."""
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_event_id(headers: Dict[str, str], raw_body: bytes) -> str:
    """Extract event ID from headers or derive from body."""
    event_id = headers.get("x-razorpay-event-id")
    if event_id:
        return event_id
    # Deterministic fallback
    return hashlib.sha256(raw_body).hexdigest()[:32]


def parse_webhook_event(raw_body: bytes, headers: Dict[str, str]) -> WebhookEvent:
    """Parse and validate webhook event."""
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        raise ValueError("invalid_json")

    event_type = data.get("event", "")
    payload = data.get("payload", {})
    created_at = data.get("created_at", int(time.time()))

    event_id = extract_event_id(headers, raw_body)

    return WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        created_at=created_at,
        raw_body=raw_body,
    )


def is_terminal_state(state: str) -> bool:
    """Check if a state is terminal."""
    return state.upper() in TERMINAL_STATES


def is_allowed_transition(from_state: str, to_state: str) -> bool:
    """Check if a state transition is allowed."""
    return (from_state.upper(), to_state.upper()) in ALLOWED_TRANSITIONS


async def handle_webhook(
    request: Request,
    ledger: Ledger,
    webhook_secret: str,
) -> Dict[str, Any]:
    """Handle incoming Razorpay webhook.

    Flow:
    1. Verify signature
    2. Parse event
    3. Persist raw event
    4. Process state transition
    5. Return 200 immediately
    """
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    # 1. Verify signature
    if not verify_webhook_signature(raw_body, signature, webhook_secret):
        raise PSPError(PSP_WEBHOOK_SIGNATURE_INVALID, "Invalid webhook signature")

    # 2. Parse event
    event = parse_webhook_event(raw_body, dict(request.headers))

    # 3. Persist raw event (for replay tooling)
    _persist_raw_event(ledger, event)

    # 4. Process state transition
    await _process_event(ledger, event)

    # 5. Return 200 immediately
    return {"status": "processed", "event_id": event.event_id}


def _persist_raw_event(ledger: Ledger, event: WebhookEvent) -> None:
    """Persist raw webhook event for replay tooling."""
    from openstore.models import WebhookEventRow

    with Session(ledger.engine) as s:
        existing = s.exec(
            select(WebhookEventRow).where(WebhookEventRow.event_id == event.event_id)
        ).first()
        if existing is not None:
            return  # idempotent
        s.add(WebhookEventRow(
            event_id=event.event_id,
            event_type=event.event_type,
            payload_json=json.dumps(event.payload),
            created_at=event.created_at,
        ))
        s.commit()


async def _process_event(ledger: Ledger, event: WebhookEvent) -> None:
    """Process webhook event and update local state."""
    payload = event.payload
    payment_entity = payload.get("payment", {})
    payment_link_entity = payload.get("payment_link", {})

    # Determine the reference ID (our checkout_id)
    reference_id = payment_entity.get("reference_id") or payment_link_entity.get("reference_id")
    if not reference_id:
        return

    # Find the PspIntent
    with Session(ledger.engine) as s:
        psp_intent = s.exec(
            select(PspIntent).where(PspIntent.reference_id == reference_id)
        ).first()

        if not psp_intent:
            return

        # Determine new state from event type
        new_state = _event_type_to_state(event.event_type)
        if not new_state:
            return

        # Check ordering guard
        current_state = psp_intent.state
        if is_terminal_state(current_state):
            if not is_allowed_transition(current_state, new_state):
                # Stale event - ignore but log
                return

        # Check timestamp ordering
        if event.created_at < psp_intent.updated_at:
            # Stale event
            return

        # Update PspIntent
        psp_intent.state = new_state
        psp_intent.psp_response_json = json.dumps(payload)
        psp_intent.updated_at = event.created_at
        psp_intent.attempts += 1

        if new_state == "PAID" and not psp_intent.psp_payment_id:
            psp_intent.psp_payment_id = payment_entity.get("id")

        s.add(psp_intent)
        s.commit()

        # If payment succeeded, create order receipt
        if new_state == "PAID" and psp_intent.order_id:
            await _create_order_receipt(ledger, psp_intent, payment_entity)

        # Trace the transition to the agent/audit channels.
        _trace_transition(event, psp_intent, new_state)


def _trace_transition(event: WebhookEvent, psp_intent: PspIntent, new_state: str) -> None:
    """Best-effort Discord trace of a PSP state transition."""
    from openstore.trace import emit_later

    level = "executed" if new_state in ("PAID", "REFUNDED") else "blocked"
    fields = {
        "checkout_id": psp_intent.checkout_id,
        "order_id": psp_intent.order_id or "-",
        "transition": f"{psp_intent.state} -> {new_state}",
        "event": event.event_type,
    }
    emit_later("audit-trail", "PSP webhook transition", fields, event.event_id, level)
    emit_later("merchant-agent", f"Payment {new_state}", fields, event.event_id, level)
    if new_state == "PAID":
        emit_later("buyer-agent", "Order paid", fields, event.event_id, "executed")


def _event_type_to_state(event_type: str) -> Optional[str]:
    """Map Razorpay event type to our internal state."""
    mapping = {
        "payment.captured": "PAID",
        "payment.failed": "FAILED",
        "payment_link.paid": "PAID",
        "payment_link.expired": "FAILED",
        "payment_link.cancelled": "CANCELLED",
        "refund.created": "REFUNDED",
        "refund.processed": "REFUNDED",
    }
    return mapping.get(event_type)


async def _create_order_receipt(ledger: Ledger, psp_intent: PspIntent, payment_entity: Dict[str, Any]) -> None:
    """Create order receipt when payment is captured."""
    from openstore.models import TrustReceipt

    receipt_id = f"R_{psp_intent.order_id or psp_intent.checkout_id}"

    # Idempotent: the receipt is keyed by payload_ref (checkout_id)
    if ledger.by_payload_ref(psp_intent.checkout_id):
        return

    payment_id = payment_entity.get("id", psp_intent.psp_payment_id or "")
    envelope = {
        "checkout_id": psp_intent.checkout_id,
        "order_id": psp_intent.order_id,
        "payment_id": payment_id,
        "amount_minor": psp_intent.amount_minor,
        "status": "paid",
    }
    receipt = TrustReceipt(
        receipt_id=receipt_id,
        kind=ReceiptKind.ORDER,
        ts=int(time.time()),
        actor_did="",
        envelope=envelope,
        payload_ref=psp_intent.checkout_id,
        statements=[f"payment {payment_id} captured for {psp_intent.checkout_id}"],
    )
    ledger.append(receipt)


# ---------- Reconciliation Sweeper (PRODUCTION_READINESS §1.5) ----------


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Result of a reconciliation run."""
    checked: int
    drift_detected: int
    corrected: int
    errors: List[str]


async def run_reconciliation(
    ledger: Ledger,
    gateway,
    max_age_days: int = 7,
    min_age_minutes: int = 10,
) -> ReconciliationResult:
    """Run reconciliation sweeper.

    For orders in non-terminal states between min_age and max_age,
    query the PSP for the true state and correct local state if needed.

    Returns ReconciliationResult with drift metrics.
    """
    now = int(time.time())
    min_age_ts = now - (min_age_minutes * 60)
    max_age_ts = now - (max_age_days * 86400)

    with Session(ledger.engine) as s:
        # Find PspIntents in non-terminal states within age range
        pending_intents = s.exec(
            select(PspIntent).where(
                PspIntent.state.in_(["PENDING", "CREATED"]),
                PspIntent.created_at >= max_age_ts,
                PspIntent.created_at <= min_age_ts,
            )
        ).all()

    checked = 0
    drift_detected = 0
    corrected = 0
    errors = []

    for intent in pending_intents:
        try:
            checked += 1
            psp_state = await gateway.fetch_payment_status(intent.psp_payment_id or intent.reference_id)

            if psp_state and psp_state != intent.state:
                drift_detected += 1
                # Update local state to match PSP
                with Session(ledger.engine) as s:
                    intent = s.get(PspIntent, intent.id)
                    if intent:
                        intent.state = psp_state
                        intent.updated_at = now
                        s.add(intent)
                        s.commit()
                corrected += 1

        except Exception as e:
            errors.append(f"intent {intent.id}: {e}")

    return ReconciliationResult(
        checked=checked,
        drift_detected=drift_detected,
        corrected=corrected,
        errors=errors,
    )


# ---------- Metrics ----------


reconciliation_drift_total = 0  # Prometheus counter would go here


def record_reconciliation_drift(count: int) -> None:
    """Record reconciliation drift metric."""
    global reconciliation_drift_total
    reconciliation_drift_total += count


# ---------- Replay Tooling ----------


async def replay_webhook(ledger: Ledger, event_id: str, gateway) -> Dict[str, Any]:
    """Replay a webhook event by event_id.

    Used for: python -m openstore.replay <event_id>
    """
    from openstore.models import WebhookEventRow

    with Session(ledger.engine) as s:
        row = s.exec(
            select(WebhookEventRow).where(WebhookEventRow.event_id == event_id)
        ).first()
        if row is None:
            return {"status": "not_found", "event_id": event_id}

        raw_body = json.dumps(json.loads(row.payload_json) if isinstance(row.payload_json, str) else row.payload_json).encode()
        event = WebhookEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            payload=json.loads(row.payload_json) if isinstance(row.payload_json, str) else row.payload_json,
            created_at=row.created_at,
            raw_body=raw_body,
        )
        await _process_event(ledger, event)
        row.processed = True
        s.add(row)
        s.commit()

    return {"status": "replayed", "event_id": event_id, "event_type": row.event_type}