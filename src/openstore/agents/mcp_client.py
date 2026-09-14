# OpenStore — InProcessMCPClient (S7.2).
# A real, non-stub MCP client for the buyer agent. It authenticates as an OAuth
# client against the local DB (real signed ES256 JWT), then dispatches each tool
# call to handle_mcp_request in-process. No HTTP, no network.

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from openstore.buyer_config import MerchantOrigin
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

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict[str, Any]:
        return await self.call("search_products", {"query": query, "tags": tags, "limit": limit})

    async def get_order(self, checkout_id: str) -> dict[str, Any]:
        return await self.call("get_order", {"checkout_id": checkout_id})

    async def list_campaigns(self) -> dict[str, Any]:
        return await self.call("list_campaigns", {})


class HttpMCPClient:
    """An MCP client for ONE merchant reachable over HTTP (S12, wire: S17).

    Speaks the JSON-RPC 2.0 wire path on /agent/mcp (DECISION-036): a lazy
    `initialize` handshake once per client, then `tools/call` per tool.
    Same call/return shape as InProcessMCPClient ({success,data,error}) so the
    two stay interchangeable at the buyer agent boundary. require_auth is
    explicit at each call site (never inferred from the tool name) so search —
    which needs no scope — never touches /oauth/token or sends a header.
    R0.10: this client only ever holds an OAuth bearer token, never PSP/signing
    material.
    """

    # Pinned per Q-036: must match surfaces/mcp_server.MCP_PROTOCOL_VERSION.
    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, merchant: MerchantOrigin, *, timeout_seconds: float = 8.0) -> None:
        self.merchant = merchant
        self._timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._rpc_id = 0
        self._initialized = False

    async def _ensure_token(self) -> None:
        """Obtain (once) an OAuth bearer token via client_credentials and cache it.

        Never logs the client_secret or the resulting access_token.
        """
        if self._access_token is not None:
            return
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            resp = await client.post(
                f"{self.merchant.base_url}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.merchant.client_id,
                    "client_secret": self.merchant.client_secret,
                },
            )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            # Fail loud (R0.5): a 200 with no token means a broken origin, not
            # a silently-anonymous call.
            raise RuntimeError(
                f"oauth/token for merchant {self.merchant.merchant_id!r} returned no access_token"
            )
        self._access_token = token

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _rpc(
        self, method: str, params: dict[str, Any] | None, *, auth: bool
    ) -> Any:
        """One JSON-RPC request; returns the result payload (fail loud)."""
        headers: dict[str, str] = {}
        if auth:
            await self._ensure_token()
            headers["Authorization"] = f"Bearer {self._access_token}"
        envelope: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            envelope["params"] = params
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            resp = await client.post(
                f"{self.merchant.base_url}/agent/mcp",
                json=envelope,
                headers=headers,
            )
        if resp.status_code == 401:
            raise RuntimeError(
                f"MCP {method} for merchant {self.merchant.merchant_id!r} "
                "rejected: invalid bearer token"
            )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            raise RuntimeError(
                f"MCP {method} for merchant {self.merchant.merchant_id!r} "
                f"protocol error {err.get('code')}: {err.get('message')}"
            )
        return body.get("result", {})

    async def _ensure_initialized(self, *, auth: bool) -> None:
        """Run the MCP initialize handshake once per client (no-op after)."""
        if self._initialized:
            return
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "openstore-buyer", "version": "0.1.0"},
            },
            auth=auth,
        )
        server_version = result.get("protocolVersion")
        if server_version != self.PROTOCOL_VERSION:
            raise RuntimeError(
                f"MCP initialize for merchant {self.merchant.merchant_id!r}: "
                f"server speaks {server_version!r}, client speaks {self.PROTOCOL_VERSION!r}"
            )
        headers: dict[str, str] = {}
        if auth:
            await self._ensure_token()
            headers["Authorization"] = f"Bearer {self._access_token}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            resp = await client.post(
                f"{self.merchant.base_url}/agent/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )
        if resp.status_code not in (200, 202):
            raise RuntimeError(
                f"MCP notifications/initialized for merchant "
                f"{self.merchant.merchant_id!r} returned {resp.status_code}"
            )
        self._initialized = True

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, require_auth: bool
    ) -> dict[str, Any]:
        """Dispatch one MCP tool call over HTTP to this merchant's /agent/mcp."""
        await self._ensure_initialized(auth=require_auth)
        result = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            auth=require_auth,
        )
        content = result.get("content", [])
        if not content or content[0].get("type") != "text":
            # Fail loud (R0.5): a tools/call result without text content means
            # a broken origin, not an empty answer.
            raise RuntimeError(
                f"MCP tools/call {tool_name!r} for merchant "
                f"{self.merchant.merchant_id!r} returned malformed content"
            )
        try:
            tool_result = json.loads(content[0]["text"])
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"MCP tools/call {tool_name!r} for merchant "
                f"{self.merchant.merchant_id!r} returned invalid JSON content: {e}"
            ) from e
        if not isinstance(tool_result, dict) or "success" not in tool_result:
            raise RuntimeError(
                f"MCP tools/call {tool_name!r} for merchant "
                f"{self.merchant.merchant_id!r} returned a malformed tool payload"
            )
        return tool_result

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict[str, Any]:
        return await self.call(
            "search_products",
            {"query": query, "tags": tags, "limit": limit},
            require_auth=False,
        )

    async def get_order(self, checkout_id: str) -> dict[str, Any]:
        return await self.call("get_order", {"checkout_id": checkout_id}, require_auth=True)

    async def list_campaigns(self) -> dict[str, Any]:
        # catalog:read, like search — no scope gate at surfaces/mcp_server.py.
        return await self.call("list_campaigns", {}, require_auth=False)


