import base64
import hashlib
import time
import uuid
import secrets
from pathlib import Path
import jwt as pyjwt

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlmodel import Session

from reference.merchant.oauth.models import AuthServerMetadata, ClientRegistrationRequest, ClientRegistrationResponse
from reference.merchant.db import get_session
from reference.merchant.models import OAuthClient
from reference.merchant.oauth.store import pending_codes
from reference.merchant.trace import emit


router = APIRouter()

BASE_URL = "http://localhost:8000"
JWT_SECRET = "dev-only-change-me"  # load from env in real deployments
ACCESS_TOKEN_TTL_SECONDS = 15 * 60
REFRESH_TOKENS: dict[str, dict] = {}  # refresh_token -> {client_id, scopes}

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

@router.get("/.well-known/oauth-authorization-server")
def as_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return AuthServerMetadata(
        issuer=base,
        authorization_endpoint=f"{base}/oauth/authorize",
        token_endpoint=f"{base}/oauth/token",
        registration_endpoint=f"{base}/oauth/register",
        revocation_endpoint=f"{base}/oauth/revoke",
        jwks_uri=f"{base}/oauth/jwks.json",
        scopes_supported=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
    )

@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
    }

@router.post("/oauth/register", response_model=ClientRegistrationResponse)
def register_client(req: ClientRegistrationRequest, session: Session = Depends(get_session)):
    client_id = f"client_{secrets.token_urlsafe(12)}"
    client = OAuthClient(
        client_id=client_id,
        client_secret_hash="",  # public client, no secret
        display_name=req.client_name,
        redirect_uris=req.redirect_uris,
    )
    session.add(client)
    session.commit()
    return ClientRegistrationResponse(
        client_id=client_id,
        client_name=req.client_name,
        redirect_uris=req.redirect_uris,
    )

@router.get("/oauth/authorize", response_class=HTMLResponse)
def authorize(
    request: Request,
    client_id: str,
    scope: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    state: str = "",
):
    return templates.TemplateResponse(
        request,
        "consent.html",
        {
            "request": request,
            "client_id": client_id,
            "scopes": scope.split(),
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
        },
    )

@router.post("/oauth/consent")
def consent(
    approve: str = Form(...),  # "yes" or "no", from the form submit button
    client_id: str = Form(...),
    scope: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    state: str = Form(""),
):
    if approve != "yes":
        return RedirectResponse(f"{redirect_uri}?error=access_denied&state={state}")

    code = secrets.token_urlsafe(24)
    pending_codes[code] = {
        "client_id": client_id,
        "scopes": scope.split(),
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + 60,  # 60 seconds to redeem
    }
    return RedirectResponse(f"{redirect_uri}?code={code}&state={state}")

def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    computed = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return computed == code_challenge

@router.post("/oauth/token")
def token(
    grant_type: str = Form(...),
    code: str = Form(None),
    code_verifier: str = Form(None),
    redirect_uri: str = Form(None),
    refresh_token: str = Form(None),
    client_id: str = Form(...),
):
    if grant_type == "authorization_code":
        entry = pending_codes.pop(code, None)
        if entry is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if entry["expires_at"] < time.time():
            return JSONResponse({"error": "invalid_grant", "error_description": "code expired"}, status_code=400)
        if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if not verify_pkce(code_verifier, entry["code_challenge"]):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        scopes = entry["scopes"]

    elif grant_type == "refresh_token":
        entry = REFRESH_TOKENS.get(refresh_token)
        if entry is None or entry["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        scopes = entry["scopes"]

    else:
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    now = int(time.time())
    jti = secrets.token_urlsafe(16)
    access_token = pyjwt.encode(
        {
            "sub": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + ACCESS_TOKEN_TTL_SECONDS,
            "jti": jti,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    new_refresh = secrets.token_urlsafe(24)
    REFRESH_TOKENS[new_refresh] = {"client_id": client_id, "scopes": scopes}

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": new_refresh,
        "scope": " ".join(scopes),
    }


# Revocation set and scope dependency
REVOKED_JTIS: set[str] = set()


def require_scope(required_scope: str):
    def dependency(authorization: str = Header(...)) -> dict:
        trace_id = str(uuid.uuid4())
        if not authorization.startswith("Bearer "):
            emit("merchant-server", "Auth rejected", {"reason": "malformed header"}, trace_id, "blocked")
            raise HTTPException(401, "Malformed Authorization header")

        token = authorization.removeprefix("Bearer ")
        try:
            claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            emit("merchant-server", "Auth rejected", {"reason": "token expired"}, trace_id, "blocked")
            raise HTTPException(401, "Token expired")
        except pyjwt.InvalidTokenError:
            emit("merchant-server", "Auth rejected", {"reason": "invalid token"}, trace_id, "blocked")
            raise HTTPException(401, "Invalid token")

        if claims.get("jti") in REVOKED_JTIS:
            emit("merchant-server", "Auth rejected", {"reason": "token revoked"}, trace_id, "blocked")
            raise HTTPException(401, "Token has been revoked")

        scopes = claims.get("scope", "").split()
        if required_scope not in scopes:
            emit(
                "merchant-server",
                "Auth rejected",
                {"reason": "missing scope", "required": required_scope, "client": claims.get("sub")},
                trace_id,
                "blocked",
            )
            raise HTTPException(403, f"Missing required scope: {required_scope}")

        return claims
    return dependency


@router.post("/oauth/revoke")
def revoke(token: str = Form(...)):
    try:
        claims = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
        jti = claims.get("jti")
        if jti:
            REVOKED_JTIS.add(jti)
    except pyjwt.InvalidTokenError:
        pass
    return {"revoked": True}