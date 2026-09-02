# tests/test_mcp_client.py
# Unit tests for InProcessMCPClient (S7.2): real OAuth JWT + in-process dispatch.

from __future__ import annotations

import asyncio

import pytest
from openstore.agents.mcp_client import InProcessMCPClient
from openstore.config import Settings


def _init(config: Settings) -> None:
    from openstore.core.database import init_database

    init_database(config)


def test_search_products_anon_succeeds(settings, session):
    """search_products needs no scope; the client's token still round-trips."""
    _init(settings)
    client = InProcessMCPClient(settings)
    result = asyncio.run(client.call("search_products", {"query": "gelato", "limit": 5}))
    assert result["success"] is True
    assert isinstance(result.get("data", {}).get("items"), list)


def test_unknown_tool_rejected(settings, session):
    _init(settings)
    client = InProcessMCPClient(settings)
    result = asyncio.run(client.call("nope_tool", {}))
    assert result["success"] is False
    assert result["error"]["reason_code"] == "auth.unknown_tool"


def test_money_tool_requires_scope(settings, session):
    """A client with no cart:write scope must be rejected by _require_scope
    (a hard CommerceError, R0.5 — insufficient scope never silently proceeds)."""
    _init(settings)
    client = InProcessMCPClient(settings, scopes=["catalog:read"])
    from openstore.core.api import CommerceError

    with pytest.raises(CommerceError) as ei:
        asyncio.run(client.call("create_cart", {}))
    assert ei.value.reason_code == "auth.insufficient_scope"


def test_scope_granted_for_cart(settings, session):
    """With cart:write the tool is authorized; a missing cart simply errors
    out on data, not on scope — proving the scope was honored."""
    _init(settings)
    client = InProcessMCPClient(
        settings,
        scopes=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
    )
    result = asyncio.run(client.call("create_cart", {"merchant_id": "m", "items": []}))
    assert result["success"] is False
    assert result["error"]["reason_code"] != "auth.insufficient_scope"


def test_cached_token_single_creation(settings, session):
    """The access token is minted once and cached (module-level key uses the
    per-call creation but the client instance caches after first use)."""
    _init(settings)
    client = InProcessMCPClient(settings)
    t1 = client._access_token
    asyncio.run(client.call("search_products", {"query": "x"}))
    t2 = client._access_token
    assert t1 is None
    assert t2 is not None
    # second call reuses the same cached token
    asyncio.run(client.call("search_products", {"query": "y"}))
    assert client._access_token == t2
