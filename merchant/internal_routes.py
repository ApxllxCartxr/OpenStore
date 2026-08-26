import hashlib
import time
import uuid
import jwt as pyjwt
from datetime import datetime

from argon2 import PasswordHasher
from fastapi import APIRouter, Body, HTTPException, Depends
from sqlmodel import Session, select

from merchant.db import get_session
from merchant.models import Checkout, OTPChallenge, Cart, Mandate
from merchant.mandate import issue_mandate, compute_cart_hash, verify_mandate
from merchant.trace import emit

router = APIRouter()
ph = PasswordHasher()
OTP_MAX_ATTEMPTS = 3


@router.post("/internal/otp-verify")
def otp_verify(
    checkout_id: str = Body(...),
    otp: str = Body(...),
    session: Session = Depends(get_session),
):
    checkout = session.exec(
        select(Checkout).where(Checkout.checkout_id == checkout_id)
    ).first()
    if checkout is None or checkout.status != "AWAITING_APPROVAL":
        raise HTTPException(400, "No pending checkout in this state")
    if checkout.expires_at < datetime.utcnow():
        checkout.status = "REJECTED"
        session.add(checkout)
        session.commit()
        raise HTTPException(400, "Checkout expired")

    challenge = session.exec(
        select(OTPChallenge)
        .where(OTPChallenge.checkout_id == checkout_id)
        .where(OTPChallenge.used == False)
    ).first()
    if challenge is None or challenge.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(400, "No valid OTP challenge (expired or too many attempts)")

    try:
        ph.verify(challenge.otp_hash, otp)
    except Exception:
        challenge.attempts += 1
        session.add(challenge)
        session.commit()
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            checkout.status = "REJECTED"
            session.add(checkout)
            session.commit()
        raise HTTPException(400, "Incorrect OTP")

    challenge.used = True
    session.add(challenge)

    cart = session.get(Cart, checkout.cart_id)
    jws_compact, fingerprint = issue_mandate(
        merchant_id="gelateria-roma",
        checkout_id=checkout_id,
        client_id=checkout.client_id,
        cart_hash=checkout.cart_hash,
        cart_version=cart.version,
        items=cart.items_json,
        total_minor=checkout.total_minor,
        delivery_address_hash=hashlib.sha256(checkout.delivery_address.encode()).hexdigest(),
    )
    checkout.status = "MANDATE_ISSUED"
    session.add(checkout)
    session.add(Mandate(
        jti=pyjwt.decode(jws_compact, options={"verify_signature": False})["jti"],
        checkout_id=checkout_id,
        jws_compact=jws_compact,
        fingerprint=fingerprint,
    ))
    session.commit()

    return {"fingerprint": fingerprint, "jws": jws_compact}
