"""A2A (Agent2Agent) protocol adapter (INTEROP_SPEC §6.4).

Translates between A2A protocol messages and CommerceCore operations.
The merchant reasoning agent keeps its A2A surface as-is; this adapter
provides the protocol-level translation for the interop layer.

A2A uses JSON-RPC 2.0 over HTTP. The adapter maps A2A task messages to
CommerceCore operations and returns A2A-shaped responses.

R6.4a — The merchant reasoning agent MUST NOT import core write functions.
This adapter delegates to CommerceCore for read-only operations only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openstore.core.api import CommerceCore


class A2AAdapter:
    """Thin adapter between A2A protocol and CommerceCore (INTEROP_SPEC §6.4).

    A2A is read-only for the merchant side: catalog queries, policy lookups,
    and status checks. No money writes.
    """

    def __init__(self, core: CommerceCore):
        self.core = core

    def handle_task(self, task_msg: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an A2A task message (JSON-RPC 2.0).

        Routes to the appropriate handler based on the task's skill_id.
        Returns an A2A-shaped response.
        """
        params = task_msg.get("params", {})
        skill_id = params.get("skill_id", task_msg.get("method", ""))

        handlers = {
            "catalog_qa": self._handle_catalog_qa,
            "policy_lookup": self._handle_policy_lookup,
            "order_status": self._handle_order_status,
        }

        handler = handlers.get(skill_id)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": task_msg.get("id"),
                "error": {"code": -32601, "message": f"Unknown skill: {skill_id}"},
            }

        try:
            result = handler(params)
            return {
                "jsonrpc": "2.0",
                "id": task_msg.get("id"),
                "result": result,
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": task_msg.get("id"),
                "error": {"code": -32000, "message": str(e)},
            }

    def _handle_catalog_qa(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a catalog Q&A query."""
        query = params.get("query", "").lower()
        # Read-only: search the catalog
        results = []
        for sku, prod in self.core._catalog.items():
            if query in sku.lower() or query in str(prod.get("tags", [])):
                results.append({
                    "sku": sku,
                    "title": prod.get("title", sku),
                    "price_paise": prod.get("unit_price_paise", 0),
                    "tags": prod.get("tags", []),
                })
        return {
            "answer": f"Found {len(results)} matching products",
            "products": results,
            "advisory": True,
        }

    def _handle_policy_lookup(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return the current merchant policy (read-only)."""
        return {
            "merchant_id": self.core._merchant_id,
            "currency": "INR",
            "advisory": True,
        }

    def _handle_order_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Check order status (read-only)."""
        order_id = params.get("order_id", "")
        checkout = self.core._checkouts.get(order_id)
        if checkout is None:
            return {"status": "not_found", "advisory": True}
        return {
            "status": checkout.status,
            "order_id": order_id,
            "advisory": True,
        }
