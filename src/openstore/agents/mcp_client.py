# OpenStore — InProcessMCPClient (S7.2).
# A real, non-stub MCP client for the buyer agent. It authenticates as an OAuth
# client against the local DB (real signed ES256 JWT), then dispatches each tool
# call to handle_mcp_request in-process. No HTTP, no network.

from __future__ import annotations

import logging
from typing import Any

from openstore.config import Settings

logger = logging.getLogger("openstore.mcp_client")

# Scopes required for the buyer agent's money tools (cart + checkout).
BUYER_SCOPES: list[str] = [
    "catalog:read",
    "cart:write",
    "checkout:initiate",
    "checkout:confirm",
    "order:read",
]


class InProcessMCPClient:
    """
    An MCP client for the buyer agent that runs fully inside the sidecar
    process. It obtains a real OAuth bearer token (create_token_pair), stores
    the token in the local DB, and routes every tool call through
    handle_mcp_request with the token's granted scopes (R0.9: all money actions
    still go through the compiler server-side; this client never holds PSP
    credentials, R0.10).

    The token is created lazily on first use, so the client can be constructed
    before the DB engine/session exists.
    """

    def __init__(
        self,
        config: Settings,
        client_id: str = "buyer-agent",
        scopes: list[str] | None = None,
    ) -> None:
        self.config = config
        self.client_id = client_id
        self.scopes = scopes if scopes is not None else list(BUYER_SCOPES)
        self._access_token: str | None = None

    def _ensure_token(self) -> None:
        """Create (once) a real OAuth token pair and cache the access token."""
        if self._access_token is not None:
            return
        from openstore.core.database import get_session, init_database
        from openstore.core.oauth import create_token_pair

        init_database(self.config)
        session = get_session(self.config)
        try:
            access, _ = create_token_pair(
                session=session,
                client_id=self.client_id,
                scopes=self.scopes,
                subject=self.client_id,
            )
            session.commit()
        finally:
            session.close()
        self._access_token = access

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call with this client's granted scopes."""
        self._ensure_token()
        assert self._access_token is not None
        from openstore.core.database import get_session, session_scope
        from openstore.core.oauth import validate_access_token
        from openstore.surfaces.mcp_server import handle_mcp_request

        session = get_session(self.config)
        try:
            token_record = validate_access_token(session, self._access_token)
            scopes = list(token_record.scopes)
            client_id = token_record.client_id
        finally:
            session.close()

        # session_scope commits on success / rolls back on error (INV-11) — a
        # plain get_session()+close() silently drops every write this call makes.
        with session_scope(self.config) as session:
            return handle_mcp_request(
                config=self.config,
                session=session,
                tool_name=tool_name,
                arguments=arguments,
                token_scopes=scopes,
                client_id=client_id,
            )

    async def search_products(self, query: str, limit: int = 10) -> dict[str, Any]:
        return await self.call("search_products", {"query": query, "limit": limit})

    async def get_order(self, checkout_id: str) -> dict[str, Any]:
        return await self.call("get_order", {"checkout_id": checkout_id})
