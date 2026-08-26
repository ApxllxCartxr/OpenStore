import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from merchant.db import get_session
from merchant.models import IntentPolicyRow, PolicyChallenge, IntentPolicy
from merchant.mandate import canonical_json_bytes

from fido2.server import Fido2Server
from fido2.webauthn import PublicKeyCredentialRpEntity

rp = PublicKeyCredentialRpEntity(id="localhost", name="OpenStore Merchant")
fido_server = Fido2Server(rp)

intent_router = APIRouter()
templates = Jinja2Templates(directory="merchant/storefront")


@intent_router.get("/intent/sign", response_class=HTMLResponse)
def intent_signing_page(request: Request):
    """Serves the Intent Policy signing ceremony page."""
    return templates.TemplateResponse(request, "intent_sign.html")


@intent_router.get("/internal/intent-status")
def intent_status(session: Session = Depends(get_session)):
    """Check if any active Intent Policy exists."""
    row = session.exec(
        select(IntentPolicyRow).where(IntentPolicyRow.active == True)
    ).first()
    return {"has_policy": row is not None}


@intent_router.post("/internal/webauthn/challenge")
def issue_webauthn_challenge(
    checkout_id: str = Body(...),
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Generate a WebAuthn challenge whose bytes are the SHA-256 hash of the
    canonical policy JSON. The authenticator will sign these bytes, binding
    the human's biometric to this exact policy."""
    intent_policy = IntentPolicy(**policy)

    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    policy_hash = hashlib.sha256(policy_canonical).hexdigest()
    challenge_bytes = bytes.fromhex(policy_hash)  # 32 bytes, valid WebAuthn challenge

    challenge_b64 = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode()

    session.add(PolicyChallenge(
        challenge_id=challenge_b64,
        checkout_id=checkout_id,
        policy_hash=policy_hash,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    session.commit()

    return {"challenge": challenge_b64, "challenge_id": challenge_b64}


@intent_router.post("/internal/webauthn/register")
def register_webauthn_credential(
    credential: dict = Body(...),
    challenge_id: str = Body(...),
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Verify the WebAuthn registration response, store the credential,
    and bind it to the signed Intent Policy."""
    challenge_row = session.exec(
        select(PolicyChallenge).where(PolicyChallenge.challenge_id == challenge_id)
    ).first()
    if challenge_row is None or challenge_row.used or challenge_row.expires_at < datetime.utcnow():
        raise HTTPException(400, "Invalid or expired challenge")

    try:
        auth_data = fido_server.register_complete(
            challenge_row.challenge_id,
            credential,
        )
    except Exception as e:
        raise HTTPException(400, f"WebAuthn registration failed: {e}")

    challenge_row.used = True
    session.add(challenge_row)

    cred_id_b64 = base64.urlsafe_b64encode(auth_data.credential_data.credential_id).rstrip(b"=").decode()
    pk_bytes = auth_data.credential_data.public_key
    pk_b64 = base64.urlsafe_b64encode(pk_bytes).rstrip(b"=").decode()

    session.add(IntentPolicyRow(
        credential_id=cred_id_b64,
        public_key=pk_b64,
        sign_count=auth_data.sign_count,
        policy_json=policy,
        active=True,
    ))
    session.commit()

    return {"status": "registered"}


@intent_router.post("/internal/webauthn/verify")
def verify_webauthn_assertion(
    checkout_id: str = Body(...),
    assertion: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Verify a WebAuthn assertion against the stored policy for this checkout.
    Returns the policy if valid; raises 403 if the assertion is invalid or the
    policy does not cover this checkout."""
    cred_id_b64 = assertion.get("id", "")
    row = session.exec(
        select(IntentPolicyRow)
        .where(IntentPolicyRow.credential_id == cred_id_b64)
        .where(IntentPolicyRow.active == True)
    ).first()
    if row is None:
        raise HTTPException(403, "Unknown credential — policy not registered")

    intent_policy = IntentPolicy(**row.policy_json)
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    expected_challenge = hashlib.sha256(policy_canonical).hexdigest()

    try:
        fido_server.authenticate_complete(
            row.sign_count,
            {"id": row.credential_id, "public_key": row.public_key},
            expected_challenge,
            assertion,
        )
    except Exception as e:
        raise HTTPException(403, f"WebAuthn assertion invalid: {e}")

    row.sign_count = row.sign_count + 1
    session.add(row)
    session.commit()

    return {"policy": row.policy_json}
