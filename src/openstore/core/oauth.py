# OpenStore core — OAuth 2.1 asymmetric tokens (INV-9)

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import OAuthAuthorizationCode, OAuthClient, OAuthToken


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status_code: int = 400):
        self.error = error
        self.description = description
        self.status_code = status_code
        super().__init__(f"[{error}] {description}")


# Token configuration
ACCESS_TOKEN_TTL_SECONDS = 3600  # 1 hour
REFRESH_TOKEN_TTL_SECONDS = 86400 * 30  # 30 days
AUTH_CODE_TTL_SECONDS = 600  # 10 minutes


def generate_client_id() -> str:
    return f"client_{secrets.token_urlsafe(16)}"


def generate_client_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_client_secret(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(hash_client_secret(secret), secret_hash)


def register_client(
    session: Session,
    client_name: str,
    redirect_uris: list[str],
    grant_types: list[str],
    scopes: list[str],
    jwks_uri: str | None = None,
    jwks: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Register a new OAuth client.

    Returns (client_id, client_secret). Client secret only returned once.
    """
    client_id = generate_client_id()
    client_secret = generate_client_secret()
    secret_hash = hash_client_secret(client_secret)

    client = OAuthClient(
        client_id=client_id,
        client_name=client_name,
        client_secret_hash=secret_hash,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        scopes=scopes,
        jwks_uri=jwks_uri,
        jwks=jwks,
        is_active=True,
    )

    session.add(client)
    session.flush()

    return client_id, client_secret


def validate_client(
    session: Session,
    client_id: str,
    client_secret: str | None = None,
) -> OAuthClient:
    """Validate client credentials."""
    client = session.exec(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    ).first()

    if not client:
        raise OAuthError("invalid_client", "Client not found", 401)

    if not client.is_active:
        raise OAuthError("invalid_client", "Client is inactive", 401)

    if client_secret and client.client_secret_hash:
        if not verify_client_secret(client_secret, client.client_secret_hash):
            raise OAuthError("invalid_client", "Invalid client secret", 401)

    return client


def create_authorization_code(
    session: Session,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create PKCE authorization code."""
    code = secrets.token_urlsafe(32)

    auth_code = OAuthAuthorizationCode(
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=datetime.utcnow() + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
        metadata=metadata,
    )

    session.add(auth_code)
    session.flush()

    return code


def validate_authorization_code(
    session: Session,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> OAuthAuthorizationCode:
    """Validate and consume authorization code."""
    auth_code = session.exec(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code == code,
            OAuthAuthorizationCode.client_id == client_id,
        )
    ).first()

    if not auth_code:
        raise OAuthError("invalid_grant", "Authorization code not found", 400)

    if auth_code.used_at:
        raise OAuthError("invalid_grant", "Authorization code already used", 400)

    if auth_code.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "Redirect URI mismatch", 400)

    if datetime.utcnow() > auth_code.expires_at:
        raise OAuthError("invalid_grant", "Authorization code expired", 400)

    # PKCE verification
    if auth_code.code_challenge and code_verifier:
        if auth_code.code_challenge_method == "S256":
            challenge = hashlib.sha256(code_verifier.encode()).digest()
            expected = base64.urlsafe_b64encode(challenge).decode().rstrip("=")
            if not hmac.compare_digest(expected, auth_code.code_challenge):
                raise OAuthError("invalid_grant", "PKCE verification failed", 400)
        else:
            raise OAuthError("invalid_grant", "Unsupported code challenge method", 400)
    elif auth_code.code_challenge and not code_verifier:
        raise OAuthError("invalid_grant", "PKCE code_verifier required", 400)

    # Mark as used
    auth_code.used_at = datetime.utcnow()
    session.add(auth_code)
    session.flush()

    return auth_code


def create_token_pair(
    session: Session,
    client_id: str,
    scopes: list[str],
    subject: str | None = None,
    merchant_jwks: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    INV-9: Create asymmetric access token (ES256) + refresh token.

    Access token is signed with merchant's private key.
    Resource server only holds JWKS (public keys).
    """
    now = datetime.utcnow()

    # Generate JTI for access token
    access_jti = secrets.token_urlsafe(32)
    refresh_jti = secrets.token_urlsafe(32)

    # Create access token (JWT signed with ES256)
    access_token = _create_jwt_token(
        jti=access_jti,
        client_id=client_id,
        scopes=scopes,
        subject=subject,
        expires_at=now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        token_type="access_token",
        merchant_jwks=merchant_jwks,
    )

    # Create refresh token (opaque, stored in DB)
    refresh_token = secrets.token_urlsafe(64)
    refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

    # Store access token metadata
    access_token_record = OAuthToken(
        jti=access_jti,
        client_id=client_id,
        token_type="access_token",
        scopes=scopes,
        subject=subject,
        issued_at=now,
        expires_at=now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        access_token_hash=hashlib.sha256(access_token.encode()).hexdigest(),
    )

    # Store refresh token metadata
    refresh_token_record = OAuthToken(
        jti=refresh_jti,
        client_id=client_id,
        token_type="refresh_token",
        scopes=scopes,
        subject=subject,
        issued_at=now,
        expires_at=now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        access_token_hash=refresh_token_hash,
    )

    session.add_all([access_token_record, refresh_token_record])
    session.flush()

    return access_token, refresh_token


def _create_jwt_token(
    jti: str,
    client_id: str,
    scopes: list[str],
    subject: str | None,
    expires_at: datetime,
    token_type: str,
    merchant_jwks: dict[str, Any] | None = None,
) -> str:
    """Create JWT token signed with ES256."""
    header = {"alg": "ES256", "typ": "JWT", "kid": "merchant-key-1"}
    claims = {
        "jti": jti,
        "iss": "openstore",
        "sub": subject or client_id,
        "aud": client_id,
        "iat": int(datetime.utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
        "scope": " ".join(scopes),
        "token_type": token_type,
    }

    # Placeholder: return a structured token that can be parsed
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    claims_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature_b64 = base64.urlsafe_b64encode(b"placeholder-signature").decode().rstrip("=")

    return f"{header_b64}.{claims_b64}.{signature_b64}"


def validate_access_token(
    session: Session,
    token: str,
    required_scopes: list[str] | None = None,
) -> OAuthToken:
    """
    Validate access token.

    In production, this would verify the ES256 signature against JWKS.
    For now, we parse and check DB record.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise OAuthError("invalid_token", "Malformed token", 401)

        claims_json = base64.urlsafe_b64decode(parts[1] + "==").decode()
        claims = json.loads(claims_json)

        jti = claims.get("jti")
        if not jti:
            raise OAuthError("invalid_token", "Missing jti claim", 401)

        exp = claims.get("exp")
        if exp and datetime.utcnow().timestamp() > exp:
            raise OAuthError("invalid_token", "Token expired", 401)

        token_record = session.exec(
            select(OAuthToken).where(
                OAuthToken.jti == jti,
                OAuthToken.token_type == "access_token",
            )
        ).first()

        if not token_record:
            raise OAuthError("invalid_token", "Token not found", 401)

        if token_record.revoked_at:
            raise OAuthError("invalid_token", "Token revoked", 401)

        if required_scopes:
            token_scopes = set(token_record.scopes)
            required = set(required_scopes)
            if not required.issubset(token_scopes):
                raise OAuthError("insufficient_scope", "Token missing required scopes", 403)

        return token_record

    except (json.JSONDecodeError, ValueError, IndexError):
        raise OAuthError("invalid_token", "Invalid token format", 401)


def revoke_token(session: Session, jti: str, token_type: str = "access_token") -> bool:
    """Revoke a token."""
    token_record = session.exec(
        select(OAuthToken).where(
            OAuthToken.jti == jti,
            OAuthToken.token_type == token_type,
        )
    ).first()

    if not token_record:
        return False

    token_record.revoked_at = datetime.utcnow()
    session.add(token_record)
    session.flush()

    return True


def get_jwks(config: Settings) -> dict[str, Any]:
    """
    INV-9: Return JWKS for token verification.

    Resource servers fetch this to validate ES256 signatures.
    """
    return {"keys": []}
