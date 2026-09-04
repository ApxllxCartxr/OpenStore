# tests/stage11/test_buyer_bot_handoff.py
# S11 Phase 2: BuyerBot checks for an active signed policy before shopping.
# With none, it creates a handoff and DMs (here: replies in-channel to) a
# signing link instead of shopping with a hardcoded "default_policy" — the
# resolved policy_id is used once one exists.

from __future__ import annotations

from openstore.agents.buyer_agent import BuyerAgent, BuyerBot
from openstore.config import merchant_id
from openstore.models import Handoff
from sqlmodel import select


class _FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


class _FakeAuthor:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeMessage:
    def __init__(self, author_id: int, channel_id: int, msg_id: int = 1):
        self.author = _FakeAuthor(author_id)
        self.channel = _FakeChannel(channel_id)
        self.id = msg_id


class _NoopMCPClient:
    async def call(self, tool_name, arguments):
        raise AssertionError("shop() must not be reached without a signed policy")

    async def search_products(self, query, limit=10):
        return {"success": True, "data": {"items": []}, "error": None}

    async def get_order(self, checkout_id):
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}


async def test_handle_shop_without_policy_creates_handoff_and_sends_link(settings, session):
    bot = BuyerBot(settings, BuyerAgent(settings, _NoopMCPClient()))
    message = _FakeMessage(author_id=555001, channel_id=777001)

    await bot._handle_shop(message, "vegan gelato please")

    assert len(message.channel.sent) == 1
    assert "/intent/studio?token=" in message.channel.sent[0]

    rows = list(session.exec(select(Handoff).where(Handoff.chat_user_id == "555001")))
    assert len(rows) == 1
    handoff = rows[0]
    assert handoff.chat_platform == "discord"
    assert handoff.chat_channel_id == "777001"
    assert handoff.request_text == "vegan gelato please"
    assert handoff.merchant_id == merchant_id(settings)
    assert handoff.consumed_at is None
