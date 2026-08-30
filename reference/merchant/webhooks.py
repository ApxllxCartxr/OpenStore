import hmac
import hashlib
import json
import uuid

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlmodel import Session, select

from reference.merchant.db import get_session
from reference.merchant.models import WebhookEvent, Order
from reference.merchant.config import settings
from reference.merchant.trace import emit

router = APIRouter()


def verify_razorpay_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, session: Session = Depends(get_session)):
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not verify_razorpay_signature(raw_body, signature, settings.razorpay_webhook_secret):
        emit("merchant-server", "Webhook signature invalid", {}, str(uuid.uuid4()), "blocked")
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw_body)
    event_id = payload["id"]

    existing = session.exec(
        select(WebhookEvent).where(WebhookEvent.razorpay_event_id == event_id)
    ).first()
    if existing is not None:
        return {"status": "duplicate, ignored"}

    session.add(WebhookEvent(
        razorpay_event_id=event_id,
        event_type=payload["event"],
        payload_json=payload,
    ))

    if payload["event"] == "payment_link.paid":
        reference_id = payload["payload"]["payment_link"]["entity"]["reference_id"]
        order = session.exec(
            select(Order).where(Order.checkout_id == reference_id)
        ).first()
        if order:
            order.status = "PAID"
            session.add(order)

    session.commit()
    emit("audit-trail", "Webhook processed", {"event": payload["event"]}, str(uuid.uuid4()), "executed")
    return {"status": "ok"}
