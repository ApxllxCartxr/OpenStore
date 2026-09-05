# tests/stage13/test_buyer_ux_seams.py
# The buyer-facing surface had three places where a failure produced NO visible
# output at all — the exact inverse of R0.5 at the one surface a human watches:
#
#   1. BuyerPlanError / LLMError propagated out of the message handlers with no
#      except anywhere, so the typing indicator stopped and the buyer got
#      nothing, indistinguishable from the bot ignoring them.
#   2. A ShoppingSession that timed out was invisible: the buyer's reply was
#      silently re-read as a brand-new goal with no explanation.
#   3. `!cancel` required a checkout_id that build_shop_result_embed
#      deliberately never prints (a sentinel test pins that it must not), so
#      the documented cancel path was unusable from chat.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from openstore.agents.buyer_agent import _AGENT_FAILURE_MESSAGE, _report_agent_failures
from openstore.agents.buyer_graph import BuyerPlanError
from openstore.agents.llm import LLMError
from openstore.core.shopping_session import (
    create_session,
    find_active_session,
    take_expired_session,
)
from openstore.models import ShoppingSessionState


class _Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str = "", **kwargs: Any) -> None:
        self.sent.append(content)


def _message() -> Any:
    return SimpleNamespace(channel=_Channel())


@pytest.mark.parametrize(
    "error",
    [BuyerPlanError("plan: hallucinated_sku:ghost"), LLMError("every provider failed")],
)
async def test_agent_failure_reaches_the_buyer(error):
    message = _message()
    async with _report_agent_failures(message, "trace_x"):
        raise error
    assert message.channel.sent == [_AGENT_FAILURE_MESSAGE]


async def test_agent_failure_message_never_leaks_the_raw_error():
    """The exception text can carry a model transcript; it goes to logs and
    #alerts, not to the buyer."""
    message = _message()
    async with _report_agent_failures(message, "trace_x"):
        raise BuyerPlanError("plan: malformed_selection: {'secret': 'internal'}")
    assert "malformed_selection" not in message.channel.sent[0]
    assert "secret" not in message.channel.sent[0]


async def test_unexpected_errors_still_propagate():
    """Only the two agent-layer failures are absorbed. A ValueError is a bug and
    must not be flattened into a friendly message (R0.5)."""
    message = _message()
    with pytest.raises(ValueError):
        async with _report_agent_failures(message, "trace_x"):
            raise ValueError("a real bug")
    assert message.channel.sent == []


def _park(session, *, ttl_seconds: int) -> Any:
    parked = create_session(
        session,
        chat_platform="discord",
        chat_user_id="u1",
        chat_channel_id="c1",
        policy_id="pol_1",
        trace_id="trace_1",
        goal="vanilla gelato",
        messages=[],
    )
    parked.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=ttl_seconds)
    session.add(parked)
    session.commit()
    return parked


def test_expired_session_is_claimed_once_and_names_the_goal(session):
    _park(session, ttl_seconds=-1)

    assert find_active_session(session, "discord", "u1", "c1") is None

    stale = take_expired_session(session, "discord", "u1", "c1")
    assert stale is not None
    assert stale.goal == "vanilla gelato"
    assert stale.state == ShoppingSessionState.EXPIRED
    session.commit()

    # Claimed exactly once: a second reply must not re-announce the timeout.
    assert take_expired_session(session, "discord", "u1", "c1") is None


def test_live_session_is_never_claimed_as_expired(session):
    live = _park(session, ttl_seconds=600)
    assert take_expired_session(session, "discord", "u1", "c1") is None
    assert live.state == ShoppingSessionState.AWAITING_REPLY


def test_result_embed_still_hides_the_checkout_id():
    """Pinned deliberately: `!cancel` takes no argument BECAUSE the id is never
    shown. If this ever starts leaking, the cancel UX rationale changes too."""
    from openstore.agents.buyer_agent import build_shop_result_embed

    embed = build_shop_result_embed(
        {
            "allowed": True,
            "amount_minor": 21000,
            "short_url": "https://pay.example/x",
            "checkout_id": "chk_internal",
        }
    )
    assert "chk_internal" not in str(embed)
    assert "!cancel" in embed["description"]
