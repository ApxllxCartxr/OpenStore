"""JSON-RPC 2.0 request/response shape for `/agent/mcp` (ADR-0026).

One place for the envelope so test files assert on tool behaviour, not on
JSON-RPC plumbing repeated per call site.
"""

from __future__ import annotations

from typing import Any


def mcp_request(name: str, *, id_: int = 1, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def mcp_result(body: dict[str, Any]) -> dict[str, Any]:
    """The tool's structured result. Asserts the call did not refuse — use
    `mcp_error` for the cases that expect one."""
    result = body["result"]
    assert not result.get("isError"), result
    return dict(result["structuredContent"])


def mcp_error(body: dict[str, Any]) -> dict[str, Any]:
    """The refusal envelope (`{"protocol", "error": {"code", "detail"}}`) from
    a `tools/call` that refused. Asserts the call did refuse."""
    result = body["result"]
    assert result.get("isError"), result
    return dict(result["structuredContent"])
