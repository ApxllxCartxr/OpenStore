# Spec review findings — 2026-09-17

> Historical pre-build review register. Formerly `docs/REVIEW-findings.md`. Frozen; references use pre-reorganisation filenames.

Review of `SPEC.md`, `CONTEXT.md`, the four plan files, `docs/EXPANSION.md`, and ADRs 0001–0016 on branch `rebuild`, taken before Slice 1 (Sidecar S1) starts. No code exists on this branch, so every finding below is a text edit today and a migration later.

Each finding names where it lands under the `EXPANSION.md` §1 ladder. **None of them is closed by editing this file** — this register routes work, it does not do it. Delete a row when its files are touched.

Severity:
- **Break** — the spec contradicts itself or is unbuildable as written.
- **Gap** — a mechanism the flow depends on that no document owns.
- **Drift** — two documents state the same fact differently.
- **Reach** — table stakes the agent surfaces expect and we do not meet.
- **Fork** — a decision to take before `PLAN-distribution.md` D5.

| ID | Finding | Severity | Lands in |
|---|---|---|---|
| F12 | No tax-invoice series and no credit notes, despite the legal-invoice claim | Gap | `SPEC.md` §4/§13, `PLAN-merchant-site.md` M2/M3 |
| R2 | The Agent Profile is an invented schema where UCP already has one | Reach | ADR-0012, `SPEC.md` §7 |
| R3 | Feed carries no reviews, return policy, or delivery promise | Reach | `PLAN-distribution.md` D1 |
| R4 | No agent-initiated cancel in the closed action set | Reach | `SPEC.md` §6, `PLAN-buyer-chat.md` B1 |
| K1 | Hosted mode: ADR-0007 and ADR-0008 decide who can sell this | Fork | New ADR, `PLAN-distribution.md` D5 |

---

## Breaks

*All cleared. F1, F2, F9 and F10 landed in SPEC §3/§6/§7/§12 with their mirror sides in S2–S7, M3, B1 and B5; F3 landed in ADR-0017.*

## Gaps

*F4–F8 and F11 cleared into SPEC §4/§5/§7/§14, ADR-0011, ADR-0017 and the plan phases. One left, and it is the one that needs a lawyer as much as an engineer.*

### F12 — No tax-invoice series and no credit notes, despite the legal-invoice claim — **half closed**

*The series exists: ADR-0020 puts a gapless, financial-year-scoped `invoice_number` on the Merchant order row, assigned at dispatch rather than at `paid` because CGST §31(1)(a) ties a goods invoice to removal. **Credit notes remain open** — partial refunds are in v1 and nothing yet issues the instrument that reverses a taxable supply, which needs its own series linked to the original invoice. That half still goes to counsel with the e-invoicing/IRN question. The original finding follows.*


`PLAN-sidecar.md` S3 justifies the Gate's arithmetic checks by saying otherwise "a legal invoice and a verifiable one stop being the same document". Nothing in the spec set issues an invoice. There is no invoice number series (sequential, unique, per financial year), no invoice issuance point, and — with partial refunds in v1 — no credit note, which under GST is the instrument that reverses a taxable supply and carries its own series linked to the original invoice.

The Quote already carries almost every field a B2C tax invoice needs (GSTIN, HSN/SAC, per-line rate and split, place of supply, total). What is unowned is the *document* and its numbering, and it belongs on the Merchant side with the tax identity (M2 pricing setup, M3 issuance), not in the sidecar. Add it there, and put the questions that need an actual opinion — e-invoicing/IRN applicability by turnover and counterparty, credit-note timing against the return period — into the same legal review ADR-0016 already schedules in D5, rather than guessing a threshold in a spec file.

---

## Drift

*Both cleared — `EXPANSION.md` §3 now names S2 as the Quote/`cart_hash` owner, and door 9's determinism is stated as side-effect-freedom given Merchant state, with clocks banned from a Quote.*

---

## Reach

Measured against what the agent surfaces actually consume today (see Sources). The money path is ahead of them; discoverability is behind, and two of the four gaps are self-inflicted.

### R2 — The Agent Profile is an invented schema where UCP already has one

ADR-0012 defines our own Agent Profile (name, contact, ES256 JWKS at a well-known URL). UCP already has agent profiles declaring capabilities, credentials and trust tier at a discoverable URL, and merchants intersect them with their own. `SPEC.md` §3 forbids exactly this move for catalogues — "inventing a third private schema would produce a file nobody ingests" — and the rule was not applied to identity.

