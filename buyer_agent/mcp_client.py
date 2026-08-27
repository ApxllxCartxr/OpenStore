import json
import uuid

import httpx


def call_mcp_tool(
    mcp_endpoint: str,
    tool_name: str,
    arguments: dict,
    token: str,
) -> dict:
    """Call an MCP tool via JSON-RPC over HTTP (Streamable HTTP transport).
    Returns the parsed result content."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = httpx.post(
        mcp_endpoint,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return _parse_response(resp)


def initialize_mcp_session(mcp_endpoint: str, token: str) -> dict:
    """Send the MCP initialize handshake."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "openstore-buyer-agent", "version": "0.1.0"},
        },
    }
    resp = httpx.post(
        mcp_endpoint,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return _parse_response(resp)


def _parse_response(resp: httpx.Response) -> dict:
    """Parse MCP response, handling both JSON and SSE stream formats."""
    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        result = {}
        for line in resp.text.split("\n"):
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                # Per Streamable HTTP spec, only the final event carries the result.
                # Intermediate events (e.g. progress) are ignored.
                if "result" in data or "error" in data:
                    result = data
        return result

    return resp.json()
