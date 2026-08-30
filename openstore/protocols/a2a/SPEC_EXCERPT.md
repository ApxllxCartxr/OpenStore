# A2A — SPEC EXCERPT

Source: https://a2a-protocol.org/latest/specification
Fetched: 2026-08-28 (UTC)

A2A (Agent2Agent Protocol, Google) enables communication and interoperability
between independent, potentially opaque AI agent systems. An agent publishes an
**AgentCard** — a JSON manifest, typically at `/.well-known/agent.json` — that
describes its name, description, version, capabilities, authentication
requirements, default I/O modes, and a list of `skills`.

## AgentCard structure
- `name`, `description`, `version`, `url`
- `capabilities`: streaming, pushNotifications, stateTransitionHistory
- `authentication`: schemes (e.g. Bearer) + credentials
- `defaultInputModes` / `defaultOutputModes`: MIME types
- `skills[]`: id, name, description, tags, examples, inputModes, outputModes

## Transport
A2A uses JSON-RPC 2.0 over HTTP (SSE for streaming). Discovering agents happens
via the AgentCard; task-based interaction (`message/send`, `tasks/get`) carries
the actual work.

## Relevance to OpenStore
The merchant reasoning agent keeps its A2A surface unchanged (INTEROP_SPEC §6.4).
It holds no signing key, no Razorpay write credential, and no core write access —
so it cannot spend. The interop contract's isolation test (R6.4a) verifies this.
