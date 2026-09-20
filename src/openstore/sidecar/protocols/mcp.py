"""MCP — the envelope the demo chat actually speaks.

`tools/list` and the tool schemas come from the closed action set in
`core/codes.py`, so a tool that exists here and not in the registry is a red
build. Least privilege per tool: each one declares the scope it needs, and the
scope check happens before the body runs rather than inside it.
"""

from __future__ import annotations

from typing import Any

from openstore.sidecar.admission.oauth import AgentToken
from openstore.sidecar.core.codes import TOOL_SCOPES, Protocol, ReasonCode, ToolName
from openstore.sidecar.protocols.core import refusal_envelope

#: Human-facing descriptions. The shapes come from the registry; these are the
#: only free text, and they say what the tool does for a Consumer rather than
#: what it does to the system.
_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.SEARCH: "Search this shop's catalogue.",
    ToolName.READ_ITEM: "Read one product group, its options and its availability.",
    ToolName.ADD_LINE: "Add one resolved variant to the basket.",
    ToolName.REMOVE_LINE: "Remove a line from the basket.",
    ToolName.SET_DESTINATION: "Set where the order is delivered.",
    ToolName.SET_CONTACT: "Set the email or phone the shop notifies.",
    ToolName.CHOOSE_FULFILLMENT: "Choose one of the shop's delivery options.",
    ToolName.APPLY_PUBLIC_CODE: "Apply an advertised discount code.",
    ToolName.START_CHECKOUT: "Price the basket and prepare it for approval.",
    ToolName.PLACE_ORDER: (
        "Hand the Consumer a link to approve the purchase. Returns an approve URL, "
        "never an order — the shop cannot place an order on the Consumer's behalf."
    ),
    ToolName.ORDER_STATUS: "Check the status of an order this agent placed.",
    ToolName.CANCEL_ORDER: "Cancel an order this agent placed, before any money moves.",
    ToolName.REQUEST_REFUND: "Ask the shop to refund an order this agent placed.",
}

_SCHEMAS: dict[ToolName, dict[str, Any]] = {
    ToolName.SEARCH: {"query": "string"},
    ToolName.READ_ITEM: {"group": "string"},
    ToolName.ADD_LINE: {"sku": "string", "qty": "integer", "parent": "string?"},
    ToolName.REMOVE_LINE: {"sku": "string"},
    ToolName.SET_DESTINATION: {"destination": "object"},
    ToolName.SET_CONTACT: {"contact": "object"},
    ToolName.CHOOSE_FULFILLMENT: {"id": "string"},
    ToolName.APPLY_PUBLIC_CODE: {"code": "string"},
    ToolName.START_CHECKOUT: {},
    ToolName.PLACE_ORDER: {},
    ToolName.ORDER_STATUS: {"order_id": "string"},
    ToolName.CANCEL_ORDER: {"order_id": "string", "reason": "string?"},
    ToolName.REQUEST_REFUND: {"order_id": "string", "reason": "string?"},
}


def tools_list() -> list[dict[str, Any]]:
    """Every tool, with the scope it needs stated in the listing itself.

    An agent can see what a tool will cost it before calling one, which is the
    difference between least privilege and a surprise.
    """
    return [
        {
            "name": tool.value,
            "description": _DESCRIPTIONS[tool],
            "scope": TOOL_SCOPES[tool].value,
            "input_schema": _SCHEMAS[tool],
        }
        for tool in ToolName
    ]


def check_scope(token: AgentToken, tool: ToolName) -> dict[str, Any] | None:
    """Refuse before the body runs.

    Holding `confirm` still buys nothing: `place-order` returns an approve URL
    and the Gate refuses a spend without a fresh Authority regardless.
    """
    required = TOOL_SCOPES[tool]
    if not token.allows(required):
        return refusal_envelope(
            Protocol.MCP,
            ReasonCode.AUTHORITY_MISSING,
            f"{tool.value} needs the {required.value} scope",
        )
    return None


def envelope(result: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": Protocol.MCP.value, "result": result}
