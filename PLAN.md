# Plan Index — 3 surfaces, one build order

Full spec in `SPEC.md`, glossary in `CONTEXT.md`, decisions in `docs/adr/`. Each surface has its own plan file with phases, interfaces, and DONE WHEN gates. No phase starts until its dependencies below are green.

- [`PLAN-sidecar.md`](./PLAN-sidecar.md) — `src/openstore/sidecar/`: the product. Gate, notebook, receipt, authority, protocols, `/agentic` console.
- [`PLAN-merchant-site.md`](./PLAN-merchant-site.md) — `demo/merchant-site/`: Next.js + Postgres demo store proving a Merchant can operate it.
- [`PLAN-buyer-chat.md`](./PLAN-buyer-chat.md) — `demo/buyer-chat/`: Ollama chat stranger proving the Consumer flow end to end.

Expanding a plan? Read [`docs/EXPANSION.md`](./docs/EXPANSION.md) first — ladder, contracts table, firewall, and grill rules.

## Build order across surfaces

1. Sidecar S1 (skeleton + harness) + trait contract (S2-contract).
2. Merchant site M1–M3 (store, admin, trait implementation) against the contract.
3. Sidecar S3–S5 (gate + notebook + provider, authority + orders, receipt + checker) verified against a fake Merchant, then the real demo store.
4. Buyer chat B1–B4 (scaffold, direct-add, MCP flow, tap + fake-UPI + receipt) against the sidecar.
5. Sidecar S6 + buyer chat B5 together (protocol translators + golden replays + toggle).
6. Mount + end-to-end (sidecar S7, store M5, chat smoothness gates): latte-to-receipt demo on fake money.

## Shared gates (every surface, every phase)

- Import firewall: no imports across the three roots in either direction — runtime, types, tests. CI red on any crossing. HTTP + signed webhooks only.
- Closed sets enforced both directions by a build-breaking check; every denial carries a reason code into signed evidence where money is involved.
- Fail loud, no coercion/defaults/silent fallbacks. Money integers (paise), time UTC.
- Convenience is sidecar-engineered: verbatim signed totals, one `approve_url`, resume links restoring exact state. Context loss = bug, red build.
- Local `.env` files only (never committed). Fresh `.env.example` template is drawn in Sidecar S1.

## Explicitly out (phase two, core untouched)

Campaigns/rules/bots/stalled-loops, COD, hosted mall/search/ranking, multi-location, partial refunds, auto-fulfilment, OMS paths beyond the 8-door trait.
