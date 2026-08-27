# AGENTS.md

## Quick commands

```bash
# Install / sync deps
uv sync

# Run merchant server (FastAPI, port 8000)
uv run uvicorn merchant.app:app --reload --port 8000

# Run buyer agent (Discord bot, separate process)
uv run python -m buyer_agent.bot

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_mcp.py

# Run a single test by name
uv run pytest tests/test_mcp.py::test_search_products_returns_results
```

No linting, formatting, or typecheck tooling is configured yet. The `.gitignore` references `.ruff_cache/` and `.mypy_cache/` but no ruff/mypy config exists in `pyproject.toml`.

## Architecture

Three processes (run independently):

| Process | Entry point | Framework | Port |
|---|---|---|---|
| Merchant execution server | `merchant/app.py` → `create_app()` | FastAPI + MCP | 8000 |
| Buyer agent | `buyer_agent/bot.py` | Discord.py + LangGraph | - |
| Merchant reasoning agent | `merchant_agent/` | Stubbed, not implemented | - |

- **MCP endpoint**: mounted at `/agent/mcp` via Streamable HTTP transport (`merchant/mcp_server.py`)
- **OAuth 2.1 server**: built into the merchant server (`merchant/oauth/routes.py`), well-known at `/.well-known/oauth-authorization-server`
- **Discovery**: `/.well-known/agent-commerce.json` describes the merchant, MCP endpoint, auth server, and policy

## Key packages under `merchant/`

- `models.py` — SQLModel tables + Pydantic schemas (Product, Cart, Checkout, Mandate, Order, etc.)
- `db.py` — SQLite WAL engine, `init_db()` must run at startup (happens in `app.py` lifespan)
- `config.py` — Pydantic Settings, reads `.env` at module load via `settings` singleton
- `intent_compiler.py` — policy verification (tags, SKU blocks, spend limits)
- `mandate.py` — Ed25519 JWS mandate signing/verification
- `mcp_server.py` — all MCP tool definitions
- `audit.py` — `@audited_tool` decorator for MCP tools

## Environment

Copy `.env.example` → `.env`. Required vars are loaded by `merchant.config.Settings` at import time.

Key vars: `DISCORD_WEBHOOK_*` (tracing), `DISCORD_BOT_TOKEN` + `DISCORD_BUYER_CHANNEL_ID` (buyer bot), `RAZORPAY_*` (payments), `APPROVER_DISCORD_USER_ID` (approval flow).

## Testing

- Uses `pytest` + `pytest-asyncio` (dev deps in `pyproject.toml`)
- `pythonpath = ["."]` in pytest config — tests import from project root directly
- Tests use FastAPI `TestClient` with an in-memory SQLite database (fresh `create_app()` per test)
- MCP test helpers: `_get_token()` creates JWT tokens, `_mcp_call()` sends JSON-RPC payloads, `_parse_mcp_result()` handles SSE responses
- Tests import `JWT_SECRET` from `merchant.oauth.routes` — this is a module-level constant, not from env

## Gotchas

- `Settings` is instantiated at module load (`merchant/config.py:21`) — importing `merchant.config` anywhere reads `.env` immediately. Tests depend on this.
- The merchant server serves both the API and an HTML storefront from `merchant/storefront/` templates.
- Currency is **INR in paise** (minor units). Prices like `25000` = ₹250.00.
- `merchant_agent/skills/` is empty — the merchant reasoning agent is not yet implemented.
- SQLite DB file is `openstore.db` at repo root (gitignored).
