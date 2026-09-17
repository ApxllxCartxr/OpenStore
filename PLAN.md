# Plan Index — 3 surfaces, one build order

Full spec in `SPEC.md`, glossary in `CONTEXT.md`, decisions in `docs/adr/`. Each surface has its own plan file with phases, interfaces, and DONE WHEN gates. No phase starts until its dependencies below are green.

- [`PLAN-sidecar.md`](./PLAN-sidecar.md) — `src/openstore/sidecar/`: the product. Gate, Ledger, receipt, authority, protocols, `/agentic` console, install + operations.
- [`PLAN-merchant-site.md`](./PLAN-merchant-site.md) — `demo/merchant-site/`: SpoiledDuckie Next.js + Postgres demo accessory shop proving a Merchant can operate it.
- [`PLAN-buyer-chat.md`](./PLAN-buyer-chat.md) — `demo/buyer-chat/`: Ollama chat stranger proving the Consumer flow end to end.
- [`PLAN-distribution.md`](./PLAN-distribution.md) — phase two, not a surface: the permissionless doors that make the three above reachable. Starts only after step 7 below is green.

Expanding a plan? Read [`docs/EXPANSION.md`](./docs/EXPANSION.md) first — ladder, contracts table, firewall, and grill rules.

## Build order across surfaces

1. Sidecar S1 (skeleton + harness) + trait contract (S2-contract, 9 doors incl. `quote`). The Quote shape and the `cart_hash` preimage are settled here — they are the last thing that can change cheaply, and everything downstream pins their bytes.
2. Merchant site M1–M3 (store, admin, trait implementation) against the contract.
3. Sidecar S3–S5 (gate + notebook + provider, authority + orders, receipt + checker) verified against a fake Merchant, then the real demo store.
4. Buyer chat B1–B4 (scaffold, direct-add, MCP flow, tap + fake-UPI + receipt) against the sidecar.
5. Sidecar S6 + buyer chat B5 together (protocol translators + golden replays + toggle).
6. Mount + end-to-end (sidecar S7, store M5, chat smoothness gates): charm-to-receipt demo on fake money.
7. Sidecar S8 (install, keys, operations): a clean machine goes `openstore up` → verified receipt with no hand-edited files. This is the adoption gate — a correct sidecar nobody can install is not a product. It is also the entry condition for `PLAN-distribution.md`: reach earned before install is reach wasted.

## Shared gates (every surface, every phase)

- Import firewall: no imports across the three roots in either direction — runtime, types, tests. CI red on any crossing. HTTP + signed webhooks only.
- Closed sets enforced both directions by a build-breaking check; every denial carries a reason code into signed evidence where money is involved.
- Fail loud, no coercion/defaults/silent fallbacks. Money integers (paise), time UTC.
- Convenience is sidecar-engineered: verbatim signed totals, one `approve_url`, resume links restoring exact state. Context loss = bug, red build.
- Local `.env` files only (never committed). Fresh `.env.example` template is drawn in Sidecar S1.

## Explicitly out (phase two, core untouched)

Campaigns/rules/bots/stalled-loops, COD (no prepayment — needs a capture-on-delivery Ledger path), delegated agent-held payment credentials (refused by design, ADR-0013), hosted mall/search/ranking (the permissionless reach doors are specced separately in `PLAN-distribution.md` and are phase two, not never), multi-Merchant tenancy, multi-location, cancellation/restocking fees, store credit, bulk cancel, auto-fulfilment, shipping labels, subscriptions, multi-currency and duties, Consumer accounts and a returns portal, fraud scoring, Merkle batch-anchoring, OMS paths beyond the 9-door trait. Partial refunds are **in** v1 (an amount on the `REFUND` entry, no ninth status).
