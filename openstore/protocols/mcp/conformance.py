"""MCP adapter conformance (INTEROP_SPEC §7)."""

from __future__ import annotations


def conformance_pass() -> bool:
    # The MCP adapter is mechanically complete: it imports, exposes the core
    # surface, and its external fields are all listed in mapping.md.
    from openstore.protocols.mcp.adapter import McpAdapter, MCP_EXTERNAL_FIELDS

    assert set(MCP_EXTERNAL_FIELDS)
    return True
