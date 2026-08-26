import uuid
import hashlib
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from fastapi import HTTPException
from sqlmodel import Session, select

from merchant.db import engine
from merchant.models import Cart, Checkout, OTPChallenge
from merchant.policy import rolling_spend_minor
from merchant.trace import emit
from merchant.mandate import compute_cart_hash

ph = PasswordHasher()
PER_TX_CAP_MINOR = 50000
ROLLING_CAP_MINOR = 200000
OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 3


def checkout_initiate(cart_id: int, delivery_address: str, client_id: str, trace_id: str = None) -> dict:
    """Initiates checkout for a cart. Recomputes the total from the DB, checks
    spend caps, freezes an immutable snapshot, and sends an OTP to the human
    approver via DM. Never touches Razorpay."""
    with Session(engine) as session:
        cart = session.get(Cart, cart_id)
        if cart is None or cart.client_id != client_id:
            raise HTTPException(404, "Cart not found")

        total_minor = sum(item["unit_minor"] * item["qty"] for item in cart.items_json)

        if total_minor > PER_TX_CAP_MINOR:
            emit("merchant-server", "Checkout rejected: over per-tx cap",
                 {"total": total_minor, "cap": PER_TX_CAP_MINOR}, trace_id, "blocked")
            raise HTTPException(400, f"Amount {total_minor} exceeds per-transaction cap {PER_TX_CAP_MINOR}")

        already_spent = rolling_spend_minor(session, client_id)
        if already_spent + total_minor > ROLLING_CAP_MINOR:
            emit("merchant-server", "Checkout rejected: over rolling cap",
                 {"already_spent": already_spent, "attempted": total_minor}, trace_id, "blocked")
            raise HTTPException(400, "Rolling 24h spend cap exceeded")

        checkout_id = str(uuid.uuid4())
        cart_hash = compute_cart_hash(cart.items_json)
        dlv_hash = hashlib.sha256(delivery_address.encode()).hexdigest()

        checkout = Checkout(
            checkout_id=checkout_id,
            cart_id=cart.id,
            client_id=client_id,
            status="AWAITING_APPROVAL",
            cart_hash=cart_hash,
            total_minor=total_minor,
            delivery_address=delivery_address,
            expires_at=datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
        )
        session.add(checkout)

        otp = "".join(secrets.choice("0123456789") for _ in range(6))
        otp_hash = ph.hash(otp)
        session.add(OTPChallenge(checkout_id=checkout_id, otp_hash=otp_hash))
        session.commit()

        from merchant.notifier import send_approval_dm
        send_approval_dm(
            checkout_id=checkout_id,
            items=cart.items_json,
            total_minor=total_minor,
            delivery_address=delivery_address,
            otp=otp,
            expires_at=checkout.expires_at,
        )

        emit("merchant-server", "Checkout initiated",
             {"checkout_id": checkout_id, "total": total_minor}, trace_id, "gate")
        return {"checkout_id": checkout_id, "status": "AWAITING_APPROVAL",
                "expires_in_seconds": OTP_TTL_MINUTES * 60}
