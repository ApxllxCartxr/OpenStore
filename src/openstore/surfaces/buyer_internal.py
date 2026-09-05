# OpenStore — buyer-side internal HTTP surface (S12 step 8).
#
# The federated buyer agent (buyer_cli.py) runs as its own process talking
# HTTP MCP to N merchants, so a merchant's studio.py cannot resume the
# buyer's conversation in-process the way the legacy single-process path
# does. Instead a merchant POSTs a best-effort ping here once a buyer
# finishes signing a policy, and this endpoint decides what (if anything)
# to do about it.
#
# SECURITY INVARIANT — read before touching this file: the request body
# carries NO AUTHORITY. It is a bare hint meaning "some buyer may have
# finished signing at merchant M" — nothing more. This handler MUST NOT
# treat any field in the body as proof a policy exists, place an order,
# unlock spend, or mutate policy state from the body alone, or accept a
# policy/policy_id/cap value the body claims. The only thing this handler
# ever acts on is the result of independently calling resolve_policy
# against the named merchant over this buyer's own authenticated MCP
# channel (FederatingMCPClient.client_for). A forged or replayed POST here
# therefore gains an attacker nothing — worst case this handler re-checks
# and finds no active policy, and does nothing. This is what keeps
# DECISION-022's "no signing hub, no new trust root" line intact: the
# merchant is a doorbell, never a source of truth for the buyer.
#
# WHO to check is likewise never taken from the body: handoff_token is
# resolved only against BuyerAgent's own record of tokens it minted itself
# (agent.resolve_pending_handoff), populated when it called
# create_policy_handoff — never derived from anything the caller asserts.
# An unknown token (forged, replayed, or stale) resolves to nothing.
#
# Always returns 202 regardless of outcome, so this endpoint leaks nothing
# about whether a given buyer or merchant exists.

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter
from pydantic import BaseModel

from openstore.agents.buyer_agent import BuyerAgent
from openstore.buyer_config import BuyerSettings
from openstore.config import Settings
from openstore.core.database import get_session
from openstore.core.shopping_session import find_active_session
from openstore.notifier import send_dm

logger = logging.getLogger("openstore.buyer_internal")


class SigningCompleteBody(BaseModel):
    handoff_token: str
    merchant_id: str


def create_buyer_internal_router(config: BuyerSettings, agent: BuyerAgent) -> APIRouter:
    router = APIRouter()

    @router.post("/internal/signing-complete", status_code=202)
    async def signing_complete(body: SigningCompleteBody) -> dict[str, Any]:
        pending = agent.resolve_pending_handoff(body.handoff_token)
        if pending is None or pending["merchant_id"] != body.merchant_id:
            logger.info(
                "signing-complete: no pending handoff for this token (merchant=%s)",
                body.merchant_id,
            )
            return {}

        user_id = f"{pending['chat_platform']}:{pending['chat_user_id']}"
        client = agent.mcp.client_for(body.merchant_id)
        # The only thing that can move this forward: an independently
        # verified ACTIVE policy, fetched just now over our own
        # authenticated channel — never anything the notification claimed.
        result = await client.call("resolve_policy", {"user_id": user_id}, require_auth=True)
        if not result.get("success"):
            logger.info(
                "signing-complete: resolve_policy found no active policy for %s at %s",
                user_id,
                body.merchant_id,
            )
            return {}

        # BuyerAgent/apply_migrations/send_dm only ever touch the
        # attributes BuyerSettings and Settings share (config.discord,
        # config.database) — same duck-typed relationship buyer_cli.py's
        # settings_view documents, not re-derived here.
        settings_view = cast(Settings, config)
        db_session = get_session(settings_view)
        try:
            sess = find_active_session(
                db_session,
                pending["chat_platform"],
                pending["chat_user_id"],
                pending["chat_channel_id"],
            )
        finally:
            db_session.close()
        if sess is None:
            logger.info("signing-complete: no parked ShoppingSession for %s", user_id)
            return {}

        await send_dm(
            settings_view,
            pending["chat_user_id"],
            f"Signed at {body.merchant_id} — reply to continue your order.",
        )
        return {}

    return router
