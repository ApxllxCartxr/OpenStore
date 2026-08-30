"""ACP adapter (stretch / transport-abstraction seam).

OpenStore is transport-neutral: the same MerchantRuntime can be exposed over MCP
(see openstore.mcp_server) or over ACP. This module is a thin ACP-shaped wrapper
that dispatches tool calls to the runtime directly, demonstrating the seam without
requiring a full ACP HTTP server. Swap in a real ACP transport later by serving
`agent_card()` and routing `invoke()` over HTTP.
"""
from typing import Dict

from openstore.models import Mandate, Quote, QuoteItem


class AcpAgent:
    def __init__(self, runtime):
        self.rt = runtime

    def agent_card(self) -> Dict:
        disc = self.rt.discover()
        return {
            "protocol": "acp",
            "name": disc.merchant.legal_name,
            "description": "OpenStore merchant agent (ACP surface)",
            "did": disc.trust_anchor.did,
            "skills": [
                "discover", "create_mandate", "create_quote", "create_order",
                "cancel_order", "refund_order", "raise_dispute",
                "release_order", "return_order", "get_trust_receipt", "get_daily_anchor",
            ],
        }

    def invoke(self, tool: str, **kwargs):
        rt = self.rt
        if tool == "discover":
            return rt.discover().model_dump()
        if tool == "create_quote":
            return rt.create_quote([QuoteItem.model_validate(i) for i in kwargs["items"]]).model_dump()
        if tool == "create_mandate":
            return {"receipt_id": rt.submit_mandate(Mandate.model_validate(kwargs["mandate"]))}
        if tool == "create_order":
            import asyncio

            return asyncio.run(rt.create_order(Quote.model_validate(kwargs["quote"]), Mandate.model_validate(kwargs["mandate"])))
        if tool == "cancel_order":
            import asyncio

            return asyncio.run(rt.cancel_order(kwargs["order_id"], kwargs["reason"]))
        if tool == "refund_order":
            import asyncio

            return asyncio.run(rt.refund_order(kwargs["order_id"], kwargs["reason"]))
        if tool == "raise_dispute":
            return rt.raise_dispute(kwargs["order_id"], kwargs["raised_by"], kwargs["reason"], kwargs.get("evidence_refs"))
        if tool == "release_order":
            return rt.release_order(kwargs["order_id"], kwargs["amount_paise"], kwargs["released_by"])
        if tool == "return_order":
            return rt.return_order(kwargs["order_id"], kwargs["items"], kwargs["reason"])
        if tool == "get_trust_receipt":
            r = rt.get_trust_receipt(kwargs["receipt_id"])
            return r.model_dump() if r else {"error": "not found"}
        if tool == "get_daily_anchor":
            return rt.anchor_today()
        raise ValueError(f"unknown tool: {tool}")
