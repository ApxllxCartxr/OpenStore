# OpenStore Rebuild Spec (v1)

Sidecar-only rebuild. Same premise as before, solid requirements, guardrails on from commit one. Glossary in `CONTEXT.md` is law; decisions in `docs/adr/` are law.

## 1. Surfaces (3 roots)

- `src/openstore/sidecar/` — the product. Makes a Merchant site transactable by any Buyer Agent for Consumers. Middleware only: no product info of its own, no shopping UI. Layout: `src/openstore/sidecar/`.
- `demo/merchant-site/` — demo Merchant store (Surface 2). Next.js + Postgres, own container, own DB. Comprehensive catalogue (accessories, sizes/options), stock, order management, lean CRUD. Static final prices only — no offers/discounts engine (cut per Q44). Proves a Merchant can operate it.
- `demo/buyer-chat/` — demo Buyer Agent (Surface 3). Claude-like chat with tool calls over Ollama (swappable), air-gapped stranger: own folder/process/deps/DB, zero sidecar imports, HTTP-only with its own OAuth client. Proves the Consumer flow end to end.
- Build-breaking import firewall both directions (runtime + types + tests). CI red on any crossing.

## 2. Actors (CONTEXT.md)

Merchant (human, owns store/keys/catalog/policies) / Consumer (human, buys, pays manually every time) / Buyer Agent (any external agent, keyless, proposal-only) / Payment Provider (per-Merchant: Justpay, Airpay, Razorpay; untrusted, never decides authority).

## 3. Discovery (ADR-0005)

V1 = direct-add: Consumer pastes Merchant URL, agent fetches the sidecar card (`/.well-known/agent-commerce.json` + UCP manifest + JWKS) directly. List-file format (`lists/seed.json`: name/category/city/sidecar URL/public key/protocols) ships scaffolded (empty + example) for later; no crawler, ranking, or hosted search in v1. A mall later reads the same format with zero money-core changes.

## 4. Money core (ADR-0002)

One pipeline, four stages, every protocol hits it:
1. **Cart** — basket slip `{sku, qty, price}`. Dry-run compile available with zero side effects.
2. **Compiler** — deterministic checklist only: human-authority-present + currency/Merchant/window/count/qty/blocked/tags/caps in fixed order, stop-at-first-fail, closed reason codes, byte-stable transcript stored with the decision. Agent output never executes; this re-validates server-side every time.
3. **Ledger** — sidecar-owned append-only notebook `RESERVE → CAPTURE / RELEASE / REFUND`, escrow-zero invariant, paise integers only, UTC. Pending holds no money; tap converts, never auto-converts.
4. **Evidence** — tight 5-section sealed receipt (bought / tapped / decided / told / moved): hash-chained, Merchant-ES256-signed, Merkle-stamped, offline-verifiable via public JWKS + CLI. Gated viewer only.

## 5. Merchant truth + stock trait (ADR-0001, Q40)

Merchant system owns catalogue items, options, stock counts, and order rows. Sidecar reads fresh at every gate and mirrors back idempotently. Narrow HTTP trait only — `catalog.read / stock.read / reserve / commit / release / restock / orders.create+read / orders.set-status`, HMAC + idempotency keys, no shared DB ever (demo included). `.yaml` counts exist only where the basic site *is* the Merchant system.
- Stock: always `int ≥ 0`, never null. Missing/negative/float fails catalog load loud. `0` = refuse with `sold-out`. Services sell countable slots. Restock = Merchant edit. Refund restores exactly.
- External (non-agent) sales win automatically: the next gate re-reads the lowered `available` (Shopify `available`/`committed`/`reserved` + webhooks; Woo `stock_quantity` + hold-stock).

## 6. Orders — 8 canonical statuses (Q41)

`pending` (agent cart, 24h, ₹0 moved) / `confirmed` (tapped, UPI link out) / `paid` / `cancelled` / `expired` (24h sweep, untouched stock, re-add to retry) / `failed` / `refunded` / `completed` (Merchant-fulfilled, observed). Adapters map 1:1 to Shopify (`pending/authorized/paid/voided/expired/refunded`, `unfulfilled/fulfilled`) and Woo (`pending/on-hold/processing/completed/cancelled/failed/refunded` + hold-stock timeout). Address + options ride inside the order row in v1. Core stays platform-blind.

## 7. Authority: passkeys + scoped agent keys (ADR-0004, Q28/Q53)

- Consumer: fresh passkey tap per spend, bound to exact `cart_hash` + amount + expiry (UV on). Platform keys on laptop/mobile, QR cross-device. Enrollment = login; no passwords/OTPs/magic links to money. Virtual tap in demo runs identical checks with test keys.
- Buyer Agent: OAuth client-credentials only (`search / build-basket / start-checkout / confirm`), pinned ES256, short expiry. Name badge only — never buys.
- Permissions: `Always allow` covers reads/drafts only; any cart-with-spend, checkout, or place-order needs a fresh Allow-once tap. Gate requires both identities valid; either revokes independently.
- Tap delivery: agent shows `Tap to approve ₹X` → same-domain `/agentic/approve?t=<one-time-token>` → biometric → auto-return via resume URL with cart + chat state intact.

## 8. Who signs what

Merchant (via sidecar-held keys): per-item attestation, policy, checkout total + ETA + `cart_hash`, evidence root — auto-stamped, no human per sale. Consumer: enrollment + one tap per bag. Agent: nothing authoritative. Provider: HMAC webhooks + dashboard truth (verified, deduped, reconciled). List publisher: list file only. Demo keys are test-marked; demo refuses live keys at boot.

## 9. Protocols + proof (Q47/Q52)

MCP live in demo chat. UCP/ACP/AP2 are thin translators over the same core with one golden end-to-end replay each (search → allow → cart → tap → fake-UPI → receipt → verify), pinned byte-identical in CI. Demo header toggle `[MCP|UCP|ACP|AP2]` replays the same flow through the selected envelope plus conformance badge. No second money path anywhere.

## 10. Mounting + consoles (Q43/Q46/Q51)

Compose: `store:3000` + `sidecar:8000` + reverse proxy. `/ → store`, `/.well-known + /agent + /agentic → sidecar`. One origin (passkeys happy), separate containers/deploys. Demo-admin = shop ops only (catalog/stock/orders). Sidecar console at `/agentic` = keys/policy/provider/exposure/receipts.

## 11. Smoothness law (Q53)

Convenience is sidecar-engineered, never agent-hoped: verbatim signed totals, ordered results, one `approve_url`, expiry countdown, resume links, errors naming the exact fix. Golden replays assert one-tap completion with zero re-search/re-login and exact resumed state. Inconvenient = red build.

## 12. Guardrails (law from commit one)

Closed sets enforced both directions by build break. Fail loud, no coercion/defaults/silent fallbacks; every denial carries a closed reason code into signed evidence. No LLM output in the money path; agents keyless + proposal-only. Money integers (paise), time UTC. Server recomputes totals, hashes, discounts, and caps — never trusts agent supply. Least-privilege per tool/scope, signed + pinned tool/manifest definitions, idempotency on every mutation, replay-safe webhooks, per-Merchant scoping on every query.

## 13. Explicitly out (phase two, same core untouched)

Campaigns/rules/bots/stalled-loops, COD, hosted mall/search/ranking, multi-location, partial refunds, auto-fulfilment, OMS write paths beyond the 8-door trait. Static sale prices only in v1.
