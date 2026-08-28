# OpenStore

An agent-commerce stack: a merchant server exposes a catalog and checkout over MCP, a Discord-based buyer agent shops on a human's behalf, and every checkout is gated by a policy a human signed once with WebAuthn — not by a human approving each transaction.

## The problem this solves

Agentic checkout usually reduces to one of two bad options: let the agent spend unsupervised, or put a human in the loop on every transaction (an OTP, a Slack approval) which doesn't scale and trains people to click "approve" without reading. OpenStore's answer is the **Intent Compiler**: a human signs a spending policy once — max amount, allowed product tags, blocked SKUs, an expiry, a merchant lock — with a WebAuthn credential (passkey, YubiKey, Touch ID). The agent then carries that signed policy through every checkout it runs, and the merchant server verifies the cart against it mathematically before ever contacting a payment provider. If the cart's total, tags, or SKUs fall outside what was signed, checkout is rejected server-side — no LLM in that decision path, and no per-transaction human step.

## Architecture

```
Discord ──▶ buyer_agent (LangGraph) ──MCP/OAuth2.1──▶ merchant (FastAPI)
                                                          │
                                          intent_compiler.py verifies
                                          cart vs. WebAuthn-signed policy
                                                          │
                                                     Razorpay (payment link)
```

**Merchant server** (`merchant/`) — FastAPI on port 8000
- Storefront at `/` (Gelateria Roma, gelato catalog from YAML in `config/`)
- MCP endpoint at `/agent/mcp`: `search_products`, `get_product`, `create_cart`, `update_cart`, `checkout_initiate`, `checkout_confirm`
- Full OAuth 2.1 authorization server (dynamic client registration, PKCE, JWKS) so any agent, not just the bundled one, can authenticate
- Agent Commerce discovery manifest at `/.well-known/agent-commerce.json`
- SQLite (WAL) for carts, checkouts, WebAuthn credentials/assertions, orders, audit log
- Razorpay for payment links (INR, minor units)

**Buyer agent** (`buyer_agent/`) — Discord bot on LangGraph
- Discovers the merchant and its auth server via well-known endpoints (no hardcoded merchant config)
- Registers as an OAuth client dynamically, runs PKCE over a loopback redirect
- Fetches the latest signed policy token from the merchant before confirming a checkout
- One conversation graph per Discord channel/user