Read the UCP agent-profile shape and either adopt it or carry ours *inside* it as a namespaced extension, keeping RFC 9421 signing and the thumbprint `agent_id` unchanged. Our admission rule (no tier unlocks money) survives either way; what changes is that an agent already publishing a profile does not publish a second one for us.

### R3 — Feed carries no reviews, return policy, or delivery promise

The feed attributes fixed in `SPEC.md` §3 cover identity, price and availability. Agent surfaces rank and render on more than that — review signal, return window, delivery estimate — and an item with none of them loses the slot regardless of how correct its GST is. D1 should carry the additional attributes as far as the Merchant actually has the data, and M1 should own where the data comes from. Absent review data, say so and treat it as a known ranking handicap rather than discovering it after the first live feed.

### R4 — No agent-initiated cancel in the closed action set — **CLOSED at hour 0**

*Resolved: `cancel-order` is in the action set at scope `start-checkout`, bounded pre-money and to the agent's own orders, refusing `cancel-not-allowed` at or past `paid`. Landed in `core/codes.py`, SPECS/PLAN.md §6.4, SPEC §6, PLAN-buyer-chat B1 and PLAN-sidecar S4. The original finding follows.*


`SPEC.md` §6 reaches `cancelled` by Consumer walk-away or shop reject. `PLAN-buyer-chat.md` B1's action set has `order-status` and `request-refund` and no cancel, so an agent that built a `pending` basket cannot abandon it except by letting it expire — which holds a Quote for 24h and, after a tap, holds stock until the payment window lapses. Add a cancel action bounded like F9 (own orders, pre-money only, `RELEASE` where a hold exists), or state in §6 that abandonment is expiry-only and accept the hold.

---

## Fork

### K1 — Hosted mode: ADR-0007 and ADR-0008 decide who can sell this

Two deliberate decisions determine which company can take this to market, and they point away from a payments company.

ADR-0007 (one sidecar, one Merchant, self-hosted) makes a container per merchant. ADR-0008 (passkey enrollment per Merchant domain) forfeits the one asset a payment provider has and a merchant does not: a single buyer identity amortised across every merchant on the platform. For a self-hosting merchant, per-domain enrollment is phishing resistance and a feature. For a provider, it deletes the product — their version enrolls once on their own domain and roams.

The Indian payment providers have already shipped the money leg on assistant surfaces. What is not taken, and what this spec set actually has, is merchant-side correctness: GST-correct quoting, deterministic refusals, and a disputable audit artifact. That is a real wedge and it is compatible with both paths — but the paths are different companies, and D5 ("gated surfaces, only with merchants behind us") is where the choice stops being deferrable.

**Narrowed 2026-09-19 by ADR-0025, not closed.** This fork assumed centralisation means operating merchants' deployments. It does not have to: the agentic payment rails admit *enrolled participants*, and an entity can enrol as the accountable party while operating no store, holding no keys and touching no funds (ADR-0024, ADR-0025). So the choice is now "hosted platform, Registrar, or neither" rather than "hosted platform or nothing" — and notably the Registrar option does not trip this fork's strongest argument, since under a rail-held mandate the amortised buyer identity lives in the bank rather than in an operator's RP ID, leaving ADR-0008 untouched. The hosted-mode question itself is unchanged and still open.

Take it as an ADR before D5, not during it. Either write `hosted-mode` as an explicitly-not-v1 decision that preserves the shape — multi-tenant scoping, RP ID on the operator's domain, and what that does to ADR-0004's dual identity — or state that self-hosted is the product and that the reach ceiling in ADR-0016 is accepted permanently. Both are defensible. Drifting into the question with three live merchants is not.

---

## Sources for the external claims

- Shopify, *Building the Universal Commerce Protocol* — https://shopify.engineering/ucp
- Shopify agentic commerce developer docs (agent profiles, trust tiers, catalog, checkout) — https://shopify.dev/docs/agents
- UCP Embedded Checkout Protocol binding — https://ucp.dev/specification/embedded-checkout/
- NPCI at Global Fintech Fest 2026, on AI and payment authorisation — https://www.medianama.com/2026/09/223-npci-ai-agents-upi-payments/
- Global Fintech Fest 2026 product launches — https://www.medianama.com/2026/09/223-agentic-ai-products-fintechs-gff-2026/
