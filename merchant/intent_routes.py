import hashlib
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
import jwt as pyjwt

from merchant.db import get_session
from merchant.models import (
    Checkout, IntentPolicyRow, PolicyChallenge, AssertionRow, IntentPolicy,
)
from merchant.mandate import canonical_json_bytes
from merchant.webauthn import (
    fido_server, b64url_encode, b64url_decode, build_credential,
)
from merchant.oauth.routes import JWT_SECRET, REVOKED_JTIS

intent_router = APIRouter()
templates = Jinja2Templates(directory="merchant/storefront")


def verify_token(authorization: str = Header(...)) -> dict:
    """Verify OAuth bearer token and return claims."""
    trace_id = str(uuid.uuid4())
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Malformed Authorization header")

    token = authorization.removeprefix("Bearer ")
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    if claims.get("jti") in REVOKED_JTIS:
        raise HTTPException(401, "Token has been revoked")

    return claims


import uuid


@intent_router.get("/intent/sign", response_class=HTMLResponse)
def intent_signing_page(request: Request):
    return templates.TemplateResponse(request, "intent_sign.html")


@intent_router.get("/intent/register", response_class=HTMLResponse)
def intent_register_page(request: Request):
    return templates.TemplateResponse(request, "intent_register.html")


@intent_router.get("/internal/intent-status")
def intent_status(user_id: str = "default-user", session: Session = Depends(get_session)):
    row = session.exec(
        select(IntentPolicyRow).where(IntentPolicyRow.active == True).where(IntentPolicyRow.user_id == user_id)
    ).first()
    return {"has_policy": row is not None}


@intent_router.post("/internal/webauthn/challenge")
def issue_webauthn_challenge(
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Generate a random 32-byte nonce and store its mapping to the policy hash.
    The authenticator will sign this nonce, which is cryptographically bound to
    the policy via the nonce -> policy_hash row in the database."""
    intent_policy = IntentPolicy(**policy)
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    policy_hash = hashlib.sha256(policy_canonical).hexdigest()

    nonce_bytes = os.urandom(32)
    nonce_b64 = b64url_encode(nonce_bytes)

    session.add(PolicyChallenge(
        challenge_id=nonce_b64,
        policy_hash=policy_hash,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    session.commit()

    return {"nonce": nonce_b64}


@intent_router.post("/internal/webauthn/register")
def register_webauthn_credential(
    credential: dict = Body(...),
    challenge_id: str = Body(...),
    policy: dict = Body(...),
    user_id: str = Body("default-user"),
    session: Session = Depends(get_session),
):
    """Verify the WebAuthn attestation response and store the credential.
    challenge_id is the nonce from /internal/webauthn/challenge."""
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

    import fido2.cbor as cbor
    cred_id_b64 = b64url_encode(auth_data.credential_data.credential_id)
    pk_cbor = cbor.encode(dict(auth_data.credential_data.public_key))
    pk_b64 = b64url_encode(pk_cbor)

    session.add(IntentPolicyRow(
        credential_id=cred_id_b64,
        public_key=pk_b64,
        sign_count=auth_data.sign_count,
        policy_json=policy,
        user_id=user_id,
        active=True,
    ))
    session.commit()

    return {"status": "registered"}


@intent_router.post("/internal/webauthn/begin-signing")
def begin_signing(
    credential_id: str = Body(...),
    policy: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Begin the authentication ceremony: generate a random nonce, store the
    fido2 state, and return browser options for navigator.credentials.get().
    The nonce is bound to the SHA-256 of the canonical policy JSON."""
    intent_policy = IntentPolicy(**policy)
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    policy_hash = hashlib.sha256(policy_canonical).hexdigest()

    row = session.exec(
        select(IntentPolicyRow)
        .where(IntentPolicyRow.credential_id == credential_id)
        .where(IntentPolicyRow.active == True)
    ).first()
    if row is None:
        raise HTTPException(404, "Credential not found — register first")

    credential = build_credential(row.credential_id, row.public_key)

    nonce_bytes = os.urandom(32)
    nonce_b64 = b64url_encode(nonce_bytes)

    options, state = fido_server.authenticate_begin(
        credentials=[credential],
        challenge=nonce_bytes,
    )

    session.add(PolicyChallenge(
        challenge_id=nonce_b64,
        policy_hash=policy_hash,
        state_json=state,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    ))
    session.commit()

    return {"nonce": nonce_b64, "options": options}


@intent_router.post("/internal/webauthn/complete-signing")
def complete_signing(
    credential_id: str = Body(...),
    assertion: dict = Body(...),
    policy_json: dict = Body(...),
    session: Session = Depends(get_session),
):
    """Complete the authentication ceremony: verify the assertion against the
    stored credential and fido2 state, then store the signed policy token
    (assertion + policy_json) for the agent to carry to checkout."""
    row = session.exec(
        select(IntentPolicyRow)
        .where(IntentPolicyRow.credential_id == credential_id)
        .where(IntentPolicyRow.active == True)
    ).first()
    if row is None:
        raise HTTPException(404, "Credential not found")

    credential = build_credential(row.credential_id, row.public_key)

    # Extract nonce from the assertion's clientDataJSON
    resp = assertion.get("response", {})
    client_data_bytes = b64url_decode(resp["clientDataJSON"])
    client_data_obj = json.loads(client_data_bytes)
    nonce_b64 = client_data_obj["challenge"]

    challenge_row = session.exec(
        select(PolicyChallenge).where(PolicyChallenge.challenge_id == nonce_b64)
    ).first()
    if challenge_row is None or challenge_row.used or challenge_row.expires_at < datetime.utcnow():
        raise HTTPException(400, "Invalid or expired signing challenge")

    # Verify the assertion signature using the fido2 state
    try:
        fido_server.authenticate_complete(
            challenge_row.state_json,
            [credential],
            assertion,
        )
    except Exception as e:
        raise HTTPException(403, f"Assertion verification failed: {e}")

    # Verify the policy hash matches
    intent_policy = IntentPolicy(**policy_json)
    policy_canonical = canonical_json_bytes(intent_policy.model_dump())
    actual_hash = hashlib.sha256(policy_canonical).hexdigest()
    if actual_hash != challenge_row.policy_hash:
        raise HTTPException(400, "Policy hash mismatch — the signed policy does not match")

    challenge_row.used = True
    session.add(challenge_row)

    # Store the policy token: assertion + policy_json + nonce + user_id
    session.add(AssertionRow(
        credential_id=credential_id,
        assertion_json=assertion,
        policy_json=policy_json,
        nonce=nonce_b64,
        user_id=row.user_id,
    ))
    session.commit()

    return {"status": "signed"}


@intent_router.get("/internal/webauthn/latest-assertion")
def latest_assertion(
    claims: dict = Depends(verify_token),
    session: Session = Depends(get_session),
):
    """Return the most recently stored policy token for the agent to carry.
    Requires a valid OAuth token (any scope). Returns the assertion for the
    merchant's default user."""
    row = session.exec(
        select(AssertionRow)
        .where(AssertionRow.user_id == "default-user")
        .order_by(AssertionRow.created_at.desc())
    ).first()
    if row is None:
        raise HTTPException(404, "No signed policy found")
    return {"policy_token": row.assertion_json, "policy_json": row.policy_json}
