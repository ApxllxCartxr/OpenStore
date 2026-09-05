# OpenStore core — ShoppingSession bridge (S13: conversational shopping)
#
# A shopping session is a durable row that parks a buyer/LLM conversation
# between Discord messages when BuyerGraph's agent_step asks the buyer
# something instead of finalizing a cart. Unlike Handoff (core/handoff.py,
# resumed by a web link click after an out-of-band ceremony), a
# ShoppingSession is resumed by the buyer's next chat message — no token,
# no link, just an identity-keyed lookup on the next incoming message.
#
# Short TTL (see DEFAULT_TTL_SECONDS): this parks an active chat exchange,
# not an async "go sign something" wait.

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.models import ShoppingSession, ShoppingSessionState

DEFAULT_TTL_SECONDS = 600  # 10 minutes — an active chat exchange, not a signing wait


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def find_active_session(
    session: Session, chat_platform: str, chat_user_id: str, chat_channel_id: str
) -> ShoppingSession | None:
    """Most recent AWAITING_REPLY session for this identity, or None if there
    isn't one or it's expired. Expiry is checked at read time (lazy, same
    style as Handoff.resolve_handoff) — no background sweeper; an expired row
    is simply treated as if it doesn't exist, and the buyer must start fresh
    with !shop."""
    rows = session.exec(
        select(ShoppingSession)
        .where(
            ShoppingSession.chat_platform == chat_platform,
            ShoppingSession.chat_user_id == chat_user_id,
            ShoppingSession.chat_channel_id == chat_channel_id,
            ShoppingSession.state == ShoppingSessionState.AWAITING_REPLY,
        )
        .order_by(ShoppingSession.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    for row in rows:
        if row.expires_at >= _now():
            return row
    return None


def create_session(
    session: Session,
    *,
    chat_platform: str,
    chat_user_id: str,
    chat_channel_id: str,
    policy_id: str,
    trace_id: str,
    goal: str,
    messages: list[dict[str, Any]],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> ShoppingSession:
    now = _now()
    sess = ShoppingSession(
        id=f"sess_{secrets.token_hex(16)}",
        chat_platform=chat_platform,
        chat_user_id=chat_user_id,
        chat_channel_id=chat_channel_id,
        policy_id=policy_id,
        trace_id=trace_id,
        goal=goal,
        messages=messages,
        turns_used=0,
        state=ShoppingSessionState.AWAITING_REPLY,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(sess)
    session.flush()
    return sess


def advance_session(
    session: Session,
    sess: ShoppingSession,
    *,
    new_messages: list[dict[str, Any]],
    turns_used: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Persist a continued turn: updated transcript, refreshed TTL. Caller
    decides the session stays AWAITING_REPLY (another question) — closing it
    to a terminal state is close_session's job."""
    now = _now()
    sess.messages = new_messages
    sess.turns_used = turns_used
    sess.updated_at = now
    sess.expires_at = now + timedelta(seconds=ttl_seconds)
    session.add(sess)
    session.flush()


def close_session(session: Session, sess: ShoppingSession, state: ShoppingSessionState) -> None:
    """Terminal transition: COMPLETED (cart produced), EXPIRED (turn cap
    hit), or CANCELLED (buyer said cancel/nevermind, or a fresh !shop
    superseded it). Never AWAITING_REPLY — use advance_session for that."""
    if state == ShoppingSessionState.AWAITING_REPLY:
        raise ValueError("close_session cannot transition to AWAITING_REPLY")
    sess.state = state
    sess.updated_at = _now()
    session.add(sess)
    session.flush()
