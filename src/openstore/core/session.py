# OpenStore core — merchant-operator browsing sessions (S24 / Q-044).
#
# A session proves WHICH OPERATOR is browsing. It is NOT money authority:
# every money-moving action (policy signing, campaign approval, checkout)
# still requires a fresh WebAuthn assertion exactly as today. This module
# never touches payments, signing keys, or the ledger (R0.10).
#
# Storage discipline mirrors Handoff: the raw cookie token is never
# persisted — only its SHA-256. CSRF tokens derive deterministically from
# the raw session token via HKDF, so no second secret is stored and no new
# env secret is needed.

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlmodel import Session, select

from openstore.core.api import CommerceError
from openstore.models import MerchantSession, WebAuthnCredential

SESSION_COOKIE = "openstore_session"
CSRF_HEADER = "X-OpenStore-CSRF"

# 12h sliding idle window, 7d hard cap from creation (Q-044).
SESSION_IDLE_SECONDS = 12 * 3600
SESSION_HARD_CAP_SECONDS = 7 * 86400


def hash_token(raw_token: str) -> str:
    """SHA-256 hex of a cookie/share token. The raw token is never stored."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_user_agent(user_agent: str | None) -> str | None:
    """Stable hash of the UA string, or None when absent (a missing UA must
    not lock the merchant out — it only forfeits the UA-change tripwire)."""
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def mint_session(
    session: Session,
    operator_id: str,
    credential_id: str,
    user_agent: str | None,
) -> str:
    """Persist a session row and return the RAW cookie token (shown once —
    the caller sets it as the cookie value; only the hash is stored)."""
    if not operator_id or not operator_id.strip():
        raise CommerceError("auth.session_required", "operator_id must not be blank", 401)
    now = _now()
    raw = secrets.token_urlsafe(32)  # same generator as core/handoff.py:57
    row = MerchantSession(
        id=uuid.uuid4().hex,
        token_hash=hash_token(raw),
        operator_id=operator_id.strip(),
        credential_id=credential_id,
        created_at=now,
        last_seen_at=now,
        expires_at=min(now + timedelta(seconds=SESSION_IDLE_SECONDS), now + timedelta(seconds=SESSION_HARD_CAP_SECONDS)),
        revoked_at=None,
        user_agent_hash=hash_user_agent(user_agent),
    )
    session.add(row)
    return raw


def validate_session(
    session: Session,
    raw_token: str | None,
    user_agent: str | None,
) -> MerchantSession:
    """Resolve + refresh a session or raise CommerceError (401). Fails loud
    (R0.5): expired, revoked, unknown, and UA-changed tokens are all hard
    rejections with a closed-set code — never a silent anonymous fallback."""
    if not raw_token:
        raise CommerceError("auth.session_required", "merchant session required", 401)
    row = session.exec(
        select(MerchantSession).where(MerchantSession.token_hash == hash_token(raw_token))
    ).first()
    if row is None or row.revoked_at is not None:
        raise CommerceError("auth.session_required", "merchant session required", 401)
    now = _now()
    hard_cap = row.created_at + timedelta(seconds=SESSION_HARD_CAP_SECONDS)
    if now >= row.expires_at or now >= hard_cap:
        raise CommerceError("auth.session_expired", "merchant session expired — sign in again", 401)
    expected_ua = row.user_agent_hash
    if expected_ua is not None and hash_user_agent(user_agent) != expected_ua:
        raise CommerceError(
            "auth.session_expired",
            "session bound to a different browser — sign in again",
            401,
        )
    # Sliding window: refresh idle expiry, never past the hard cap.
    row.last_seen_at = now
    row.expires_at = min(now + timedelta(seconds=SESSION_IDLE_SECONDS), hard_cap)
    session.add(row)
    return row


def revoke_session(session: Session, raw_token: str | None) -> None:
    """Explicit logout. Unknown tokens are a no-op (logout must be
    idempotent — a double-click is not an error)."""
    if not raw_token:
        return
    row = session.exec(
        select(MerchantSession).where(MerchantSession.token_hash == hash_token(raw_token))
    ).first()
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = _now()
    session.add(row)


def store_claimed(session: Session) -> bool:
    """Trust-on-first-use gate (DECISION-044): True once ANY WebAuthn
    credential exists for any operator. The first passkey to claim an
    unclaimed store becomes operator #1."""
    return (
        session.exec(select(WebAuthnCredential.id).limit(1)).first() is not None
    )


def store_claimed_by_other(session: Session, operator_id: str) -> bool:
    """True if a credential exists for an operator OTHER than operator_id.

    Used to detect the genuine stale-claim race (two tabs both see an
    unclaimed store; one already claimed under a different operator name
    by the time this one completes) without also rejecting the ordinary
    case: op registers a passkey then completes its own first claim, at
    which point store_claimed() is already true for op's own credential."""
    return (
        session.exec(
            select(WebAuthnCredential.id).where(WebAuthnCredential.user_handle != operator_id).limit(1)
        ).first()
        is not None
    )


def csrf_token_for(raw_token: str) -> str:
    """Per-session CSRF token, derived — never stored, never in a cookie.
    An XSS-free attacker cannot read the HttpOnly session cookie and so
    cannot derive this; a same-site form post cannot set the header."""
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"openstore-merchant-console-v1",
        info=b"csrf",
    ).derive(raw_token.encode("utf-8"))
    return derived.hex()


def check_csrf(raw_token: str | None, presented: str | None) -> None:
    """Require the CSRF header on mutating merchant routes. SameSite=Lax
    alone is not sufficient for POST (top-level navigations still send
    cookies)."""
    if not raw_token or not presented:
        raise CommerceError("auth.csrf_invalid", "missing CSRF token", 403)
    if not hmac.compare_digest(csrf_token_for(raw_token), presented):
        raise CommerceError("auth.csrf_invalid", "invalid CSRF token", 403)

