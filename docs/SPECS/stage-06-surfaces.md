# Stage 06 — Agent surfaces: MCP tools, well-knowns, catalog + campaign feed

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md`.
- `OPENSTORE_PRD_v3.md` §3.8 (catalog + attestation), §3.9 (manifest schema), §3.10
  (OAuth closed sets), Part 6 (14 tools, route list), INV-9, INV-12.
- `REGISTRY.json` — do not edit. Tool names, routes, and scopes come from it alone.
- Stages 2–5 artifacts: compiler, ledger, hold/cancel, Razorpay driver.

## SCOPE (closed)
- `src/openstore/surfaces/mcp_server.py`, `wellknown.py`, `catalog.py`,
  `storefront.py`, `templates/storefront.html`
- `src/openstore/core/api.py` (Commerce Core API — single entry for adapters)
- OAuth server module (asymmetric tokens, INV-9): `src/openstore/core/oauth.py`
- `tests/stage06/**` (incl. a route-table snapshot sentinel)
- `OPEN_QUESTIONS.md`

## BUILD

### S6.1 Commerce Core API — `core/api.py`
Single deterministic entry for all adapters: catalog read, cart create/update (frozen
snapshot at `checkout_initiate`, INV-1), checkout initiate/confirm, order read, audit
read. All money math server-side (R0.8). Every call attributed with `client_id` +
`trace_id` (INV-12); PII redacted by the allowlist (delivery address and full WebAuthn
assertion excluded from logs/traces).

### S6.2 OAuth 2.1 + PKCE — `core/oauth.py`
Asymmetric ES256 tokens with `kid` header (INV-9); `GET /oauth/jwks.json` serves the
public JWK; algorithm allowlist rejects `none` and HS256. Scopes are the closed set
`catalog:read`, `cart:write`, `checkout:initiate`, `checkout:confirm` — any other scope →
hard error (R0.3). Token claims per §3.10.

### S6.3 MCP server — 14 tools, closed set
`search_products`, `get_product`, `create_cart`, `update_cart`, `checkout_initiate`,
`checkout_confirm`, `get_order`, `get_audit_log`, `webauthn_register_begin`,
`webauthn_register_complete`, `webauthn_begin_assertion`, `webauthn_complete_assertion`,
`list_campaigns`, `get_campaign`. Each tool is a thin adapter over `core/api.py` +
the WebAuthn RP (Stage 3); tool scopes enforced per REGISTRY.json mapping
(catalog tools → `catalog:read`; cart tools → `cart:write`; etc.). `list_campaigns` /
`get_campaign` read the campaign store and return only `ACTIVE` campaigns (they read
whatever Stage 8 will write; if none exist, return empty lists — do NOT build orchestrator
logic here).

### S6.4 Well-knowns — 6 manifests
Serve per §3.9 and Part 6: `/.well-known/agent-commerce.json` (version "0.2", schema
verbatim incl. the `campaigns` block), `/.well-known/agent-policy.json` (MUST publish the
HOLD_SECONDS table and `evidence_retention_days` — advertised retention equals the
configured value, DECISIONS §11.1.9), `/.well-known/agent-card.json`,
`/.well-known/oauth-authorization-server`, `/.well-known/poai-jwks.json` (this merchant's
keys only, namespaced `kid`), `/.well-known/agent-campaigns.json` (signed campaign feed —
empty signed feed until Stage 8). The `protocols[]` array is generated from the adapter
registry at startup, never hand-written; ACP version string is `[verify-at-build]`.

### S6.5 Catalog feed + attestation
`/agent/catalog` serves the merchant's catalog items per §3.8 (tags sorted, `unit_minor`
integers). Each item carries an ES256 `catalog_attestation` JWS per the §3.8 payload with
`iat` Unix seconds and `catalog_digest` over the normalized catalog. Storefront
(`storefront.html`) renders catalog + cart read-only from the same server-side data —
lean single-file HTML per Part 4.

## MUST NOT
- No checkout money movement beyond what Stage 5 wired; surfaces only call core.
- No agent/LLM code (Stage 7), no campaign drafting (Stage 8), no ACP/AP2 protocol
  adapters (Stage 9 handles the demo; adapters are scoped to the manifest only here).
- No new routes or tools beyond Part 6 / REGISTRY.json.
- `list_campaigns`/`get_campaign` MUST NOT fabricate campaigns when the store is empty.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage06/ -q` → 0 failures, incl. scope-enforcement negatives (wrong scope →
  closed-set error), alg-confusion rejection, attestation freshness rule
  (`iat <= cart_created_at`)
- Route-table snapshot sentinel passes (no route added/removed vs REGISTRY.json)
- Manifest conformance: a test fetches `/.well-known/agent-commerce.json` and validates it
  against the §3.9 schema field-by-field
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(06): surfaces`
