"""MCP — real JSON-RPC 2.0 (ADR-0026), not a lookalike envelope.

`tools/list` and the tool schemas come from the closed action set in
`core/codes.py`, so a tool that exists here and not in the registry is a red
build. Least privilege per tool: each one declares the scope it needs, and the
scope check happens before the body runs rather than inside it.

Three methods, because this sidecar has never exposed anything a fourth would
serve: `initialize` (capability handshake), `tools/list` (JSON Schema +
annotations a generic client can act on without knowing any tool by name), and
`tools/call` (dispatches into the same core every other envelope shares). No
resources, no prompts, no sampling.
"""

from __future__ import annotations

import json
from typing import Any

from openstore.sidecar.admission.oauth import AgentToken
from openstore.sidecar.core.codes import TOOL_SCOPES, Protocol, ReasonCode, Scope, ToolName
from openstore.sidecar.core.correlation import request_id
from openstore.sidecar.protocols.core import refusal_envelope

PROTOCOL_VERSION = "2025-06-18"

#: Human-facing descriptions. The shapes come from the registry; these are the
#: only free text, and they say what the tool does for a Consumer rather than
#: what it does to the system.
_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.SEARCH: (
        "Search this shop's catalogue. An empty query lists everything, a page at a "
        "time: pass the `next_cursor` from a result back as `cursor` for the next page, "
        "and `total` says how many matched in all."
    ),
    ToolName.READ_ITEM: "Read one product group, its options and its availability.",
    ToolName.ADD_LINE: "Add one resolved variant to the basket.",
    ToolName.REMOVE_LINE: "Remove a line from the basket.",
    ToolName.CLEAR_BASKET: "Empty the basket back to a fresh state: lines, destination, contact, fulfillment and code all go.",
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

#: field -> loose type shorthand ("x?" is optional). Kept next to the
#: descriptions because both are hand-authored per tool; `_input_schema`
#: below is what actually leaves the process.
_FIELDS: dict[ToolName, dict[str, str]] = {
    ToolName.SEARCH: {"query": "string", "limit": "integer?", "cursor": "string?"},
    ToolName.READ_ITEM: {"group": "string"},
    ToolName.ADD_LINE: {"sku": "string", "qty": "integer", "parent": "string?"},
    ToolName.REMOVE_LINE: {"sku": "string"},
    ToolName.CLEAR_BASKET: {},
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

_JSON_TYPE = {"string": "string", "integer": "integer", "object": "object", "boolean": "boolean"}

#: The two scopes that move a basket toward spend. Everything else — reads and
#: basket-building — is safe for a client to offer standing "always allow" on;
#: these two never are, regardless of what a client's UI wants to do.
_MONEY_PATH_SCOPES = {Scope.START_CHECKOUT, Scope.CONFIRM}


def _input_schema(tool: ToolName) -> dict[str, Any]:
    fields = _FIELDS[tool]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, kind in fields.items():
        optional = kind.endswith("?")
        json_type = _JSON_TYPE[kind[:-1] if optional else kind]
        properties[name] = {"type": json_type}
        if not optional:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tools_list() -> list[dict[str, Any]]:
    """`tools/list`. Annotations carry the safety-relevant facts — read-only,
    destructive, money-path — as booleans a generic client can branch on
    without a single hardcoded tool name."""
    out = []
    for tool in ToolName:
        scope = TOOL_SCOPES[tool]
        out.append(
            {
                "name": tool.value,
                "description": _DESCRIPTIONS[tool],
                "inputSchema": _input_schema(tool),
                "annotations": {
                    "title": tool.value.replace("-", " ").capitalize(),
                    "readOnlyHint": scope is Scope.SEARCH,
                    "destructiveHint": tool
                    in (ToolName.CANCEL_ORDER, ToolName.REQUEST_REFUND, ToolName.CLEAR_BASKET),
                    "idempotentHint": scope is Scope.SEARCH,
                    "openWorldHint": False,
                    # Not a standard MCP annotation — this sidecar's own scope
                    # ladder, exposed so a client's permission UI can offer
                    # standing approval for everything except the two scopes
                    # that ever move toward a spend (ADR-0008, ADR-0017).
                    "moneyPathHint": scope in _MONEY_PATH_SCOPES,
                    "scope": scope.value,
                },
            }
        )
    return out


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


def call_result(result: dict[str, Any]) -> dict[str, Any]:
    """A successful `tools/call` result: text for a client that renders
    nothing tool-specific, `structuredContent` for one that does."""
    return {
        "content": [{"type": "text", "text": json.dumps(result)}],
        "structuredContent": result,
        "isError": False,
    }


def call_error(code: ReasonCode, detail: str, **fields: Any) -> dict[str, Any]:
    """A refused `tools/call`: still a successful JSON-RPC response (the
    *transport* worked), `isError: true` per the MCP spec, so a generic client
    doesn't need this repo's `ReasonCode` enum to know something went wrong —
    it only needs one it already does need it to show why."""
    error: dict[str, Any] = {"code": code.value, "detail": detail, **fields}
    # Same id the console row carries. An agent that reports a refusal to a
    # Merchant can now name the attempt instead of the minute it happened.
    current = request_id()
    if current:
        error["request_id"] = current
    return {
        "content": [{"type": "text", "text": f"{code.value}: {detail}"}],
        "structuredContent": {"protocol": Protocol.MCP.value, "error": error},
        "isError": True,
    }
