"""MCP adapter (INTEROP_SPEC §6.1) — mechanical claims-extraction + core call.

MCP is the one protocol this document can specify completely, because it is the
one already running (`openstore.mcp_server`). This adapter is the refactor
target: the tool body moves here as claims-extraction, and the money decision
happens in `openstore.core.api.CommerceCore`. The live MCP server may continue
to call the core directly; this module is the canonical adapter surface and is
what discovery advertises.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple

from openstore.core.api import CommerceCore
from openstore.core.types import (
    Actor,
    AuthorityPresentation,
    DeliveryAddress,
    LineItemRequest,
    SearchQuery,
)

# External field names on the MCP tool boundary (R0.I3 / §6). Every one of these
# MUST appear in mapping.md.
MCP_EXTERNAL_FIELDS: Tuple[str, ...] = (
    "sku", "qty", "query", "limit", "delivery", "jws", "policy", "idempotency_key",
)


class McpAdapter:
    def __init__(self, core: CommerceCore):
        self.core = core

    def _actor(self, client_id: str, scopes: Tuple[str, ...], session_id: str | None = None) -> Actor:
        from openstore.core.types import normalize_scopes
        return Actor(
            subject=client_id or "mcp:anon",
            display_name=client_id or "mcp-agent",
            scopes=normalize_scopes(scopes),
            protocol="mcp",
            protocol_session_id=session_id,
            auth_method="oauth2_bearer",
        )

    def search_products(self, query: str = "", limit: int = 50) -> list:
        return self.core.search_products(SearchQuery(query=query, limit=limit))

    def get_product(self, sku: str):
        return self.core.get_product(sku)

    def create_cart(self, items: Sequence[Mapping[str, Any]], client_id: str = "",
                    scopes: Tuple[str, ...] = ("cart:write",)):
        reqs = tuple(LineItemRequest(sku=i["sku"], qty=int(i["qty"])) for i in items)
        return self.core.create_cart(self._actor(client_id, scopes), reqs)

    def update_cart(self, cart_id: int, items: Sequence[Mapping[str, Any]], client_id: str = "",
                    scopes: Tuple[str, ...] = ("cart:write",)):
        reqs = tuple(LineItemRequest(sku=i["sku"], qty=int(i["qty"])) for i in items)
        return self.core.update_cart(self._actor(client_id, scopes), cart_id, reqs)

    def initiate_checkout(self, cart_id: int, delivery: str = "",
                          client_id: str = "", scopes: Tuple[str, ...] = ("checkout:initiate",)):
        return self.core.initiate_checkout(
            self._actor(client_id, scopes), cart_id, DeliveryAddress(raw=delivery))

    def checkout_confirm(self, checkout_id: str, policy_token: Mapping[str, Any],
                         policy_json: Mapping[str, Any] | None, client_id: str = "",
                         scopes: Tuple[str, ...] = ("checkout:confirm",),
                         idempotency_key: str = "") -> Any:
        # §6.1a — the legacy `jws` OTP/mandate path stays on the MCP adapter, not
        # promoted into the core. Here we present it as a native_webauthn authority.
        authority = AuthorityPresentation(
            scheme="native_webauthn", raw=dict(policy_token), policy_json=policy_json)
        return self.core.confirm_checkout(
            self._actor(client_id, scopes), checkout_id, authority, idempotency_key)

    def get_order(self, checkout_id: str, client_id: str = ""):
        return self.core.get_order(self._actor(client_id, ("catalog:read",)), checkout_id)

    def get_evidence(self, checkout_id: str, client_id: str = ""):
        return self.core.get_evidence(self._actor(client_id, ("catalog:read",)), checkout_id)