class FederatingMCPClient:
    """Fans search out across many merchant origins; routes cart/checkout to one.

    create_cart/checkout_* are deliberately NOT fanned out here — the caller
    already knows which merchant a cart line belongs to (stamped onto each
    search result item) and reaches that merchant directly via client_for.
    """

    def __init__(
        self, merchants: list[MerchantOrigin], *, search_timeout_seconds: float = 5.0
    ) -> None:
        self._clients: dict[str, HttpMCPClient] = {
            m.merchant_id: HttpMCPClient(m, timeout_seconds=search_timeout_seconds)
            for m in merchants
        }

    def client_for(self, merchant_id: str) -> HttpMCPClient:
        try:
            return self._clients[merchant_id]
        except KeyError:
            raise ValueError(
                f"Unknown merchant_id {merchant_id!r}; known merchants: {sorted(self._clients)}"
            ) from None

    def merchants(self) -> list[MerchantOrigin]:
        """Every merchant this buyer can reach, in registration order —
        used to list/page a full menu across stores (buyer_agent.py)."""
        return [client.merchant for client in self._clients.values()]

    async def search_products(
        self, query: str, tags: list[str] | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """Search every merchant in parallel and merge results into one list.

        A merchant that errors or times out contributes [] and is logged —
        one bad origin must never fail the whole search (R0.5: loud, not
        crashing the buyer's turn).
        """

        async def _search_one(merchant_id: str, client: HttpMCPClient) -> list[dict[str, Any]]:
            result = await client.search_products(query, tags=tags, limit=limit)
            if not result.get("success"):
                logger.warning(
                    "federated search: merchant %s returned an error: %s",
                    merchant_id,
                    result.get("error"),
                )
                return []
            items = result.get("data", {}).get("items", [])
            # Stamp merchant_id from the client that made the call, never from
            # the remote payload — a merchant must not be able to claim to be
            # another merchant.
            stamped = []
            for item in items:
                item = dict(item)
                item["merchant_id"] = merchant_id
                stamped.append(item)
            return stamped

        results = await asyncio.gather(
            *(_search_one(merchant_id, client) for merchant_id, client in self._clients.items()),
            return_exceptions=True,
        )

        merged: list[dict[str, Any]] = []
        for merchant_id, outcome in zip(self._clients.keys(), results, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "federated search: merchant %s unreachable: %s", merchant_id, outcome
                )
                continue
            merged.extend(outcome)

        return {"success": True, "data": {"items": merged, "count": len(merged)}}

    async def list_campaigns(self) -> dict[str, Any]:
        """Fan out campaign discovery the same way search fans out, stamping
        each campaign with the merchant it actually came from (never from the
        remote payload). One unreachable origin contributes nothing rather than
        failing the buyer's turn."""

        async def _list_one(merchant_id: str, client: HttpMCPClient) -> list[dict[str, Any]]:
            result = await client.list_campaigns()
            if not result.get("success"):
                logger.warning(
                    "federated campaigns: merchant %s returned an error: %s",
                    merchant_id,
                    result.get("error"),
                )
                return []
            stamped = []
            for campaign in result.get("data", {}).get("campaigns", []):
                campaign = dict(campaign)
                campaign["merchant_id"] = merchant_id
                stamped.append(campaign)
            return stamped

        results = await asyncio.gather(
            *(_list_one(merchant_id, client) for merchant_id, client in self._clients.items()),
            return_exceptions=True,
        )

        merged: list[dict[str, Any]] = []
        for merchant_id, outcome in zip(self._clients.keys(), results, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "federated campaigns: merchant %s unreachable: %s", merchant_id, outcome
                )
                continue
            merged.extend(outcome)

        return {"success": True, "data": {"campaigns": merged, "count": len(merged)}}
