# OpenStore core — OAuth 2.1 asymmetric tokens (INV-9)

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import OAuthAuthorizationCode, OAuthClient, OAuthToken

# INV-9 (Q-008 RESOLUTION): access tokens are asymmetric ES256 JWTs. The validator
# pins the header alg and verifies the JWS signature against the merchant's public
# key (resolved by kid) BEFORE trusting any claim.
_JWS_ALG_ALLOWLIST = {"ES256"}


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
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
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

    if datetime.now(UTC).replace(tzinfo=None) > auth_code.expires_at:
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
    auth_code.used_at = datetime.now(UTC).replace(tzinfo=None)
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
    now = datetime.now(UTC).replace(tzinfo=None)

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
    """Create JWT token signed with ES256.

    INV-9 / Q-008: issuance signs a real ES256 JWS Compact using the merchant's
    per-merchant key (DECISIONS §11.1.10), header {alg, kid, typ}. The kid is
    namespaced "{merchant_id}-key-{n}"; the default merchant key is used when no
    explicit jwks/key material is supplied (single-tenant sidecar default).
    """
    header = {"alg": "ES256", "typ": "JWT", "kid": _resolve_token_kid(merchant_jwks)}
    claims = {
        "jti": jti,
        "iss": "openstore",
        "sub": subject or client_id,
        "aud": client_id,
        "iat": int(datetime.now(UTC).replace(tzinfo=None).timestamp()),
        "exp": int(expires_at.timestamp()),
        "scope": " ".join(scopes),
        "token_type": token_type,
    }

    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    claims_bytes = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    header_b64 = base64.urlsafe_b64encode(header_bytes).decode().rstrip("=")
    claims_b64 = base64.urlsafe_b64encode(claims_bytes).decode().rstrip("=")
    signing_input = f"{header_b64}.{claims_b64}"

    private_key = _token_private_key(merchant_jwks)
    sig_b64 = _sign_es256(signing_input, private_key)

    return f"{header_b64}.{claims_b64}.{sig_b64}"


def _token_private_key(merchant_jwks: dict[str, Any] | None) -> Any:
    """Return the merchant's ES256 private key for token signing.

    When explicit key material is absent, fall back to the sidecar's per-merchant
    key from the well-known surface (same keypair its /.well-known/poai-jwks.json
    serves), so a default single-merchant install signs and verifies end to end.
    """
    if merchant_jwks and merchant_jwks.get("private_key"):
        from cryptography.hazmat.primitives import serialization
        return serialization.load_der_private_key(merchant_jwks["private_key"], password=None)

    from openstore.surfaces.wellknown import _load_or_generate_poai_keys
    data = _load_or_generate_poai_keys("merchant")
    from cryptography.hazmat.primitives import serialization
    return serialization.load_der_private_key(data["private_key"], password=None)


def _resolve_token_kid(merchant_jwks: dict[str, Any] | None) -> str:
    """kid for the signing key: per-merchant default (DECISIONS §11.1.10
    namespaced kid for merchant_id 'merchant')."""
    from openstore.surfaces.wellknown import _load_or_generate_poai_keys
    kid = _load_or_generate_poai_keys("merchant")["jwk"].get("kid")
    if not isinstance(kid, str):
        raise ValueError("merchant JWK kid must be a string")
    return kid


def _sign_es256(signing_input: str, private_key: Any) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        if private_key.curve.name != "secp256r1":
            raise ValueError("token signing requires EC P-256 (secp256r1)")
    else:
        raise ValueError("token signing requires an EC P-256 private key")
    der_sig = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw_sig).decode().rstrip("=")


def validate_access_token(
    session: Session,
    token: str,
    required_scopes: list[str] | None = None,
    jwks: dict[str, Any] | None = None,
) -> OAuthToken:
    """
    Validate access token.

    INV-9 / Q-008: BEFORE trusting any claim, verify the JWS signature and pin the
    header alg (allowlist ["ES256"]) against the merchant JWKS resolved by kid.
    Verification comes before the jti/exp/DB/revocation checks. Every verification
    failure carries the closed-set reason code auth.token_verification_failed.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise OAuthError("invalid_token", "Malformed token", 401)

        header_json = base64.urlsafe_b64decode(parts[0] + "==").decode()
        header = json.loads(header_json)

        if header.get("alg") not in _JWS_ALG_ALLOWLIST:
            raise OAuthError(
                "auth.token_verification_failed", "Unsupported token algorithm", 401
            )

        kid = header.get("kid")
        if not kid:
            raise OAuthError("auth.token_verification_failed", "Missing token kid", 401)

        public_key = _resolve_public_key(kid, jwks)

        signing_input = f"{parts[0]}.{parts[1]}"
        if not _verify_es256(signing_input, parts[2], public_key):
            raise OAuthError("auth.token_verification_failed", "Token signature invalid", 401)

        claims_json = base64.urlsafe_b64decode(parts[1] + "==").decode()
        claims = json.loads(claims_json)

        jti = claims.get("jti")
        if not jti:
            raise OAuthError("invalid_token", "Missing jti claim", 401)

        exp = claims.get("exp")
        if exp and datetime.now(UTC).replace(tzinfo=None).timestamp() > exp:
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


def _resolve_public_key(kid: str, jwks: dict[str, Any] | None) -> Any:
    """Resolve the ES256 public key by kid. Q-008: unknown kid is a hard error."""
    import base64 as _b64

    if jwks:
        keys = jwks.get("keys", [])
        jwk = next((k for k in keys if k.get("kid") == kid), None)
        if jwk is None:
            raise OAuthError(
                "auth.token_verification_failed", f"Unknown token kid {kid!r}", 401
            )
        x = _b64.urlsafe_b64decode(jwk["x"] + "==")
        y = _b64.urlsafe_b64decode(jwk["y"] + "==")
        return _build_public_key(x, y)

    # Default single-merchant install: use the same per-merchant keypair the
    # sidecar serves at /.well-known/poai-jwks.json (DECISIONS §11.1.10). If the
    # default merchant key was never created at issuance, generate it now so the
    # lookup below can resolve kid "merchant-key-1".
    from openstore.surfaces.wellknown import POAI_KEYS, _load_or_generate_poai_keys

    _load_or_generate_poai_keys("merchant")
    data = next(
        (cached for cached in POAI_KEYS.values() if cached["jwk"]["kid"] == kid),
        None,
    )
    if data is None:
        raise OAuthError(
            "auth.token_verification_failed", f"Unknown token kid {kid!r}", 401
        )
    return data["public_key"]


def _build_public_key(x: bytes, y: bytes) -> Any:
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    ).public_key()


def _verify_es256(signing_input: str, sig_b64: str, public_key: Any) -> bool:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

        raw_sig = base64.urlsafe_b64decode(sig_b64 + "==")
        if len(raw_sig) != 64:
            return False
        r = int.from_bytes(raw_sig[:32], "big")
        s = int.from_bytes(raw_sig[32:], "big")
        der_sig = encode_dss_signature(r, s)
        public_key.verify(
            der_sig, signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
        return True
    except Exception:
        return False


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

    token_record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(token_record)
    session.flush()

    return True


def get_jwks(config: Settings) -> dict[str, Any]:
    """
    INV-9: Return JWKS for token verification.

    Resource servers fetch this to validate ES256 signatures.
    """
    from openstore.surfaces.wellknown import get_poai_jwks
    return get_poai_jwks(config)
