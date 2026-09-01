# tests/redteam/test_inv9_oauth_alg.py
# INV-9 — Asymmetric OAuth tokens: ES256 keypair, `kid` header, no `alg` confusion.
# Red-team: a token forged with `alg: none` (or RS256) must be REJECTED.
#
# STATUS: KNOWN GAP. validate_access_token() parses claims and checks the DB record
# but does NOT pin the header alg nor verify the ECDSA signature. A forged token with
# the same claims is ACCEPTED. This test is the adversarial proof; per stage-10
# MUST-NOT it must NOT be weakened. Tracked in OPEN_QUESTIONS.md (Q-008).

from __future__ import annotations

import base64
import json

import pytest
from openstore.core.oauth import OAuthError, create_token_pair, validate_access_token


def _forge(token: str, alg: str = "none", sig: str = "") -> str:
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==").decode())
    header = {"alg": alg, "typ": "JWT"}
    hdr_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{hdr_b64}.{payload_b64}.{sig}"


def test_alg_none_token_rejected(session):
    """INV-9: an unsigned (alg:none) token must never be accepted as a valid token."""
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()

    forged = _forge(token, alg="none")
    # This MUST be rejected. Currently ACCEPTED (product gap) — see OPEN_QUESTIONS.
    with pytest.raises(OAuthError):
        validate_access_token(session, forged, required_scopes=["catalog:read"])


def test_header_alg_is_es256_pinned(session):
    """INV-9: the issuer only ever emits ES256 tokens."""
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "==").decode())
    assert header["alg"] == "ES256"
    assert header["kid"] == "merchant-key-1"


def test_rs256_alg_confusion_rejected(session):
    """INV-9: an RSA-signed-looking token (alg: RS256) must not pass as ES256-only."""
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()
    forged = _forge(token, alg="RS256")
    with pytest.raises(OAuthError):
        validate_access_token(session, forged)


def test_malformed_token_rejected(session):
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()
    with pytest.raises(OAuthError) as ei:
        validate_access_token(session, "not-a-jwt")
    assert ei.value.error == "invalid_token"


def test_unsigned_token_rejected(session):
    """Q-008: an ES256-header token with an empty/absent signature must be rejected."""
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()
    forged = _forge(token, alg="ES256", sig="")
    with pytest.raises(OAuthError) as ei:
        validate_access_token(session, forged)
    assert ei.value.error == "auth.token_verification_failed"


def test_wrong_kid_token_rejected(session):
    """Q-008: a token whose kid is not in the merchant JWKS is a hard error."""
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()
    header = {"alg": "ES256", "typ": "JWT", "kid": "merchant-key-99"}
    hdr_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    forged = f"{hdr_b64}.{token.split('.')[1]}.{token.split('.')[2]}"
    with pytest.raises(OAuthError) as ei:
        validate_access_token(session, forged)
    assert ei.value.error == "auth.token_verification_failed"


def test_revoked_token_rejected(session):
    token, _ = create_token_pair(session, "cli_a", ["catalog:read"])
    session.commit()
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==").decode())
    from openstore.core.oauth import revoke_token
    revoke_token(session, claims["jti"])
    session.commit()
    with pytest.raises(OAuthError) as ei:
        validate_access_token(session, token)
    assert ei.value.error == "invalid_token"
