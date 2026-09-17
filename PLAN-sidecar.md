# Plan — Sidecar (`src/openstore/sidecar/`)

The product. Middleware only: no product info of its own, no shopping UI. Reads Merchant truth fresh at every gate, mirrors moves back, signs with Merchant-held keys automatically. Serves `/.well-known/*`, `/agent/*`, `/agentic/*` on the Merchant domain via reverse-proxy split.

## S1 — Skeleton + guardrail harness

- Layout `src/openstore/sidecar/` (core, protocols, consoles, verify) with zero imports from `demo/*`.
- Closed identifier sets enforced both directions by a build-breaking check (codes, scopes, statuses, routes, tool names).
- Fail-loud error shape: structured denials carrying reason codes; no silent excepts, coercions, or defaults anywhere in v1 paths.
- Money lint (integers/paise only) + time lint (UTC) over money paths.
- Import-firewall test covering runtime + types + tests, both directions.
- Fresh `.env.example` template: provider keys/secrets per Merchant, webhook secrets, passkey RP ID/origin, sidecar signing keys, OAuth credentials. Local `.env` never committed.
- DONE WHEN: empty app boots behind the proxy split; harness tests red on planted violations (unregistered code, float money, cross-root import) and green otherwise.

## S2 — Merchant-truth trait contract

- Define the 8-door HTTP trait the sidecar calls and demo/real stores implement: `catalog.read / stock.read / reserve / commit / release / restock / orders.create+read / orders.set-status`. HMAC auth + idempotency keys on every mutating door, closed reason codes on every refusal.
- Conformance fake: in-memory Merchant implementing the trait for sidecar-side tests (stock goes negative-proof, external-sale simulation to prove re-read wins).
- Seed-list file format (`lists/seed.json`: name/category/city/sidecar URL/public key/protocols) defined + validated, ships with one example. Reader supports direct-add URL + custom list URL.
- DONE WHEN: trait conformance suite passes against the fake, including retry-safety (same key, same result), external-sale visibility (gate sees lowered `available`), and malformed-list rejection.

## S3 — Gate + notebook + provider plug

- Deterministic compiler: fixed check order (human-authority-present + currency/Merchant/window/count/qty/blocked/tags/caps), stop-at-first-fail, closed reason codes, byte-stable transcript persisted with each decision. Dry-run mode with zero side effects.
- Append-only ledger `RESERVE → CAPTURE / RELEASE / REFUND` with escrow-zero invariant; paise integers; concurrent-gate safety (read-then-act races closed by design).
- Provider trait (`make-link / check-status / cancel / refund`) with fake drawer + Razorpay; Justpay/Airpay later in the same shape. UPI-only. Webhooks: HMAC on raw bytes, `event_id` dedupe, amount/currency reconcile vs order, provider-is-truth on paid-or-not.
- DONE WHEN: golden decision vectors pass byte-identical; ledger invariants hold under replay + concurrency tests; webhook forgery/replay/reorder tests pass; crash-mid-link adopts instead of double-creating.

## S4 — Authority + orders

- Passkeys: enrollment + per-spend tap bound to exact `cart_hash` + amount + expiry (UV on, challenge binding, sign-count tracking). Virtual tap in tests runs identical checks with test keys.
- OAuth client-credentials for agents (four scopes: `search / build-basket / start-checkout / confirm`), pinned ES256, short expiry. Dual identity on every order: `consumer_id` + `agent_id`, independently revocable; gate needs both.
- Permissions: standing approval covers reads/drafts only; any spend needs a fresh Allow-once tap. Tap delivery at `/agentic/approve?t=<one-time-token>` with resume URL restoring exact state.
- Orders in the Merchant book via the trait: `pending` (24h, ₹0 moved) → `confirmed` → `paid` / `cancelled` / `expired` (sweeper, untouched stock) / `failed` / `refunded` / `completed` (observed). Address + options ride inside the order row.
- DONE WHEN: tap-bound happy path passes; tampered/replayed/stale assertions rejected with codes; expiry sweeper marks `expired` with nothing moved; revoked-credential orders refuse.

## S5 — Tight receipt + checker + manifests

- 5-section bundle (bought / tapped / decided / told / moved): hash-chained, Merchant-ES256-signed, Merkle-stamped. Each order pins the attestation it used so later edits can't rewrite history.
- Offline CLI verifier (exit-coded), gated browser viewer, public JWKS, discovery manifests (agent-commerce + UCP), per-item signed attestations over `{sku, price, tags, digest}`.
- DONE WHEN: clean bundle verifies; 1-digit-tampered bundle fails naming the exact link; fixtures pinned; viewer checks against the same vectors.

## S6 — Protocol plugs + proof

- Thin translators MCP (live) + UCP/ACP/AP2 over the same core: validate foreign envelope → core calls → translate back. No second checkout, ledger, or receipt anywhere.
- One golden end-to-end replay per protocol (search → allow → cart → tap → fake-UPI → receipt → verify), pinned byte-identical in CI. Demo toggle replays the same flow through the selected envelope with conformance badges.
- DONE WHEN: identical transcript verifies through all four envelopes; unknown/legacy shapes rejected with codes, never executed.

## S7 — Mount + Merchant console

- Same-domain split via reverse proxy: `/ → store`, `/.well-known + /agent + /agentic → sidecar`. One origin for passkeys, separate containers/deploys.
- Minimal `/agentic` console: key enrollment/rotation, provider secrets, policy view, per-policy exposure, order lookup, receipt access. Shop ops (catalog/stock/fulfilment) never live here — that's demo-admin.
- DONE WHEN: proxy-split compose boots; console completes key enroll → policy view → exposure check → receipt open with zero shop-ops controls present.
