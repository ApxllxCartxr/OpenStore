# MCP — SPEC EXCERPT

Source: https://modelcontextprotocol.io/specification/2025-06-18
Fetched: 2026-08-28 (UTC)

MCP is an open protocol enabling LLM applications to connect to external tools
and data sources. It uses JSON-RPC 2.0 messages and supports two transports:
stdio and Streamable HTTP. For HTTP-based transports, implementations SHOULD
conform to the OAuth 2.1 authorization framework.

## Tool model
- Servers expose **tools** discoverable via `tools/list` and invocable via
  `tools/call`. Tools are model-controlled: the LLM discovers and invokes them.
- Each tool is identified by a `name` and described by an input schema.
- Tool results carry structured content; execution errors are reported with
  `isError: true`.

## Lifecycle
- JSON-RPC `initialize` negotiates capabilities and protocol version.
- Requests carry a string/integer `id` (never `null`); responses echo it.

## Authorization (HTTP transport)
- OAuth 2.1: the client presents a bearer access token; the server authenticates
  it per RFC 6750. This is the `oauth2_bearer` auth method our Actor uses.

## Fields we map (see mapping.md)
- Tool input arguments (`sku`, `qty`, `query`, `limit`, `delivery`, `jws`,
  `policy`, `idempotency_key`) are the only caller-supplied fields. Prices are
  NEVER accepted from the caller; they are re-derived from the merchant catalog.
