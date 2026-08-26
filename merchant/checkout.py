import uuid
import hashlib
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session

from merchant.db import engine
from merchant.models import Cart, Checkout
from merchant.policy import rolling_spend_minor
from merchant.trace import emit
from merchant.mandate import compute_cart_hash

PER_TX_CAP_MINOR = 50000
ROLLING_CAP_MINOR = 200000


def checkout_initiate(cart_id: int, delivery_address: str, client_id: str, trace_id: str = None) -> dict:
    """Initiates checkout for a cart. Recomputes the total from the DB,
    checks spend caps, freezes an immutable snapshot. In the Intent Compiler
    flow, no OTP is issued — the agent proceeds directly to checkout_confirm,
    where the Intent Compiler verifies the cart against the signed policy."""
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

        checkout = Checkout(
            checkout_id=checkout_id,
            cart_id=cart.id,
            client_id=client_id,
            status="POLICY_VERIFIED",  # Intent Compiler flow: no approval wait
            cart_hash=cart_hash,
            total_minor=total_minor,
            delivery_address=delivery_address,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        session.add(checkout)
        session.commit()

        emit("merchant-server", "Checkout initiated (Intent Compiler path)",
             {"checkout_id": checkout_id, "total": total_minor}, trace_id, "gate")
        return {
            "checkout_id": checkout_id,
            "status": "POLICY_VERIFIED",
            "expires_in_seconds": 300,
            "next_step": "checkout_confirm — the Intent Compiler will verify your cart against the signed policy",
        }
