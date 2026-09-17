# Plan — Demo Merchant Site (`demo/merchant-site/`)

Surface 2. Proves a Merchant can operate the sidecar. Next.js + Postgres in its own container and compose service — deliberately a different stack from the sidecar to prove HTTP-only integration. Own DB is the Merchant truth the sidecar mirrors. Static final prices only: no offers/discounts engine (cut per grill Q44).

## M1 — Store scaffold + schema + seed

- Next.js storefront (browse/search/item with accessories, sizes/options) + Postgres with migrations.
- Tables: `catalogue_items` (sku unique, name, price_minor int ≥ 0, options JSON, tags), `stock` (per-SKU int ≥ 0, no nulls — row missing or negative fails loud), `orders` (the 8 canonical statuses: `pending / confirmed / paid / cancelled / expired / failed / refunded / completed`, line items `{sku, qty, price}`, totals, address + options inside the row, `agent_id`, `consumer_ref`, `expires_at`, `tap_proof`, `receipt_id`).
- Seed: coffee/food catalogue with accessories and size variants, counts on every SKU, one 24h-pending example order.
- Compose service + health endpoint. No imports from sidecar or buyer-chat — not even types.
- DONE WHEN: store boots alone, seed loads with zero null-stock rows, catalogue validation rejects missing/negative/float stock with a named error.

## M2 — Shop-ops admin (demo-admin)

- Catalog CRUD (add/delete/edit items + options), stock adjust with reason + actor recorded, order list with status transitions (confirm/ship → `completed`, cancel with reason), read-only exposure per Merchant.
- Validation: price/stock integers only, negative refused, unknown SKU refused, all failures named. Every stock move logged (who, when, delta, reason).
- Explicitly not here: keys, provider secrets, policy signing, exposure policy, receipts — those are the sidecar `/agentic` console. Demo-admin is shop ops only so the demo stays replaceable.
- DONE WHEN: full ops loop passes in tests — create item → set stock → place test order → adjust → cancel/refund — with the audit showing every move.

## M3 — Trait implementation (the 8 doors)

- Implement the sidecar's trait natively over HTTP on the private network: `catalog.read / stock.read / reserve / commit / release / restock / orders.create+read / orders.set-status`, HMAC auth + idempotency keys, closed reason codes.
- Semantics: `reserve` marks committed-not-sold (pending cart, 24h); `commit` decrements `available` on paid; `release` returns on cancel/expire/fail; `restock` returns on refund. External (walk-in/site) sales decrement `available` directly — the sidecar sees them on its next fresh read.
- Pass the sidecar's trait conformance suite (from Sidecar S2) unmodified: retries collapse, double-commit impossible, release-without-reserve refused.
- DONE WHEN: conformance green against this real implementation, plus a simulated walk-in sale visibly lowering the next agent gate's `available`.

## M4 — Change notifications + health

- Emit signed change events the sidecar can subscribe to for refresh (`stock.changed`, `order.changed`): HMAC-signed, deduped by event id, replay-safe. The gate still re-reads fresh — events are a speedup, never the source of truth.
- Liveness/readiness endpoints for compose + proxy.
- DONE WHEN: event replay/duplication tests pass; killing events degrades to slower reads with zero wrong sales.

## M5 — Compose wiring + static pricing discipline

- Compose service with its Postgres, env-driven secrets (local `.env` only), no shared volumes/DB with sidecar or chat.
- Pricing discipline enforced in review: final numbers only, manual edits, no rule/bot/draft code anywhere in this repo path. Any engine-shaped code fails review by rule, not by test.
- DONE WHEN: `store + sidecar` compose boots via the path split, seeded latte orderable through the sidecar gate against this store's live stock.