**Intent Compiler** (`merchant/intent_compiler.py`, `merchant/webauthn.py`, `merchant/intent_routes.py`)
- A human visits `/intent/register`, defines a policy (spend cap, allowed tags, blocked SKUs, expiry, merchant lock), and registers a WebAuthn credential against it
- To authorize spending, the human visits `/intent/sign`: the server issues a random nonce bound to `SHA-256(canonical policy JSON)`, the authenticator signs it, and the assertion + policy are stored
- The agent fetches this assertion via `GET /internal/webauthn/latest-assertion` and passes both the assertion and policy JSON to `checkout_confirm`
- The server re-derives the policy hash, verifies the WebAuthn signature (`fido2`) matches the credential on file, confirms the signed nonce maps to that exact policy hash (so an agent can't reuse a signature for a different policy), then runs the cart through `verify_cart_against_policy`: merchant lock → expiry → blocked SKUs → tag allowlist → spend cap
- Any violation rejects the checkout before Razorpay is touched; a pass moves the checkout straight to `POLICY_VERIFIED` — no OTP round-trip
- The older OTP/mandate path (`merchant/mandate.py`, Discord modal approval in `merchant/notifier.py`) still exists as a fallback, but the Intent Compiler is the primary flow — it replaces a human decision-per-transaction with a human decision-per-policy

## Other design choices worth noting

- **Never trust agent-supplied prices** — `create_cart` / `update_cart` re-derive every unit price from the live catalog on the server, not from what the agent claims
- **Cart versioning** — every mutation bumps `Cart.version`; a policy or mandate referencing a stale version is rejected
- **Idempotency keys** — required on `checkout_confirm`; the server persists and replays the original response rather than double-processing
- **Two independent spend caps** — a per-transaction cap (₹500 default, overridable by policy) and a rolling 24h cap (₹2,000) tracked via `SpendLedgerEntry`, enforced with a token-bucket rate limiter per `(client_id, tool)` in `merchant/policy.py`
- **Full audit trail** — every MCP tool call writes an `AuditLogEntry` (args, result, latency, success) via the `@audited_tool` decorator, viewable at `/admin/audit`

## Running it

```bash
uv sync

cp .env.example .env
# Fill in DISCORD_BOT_TOKEN, DISCORD_BUYER_CHANNEL_ID, DISCORD_WEBHOOK_*,
# RAZORPAY_KEY_ID/SECRET, APPROVER_DISCORD_USER_ID

# Terminal 1: merchant server
uv run uvicorn merchant.app:app --reload --port 8000

# Terminal 2: buyer agent (Discord bot)
uv run python -m buyer_agent.bot
```

Visit `http://localhost:8000` for the storefront, `http://localhost:8000/intent/register` to set up a policy, and `/intent/sign` to authorize a checkout. Message the bot in the configured Discord channel to shop.

## Project layout

```
merchant/
  app.py               # FastAPI app factory, lifespan, routers
  mcp_server.py        # MCP tool definitions + checkout_confirm orchestration
  models.py            # SQLModel tables + Pydantic schemas (incl. IntentPolicy)
  intent_compiler.py   # verify_cart_against_policy — the compiler itself
  webauthn.py           # fido2 assertion/attestation verification
  intent_routes.py     # /intent/* pages + WebAuthn challenge/register/sign endpoints
  policy.py            # token-bucket rate limiting, rolling spend queries
  checkout.py          # checkout_initiate: cap checks, immutable cart snapshot
  mandate.py            # Ed25519 JWS mandate signing (fallback path)
  notifier.py           # Discord OTP-approval modal (fallback path)
  oauth/routes.py       # OAuth 2.1 server: client reg, auth code, token, JWKS
  mcp_auth.py           # bearer-token verification for MCP tool calls
  webhooks.py           # Razorpay webhook receiver
  catalog/yaml_adapter.py
  config.py             # pydantic-settings, loads .env
  db.py                 # SQLite engine + init_db()
  audit.py              # @audited_tool decorator
  trace.py              # Discord webhook tracing
  storefront/           # Jinja2 templates: index, audit, intent_sign, intent_register

buyer_agent/
  bot.py                # Discord client + message handler
  graph.py              # LangGraph state machine
  discover.py           # well-known endpoint discovery
  oauth_client.py       # dynamic client registration + PKCE
  mcp_client.py         # MCP tool-call wrapper
  intent.py             # fetches the signed policy token before checkout
  llm.py                # LLM calls (Gemini)

tests/
  test_mcp.py           # MCP tool integration tests
  test_isolation.py     # cart version isolation
  test_llm.py           # LLM-related tests (stubbed)
```

## Testing

```bash
uv run pytest
uv run pytest tests/test_mcp.py
uv run pytest tests/test_mcp.py::test_search_products_returns_results
```

Each test spins up a fresh `TestClient` against an in-memory SQLite DB. `tests/test_mcp.py` exposes `_get_token()`, `_mcp_call()`, `_parse_mcp_result()` as MCP test helpers.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `DISCORD_BOT_TOKEN` | Buyer agent Discord bot token |
| `DISCORD_BUYER_CHANNEL_ID` | Channel where the bot listens |
| `DISCORD_WEBHOOK_BUYER_AGENT` / `_MERCHANT_AGENT` / `_MERCHANT_SERVER` / `_AUDIT_TRAIL` | Tracing webhooks per component |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | Payment link creation + webhook verification |
| `APPROVER_DISCORD_USER_ID` | Human approver for the OTP/mandate fallback path |
| `INTENT_SIGNING_USER_ID` | User ID the Intent Compiler treats as the policy signer (default `default-user`) |
| `DATABASE_URL` | SQLite connection string |
| `MERCHANT_CONFIG_PATH` | Path to the catalog YAML (default `config/gelateria.yaml`) |
| `GEMINI_API_KEY` | LLM key for the buyer agent's conversation graph |

## Where this goes next

`docs/PROOF_CARRYING_COMMERCE.md` — a brutal audit of this project against the agentic-commerce specs
that already exist (AP2, ACP, Visa TAP, W3C SPC), and the layer none of them define: portable,
offline-verifiable evidence for adjudicating an agent transaction after it is disputed.

`docs/DELEGATION_AND_ORCHESTRATION.md` — verifiable sub-budget delegation across agents, and multi-merchant
sourcing with saga compensation. The unsolved part of every current mandate spec.

`docs/GROWTH_AGENTS.md` — the merchant-side growth stack for when the customer is a machine: a synthetic
buyer swarm, blocked-cart recovery, headroom upsell, and an agent-experience optimization loop.

`docs/INTEROP_SPEC.md` — protocol-free commerce core plus adapters (MCP, ACP, AP2, A2A), and the
maximum evidence tier each protocol's authorization actually supports.

`docs/AGENT_LAYER.md` — the eval harness, adversarial red-team campaign, and agent-reasoning capture
that the deterministic core makes possible.

`docs/IMPLEMENTATION_SPEC.md` — the normative build contract for that layer: exact canonicalisation,
the closed reason-code and predicate sets, route-by-route request/response shapes, the pinned compiler
digest, and a test manifest. Written so an implementer never has to guess.

`docs/PRODUCTION_READINESS.md` is a self-review of the money path: six defects found by red-teaming
this repo's own `checkout_confirm` (with exploits and fixes), plus the payments-engineering work a real
deployment needs — idempotency semantics, the dual-write problem, a double-entry spend ledger, webhook
ordering, reconciliation, key management, and how the Intent Compiler maps onto RBI's e-mandate model.

## What's not done

- No linting/typecheck configured (ruff/mypy caches gitignored, no config committed)
- Razorpay webhook handler logs incoming events but doesn't yet transition `Order.status` to `PAID`
- WebAuthn ceremonies target `localhost` as the relying party — not yet parameterized for a real deployment domain
