# OpenStore core — Handoff bridge (S11 Phase 2, Q-014/Q-016)
#
# A handoff is a durable, single-use, token-addressed row that parks a chat
# conversation while a human completes an out-of-band ceremony (policy
# signing, amendment approval) in a browser, then resumes it. One table and
# one create/resolve/consume path serves every ceremony kind — no bespoke
# one-off "policy signed" callback (plan go-with-the-push-functional-hearth.md).
#
# Fails loud (R0.5): an unknown, expired, or already-consumed token is a hard
# HandoffError carrying a closed-set authority.* reason code, never a silent
# fallback identity. core/ may not import agents/ (import firewall) — the
# chat push egress this module's callers trigger belongs in notifier.py.

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.core.webauthn_rp import get_user_credentials
from openstore.models import Handoff, HandoffKind, IntentPolicy

# Handoff TTL: a signing/approval link must survive long enough for a human to
# notice a DM and act on it. Not a PRD/[verify-at-build] cryptographic
# constant (like CHALLENGE_TTL_SECONDS in webauthn_rp.py) — a plain module
# default, overridable per-call.
HANDOFF_TTL_SECONDS = 3600

_REASON_NOT_FOUND = "authority.handoff_not_found"
_REASON_EXPIRED = "authority.handoff_expired"
_REASON_CONSUMED = "authority.handoff_consumed"
_REASON_POLICY_UNSIGNED = "authority.policy_unsigned"


class HandoffError(Exception):
    """Closed-set rejection (Q-016). reason_code is always one of the four
    authority.handoff_* / authority.policy_unsigned REGISTRY entries."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def buyer_handle(handoff: Handoff) -> str:
    """The webauthn user_handle a chat handoff resolves to (S11 plan: buyer
    identity rides the existing credential join, e.g. "discord:123456")."""
    return f"{handoff.chat_platform}:{handoff.chat_user_id}"


def create_handoff(
    session: Session,
    *,
    kind: HandoffKind,
    merchant_id: str,
    chat_platform: str,
    chat_user_id: str,
    chat_channel_id: str,
    request_text: str,
    ttl_seconds: int = HANDOFF_TTL_SECONDS,
    amendment_draft: dict[str, Any] | None = None,
) -> Handoff:
    """Create a new single-use handoff. Never reuses a token (secrets.token_urlsafe
    mirrors the existing cancel_token capability pattern).

    amendment_draft (S11 Phase 4, Q-020) is set only for kind=AMENDMENT: the
    drafted amendment (MerchantAgent.draft_amendment output) plus the cart it
    was drafted against, carried between drafting and approval."""
    now = _now()
    handoff = Handoff(
        token=secrets.token_urlsafe(32),
        kind=kind,
        merchant_id=merchant_id,
        chat_platform=chat_platform,
        chat_user_id=chat_user_id,
        chat_channel_id=chat_channel_id,
        request_text=request_text,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        amendment_draft=amendment_draft,
    )
    session.add(handoff)
    session.flush()
    return handoff


def resolve_handoff(session: Session, token: str) -> Handoff:
    """Look up a handoff by token without consuming it. Fails loud (R0.5) on
    unknown / expired / already-consumed — never a silent fallback identity."""
    handoff = session.get(Handoff, token)
    if handoff is None:
        raise HandoffError(_REASON_NOT_FOUND, f"handoff token not found: {token}")
    if handoff.consumed_at is not None:
        raise HandoffError(_REASON_CONSUMED, f"handoff already consumed: {token}")
    if handoff.expires_at < _now():
        raise HandoffError(_REASON_EXPIRED, f"handoff expired: {token}")
    return handoff


def consume_handoff(
    session: Session, token: str, *, result_policy_id: str | None = None
) -> Handoff:
    """Single-use: mark a handoff consumed. Raises the same closed-set errors as
    resolve_handoff; a second call against the same token raises
    authority.handoff_consumed (never a silent no-op)."""
    handoff = resolve_handoff(session, token)
    handoff.consumed_at = _now()
    if result_policy_id is not None:
        handoff.result_policy_id = result_policy_id
    session.add(handoff)
    session.flush()
    return handoff


def require_active_policy(session: Session, buyer_user_handle: str) -> IntentPolicy:
    """The buyer must hold an active, unexpired IntentPolicy before an errand
    can run (plan step: "check for an active policy before shopping"). Fails
    loud with authority.policy_unsigned — never a silent default policy_id
    (R0.5/R0.8). Policies are reached via the credential join, matching
    surfaces/studio.py's _current_aggregate / policy_blast_radius scoping."""
    now_unix = int(datetime.now(UTC).timestamp())
    credential_ids = [c.credential_id for c in get_user_credentials(session, buyer_user_handle)]
    if credential_ids:
        policies = list(
            session.exec(
                select(IntentPolicy).where(
                    IntentPolicy.webauthn_credential_id.in_(credential_ids),  # type: ignore[attr-defined]
                    IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
                    IntentPolicy.expires_at > now_unix,
                )
            ).all()
        )
        if policies:
            return policies[0]
    raise HandoffError(_REASON_POLICY_UNSIGNED, f"no active signed policy for {buyer_user_handle}")
