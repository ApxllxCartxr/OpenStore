# OpenStore MVP — 48-Hour Build Plan

> Historical 48-hour build plan as executed. Formerly `SPECS/PLAN.md`. Frozen; internal references use pre-reorganisation filenames (`SPEC.md`, `CONTEXT.md`, `PLAN-*.md`, `LOGS.md`).

One document, start to end. Scope is the whole product spine across all three surfaces, built in two days, reusing what already works on `main`. `SPEC.md` is the law this implements; `CONTEXT.md` is the vocabulary; `docs/adr/` are the settled decisions. Where this plan departs from `SPEC.md`, the departure is named in §2 with a reason — nowhere else.

Read order for anyone picking this up cold: §0 (how to execute) → §1 (what ships) → §2 (decisions) → §3 (reuse map) → §6 (frozen contracts) → **§16 (every pinned constant)** → your surface (§7/§8/§9) → §12 (schedule).

**Running this unattended?** §0 is binding and §16 is where every value lives. The plan is written so that nothing has to be asked mid-run.

---

## 0. Execution protocol

This section binds anyone executing the plan — human or agent. It exists because the expensive failures in a 48-hour build are not hard problems; they are small ambiguities resolved silently in the wrong direction.

### The first rule: never ask, decide by the ladder

**This build runs unattended. Nobody is awake. There is no one to ask.**

Every question you can hit has an answer somewhere in this document, and §16 pins every constant the build needs — ports, credentials, prices, GST rates, addresses, timings, rate limits. When something still seems undecided, you resolve it yourself by this ladder, in order, and **keep going**:

1. **§16 Pinned values.** If the thing is a constant, it is there. Use it exactly.
2. **`SPEC.md`, then the ADR it points to.** These are law and they are specific.
3. **§6 frozen contracts**, then the phase text.
4. **Still undecided → take the most conservative option that keeps the DONE WHEN gates reachable, write it in `LOGS.md` under `## OPEN — <phase>` with the reasoning, and continue.** Conservative means: fail loud over coerce, refuse over allow, narrower scope over wider, existing identifier over new one.

**Never stop and wait. Never leave a phase half-built pending an answer.** A decision logged and moved past can be reversed in the morning in five minutes; a track stalled at 3am cannot be recovered at all.

The one exception: if resolving it would require **changing a frozen contract in §6** (the nine doors, the Quote, the `cart_hash` preimage, the closed sets), do not change it. Build everything else in the phase, log it under `## BLOCKED — <phase>`, and move to the next phase. Frozen contracts are frozen because everything downstream pins their bytes.

### Three standing rules

1. **Never invent an identifier.** Reason codes, statuses, scopes, ledger kinds, door names, field names, and route paths come from §6 or from `core/codes.py`. If the thing you need genuinely is not there, **add it to §6 and the enum in the same commit and log it** — do not add it at a call site, and do not wait for permission. A code invented at a call site is a red build by design; a code added to the registry is a decision, which is allowed.
2. **Never widen scope to make a test pass.** If a DONE WHEN gate fails, the fix is in the code under test, or the gate was wrong. If the gate was wrong, say so in `LOGS.md` and change it deliberately. Deleting a gate, loosening an assertion, or adding a special case to satisfy one is the single move that makes the remaining hours worthless.
3. **When two parts of this document disagree, `SPEC.md` wins, then §16, then §6, then the phase text.** Record the disagreement in `LOGS.md`. Do not pick the more convenient reading and move on silently.

### Order of work

Phases run in the order given in §12 and **not in parallel within a track**. A phase is not started until the previous phase's DONE WHEN gates are green, because every gate downstream assumes them.

**If a phase overruns its budget by more than 50%**, do not keep grinding: take the next available cut from §12's list, log it, and move on. Finishing eight phases and cutting two beats finishing six perfectly.

### Commit discipline

- One commit per phase minimum, more if the phase has natural seams. Conventional prefixes (`feat:`, `fix:`, `test:`, `docs:`).
- The commit message names the phase (`feat(A3): gate decide/settle + ledger invariants`).
- **Never `git add .`** — stage the files the phase names.
- A commit whose CI is red is not a finished phase.
- **Commit at every phase boundary even if the phase was cut short.** An unattended run that dies at hour 19 should leave eighteen hours of committed work, not an uncommitted tree.

### The morning report

At the end of the run, `LOGS.md` must contain, in this order: every phase with its gates green or not, every `## OPEN —` decision taken by ladder step 4, every `## BLOCKED —` item, and every cut taken with the hour it was taken at. That file is the handover. Write it as you go, not at the end.

### What "done" means

A phase is done when its DONE WHEN gates are green, its files exist, CI is green, and it is committed. Not when the code is written. **Never report a phase complete without running its gates** — paste the output into `LOGS.md` every time, not only when in doubt. An unattended run's claim that something passed is worth exactly what the pasted output under it is worth.

**Blocked** means one thing only: a DONE WHEN gate cannot be made green without changing a frozen contract in §6. Everything else is a decision, and the ladder resolves it. When genuinely blocked: log it under `## BLOCKED — <phase>` with the exact gate, build everything else in the phase, commit, and **move to the next phase**. Never wait.

---

## 1. What ships at hour 48

A clean machine runs `make up` and gets:

- **`spoiledduckie.localhost`** — a real accessory shop. Home, shop with filters, product-group pages with a colour/size picker, order lookup by token, GST and shipping on every total, `ProductGroup` + `hasVariant` JSON-LD on every group page. *(No direct checkout — cut 5. Buying happens through an agent, which is the point anyway.)*
- **`spoiledduckie.localhost/admin`** — shop ops. Dashboard, catalogue with a variant matrix, inventory moves audit, orders with a Timeline, dispatch + invoice numbers, a refund dialog that moves real (fake) money through the sidecar.
- **`spoiledduckie.localhost/agentic`** — the sidecar console. Keys, policy, provider + enabled methods, exposure, agent allowlist/blocklist, receipts, attribution, overdue holds.
- **`spoiledduckie.localhost/.well-known/*` + `/agent/*`** — the machine doors. Agent-commerce card, UCP manifest, JWKS, MCP endpoint, product feed.
- **`chat.localhost`** — a Claude-shaped buyer chat that has never heard of SpoiledDuckie, self-registers with its own published Agent Profile, shops by tool call, and hands the Consumer a tap.

And the demo runs end to end: **paste URL → search → allow-once → pick a black tote M → address → verbatim quote with GST → tap → fake-UPI approve → auto-return with the thread intact → receipt → `openstore verify` prints green, then prints red on one flipped byte.**

The same flow runs **Cash on Delivery** with no provider in it at all — stock held, no money held, a legal invoice at dispatch, cash captured at a doorstep or an RTO that charges nobody.

That is the deliverable. Everything below exists to make that sentence true on fake money, with the guardrails on.

### The three surfaces and their one rule

| Surface | Root | Stack | Owns |
|---|---|---|---|
| **Sidecar** (the product) | `src/openstore/sidecar/` | Python 3.12, FastAPI, SQLite→Postgres, `uv` | Gate, Ledger, Authority, Evidence, protocols, `/agentic` |
| **Merchant site** (Surface 2) | `demo/merchant-site/` | SvelteKit 2 + Svelte 5, Postgres, `pnpm` | Catalogue, stock, orders, prices, GST, the 9 doors |
| **Buyer chat** (Surface 3) | `demo/buyer-chat/` | SvelteKit 2 + Svelte 5, SQLite, `pnpm` | Threads, tool loop, rendering — and nothing else |
| **Design** (assets only) | `design/` | CSS + woff2 | Tokens, fonts. No logic, ever. |

**The firewall is the one rule that never bends.** No imports across the three product roots in either direction — runtime, types, or tests. HTTP and signed webhooks only. `design/` may be read by the two SvelteKit roots and by nothing else, and may contain only `.css`, `.woff2`, `.svg`, `.md`, and `LICENSE-*`. A CI check asserts all of this and goes red on any crossing, including a planted one.

---

## 2. Decisions taken for the MVP

Each of these is a departure from `SPEC.md` or a live choice the spec left open. Each has a reason and a cost. Nothing here is cut for taste; it is cut for 48 hours or chosen for reuse.

### D1 — Merchant site is SvelteKit, not Next.js — **settled**

`SPEC.md` names Next.js for `demo/merchant-site/`, and its stated reason is "deliberately a different stack from the sidecar to prove HTTP-only integration." SvelteKit satisfies that reason exactly as well — the sidecar is Python either way — and it buys us one toolchain across both browser surfaces and the ability to use `~/projects/portfolío/frontend` as a design system rather than as a reference to re-implement. The portfolio is SvelteKit 2 / Svelte 5 / Tailwind v4 with a finished token layer; porting it to Next.js is roughly half a day we do not have.

Cost: `SPEC.md §1` and `PLAN-merchant-site.md` say Next.js and need a one-line amendment, which lands in the hour-0 commit alongside the frozen contracts.

### D2 — Both Authority kinds ship, not just the default

`upi-pin` is the spec default and the honest Indian story. `passkey` is the stronger binding and the thing that makes an audience sit up — and `main` already has 621 lines of working WebAuthn RP plus a virtual authenticator. Shipping both costs an afternoon rather than a day, and the demo toggles between them to show the receipt's Binding line changing. `confirmed-intent` ships too, because COD rests on it (D4). `mandate` is registered in the closed set, recorded, and refused with a named code — the seam a UAP adapter lands on rather than an ADR reopening.

### D3 — All four protocols are live: MCP, UCP, ACP, AP2

*(Revised. This previously shipped ACP and AP2 as declared-and-refused; they are now real translators.)*

The protocol toggle is the clearest statement of "one core, many envelopes," and two live tabs plus two greyed ones makes that claim at half strength. All four ship as thin translators over the same core: **validate the foreign envelope → core calls → translate back.** No second checkout, no second ledger, no second receipt, and the core Transcript stays byte-identical across all four — which is the whole point and the thing CI asserts.

**The deviations are not a weakness here; they are the argument.** Each protocol wants something at the completion step that this product refuses on purpose, and the badge names it inline:

- **MCP** — live and spoken by the demo chat. No completion model of its own, so no deviation beyond the redirect handoff.
- **UCP** — its totals breakdown maps 1:1 onto the Quote, so the translator carries no pricing logic. Foreign direct-checkout-inside-AI maps to our same-domain approve handoff, which is **UCP's own buyer-escalation path**, not a departure from it.
- **ACP** (spec `2026-04-17`, maintained by OpenAI and Stripe) — five checkout-session operations across four paths, mapping almost 1:1 onto our core, with native `Idempotency-Key` headers and OAuth 2.0 delegate authentication we already implement. Its `POST /checkout_sessions/{id}/complete` hands the merchant a **delegated payment credential** — exactly the authority ADR-0008 and ADR-0013 removed from agents. The other four operations work normally; **that one refuses with a named code and returns the approve URL.** Refused on purpose, not unimplemented, and the badge says which. Full endpoint and schema map in §16.12.
- **AP2** — **not a fourth envelope.** The specification states it "operates as a security feature within a Commerce Protocol," puts catalogue and checkout APIs outside its own scope, and is "designed explicitly to be compatible with the Universal Commerce Protocol." So AP2 ships as a **mandate layer over the UCP translator**, not as a sibling of it. Its **Direct (human-present)** mode — where the agent presents mandates to a Trusted Surface for the user to review and sign — is our approve ceremony, and its `checkout_hash` binding a signed mandate to a merchant-signed Checkout JWT is structurally the same primitive as our `cart_hash` binding an Authority to a merchant-signed Quote. Its **Autonomous (human-not-present)** mode rests on pre-approved constraints, which is our `mandate` Authority kind: registered, recorded, **refused in v1** (ADR-0017).

Three envelopes and one security layer, where two of the four refuse a completion step **on purpose** — that is worth more on stage than four tabs that all say yes, and it makes the conformance badge do real work.

**Cost and how it is funded, stated honestly:** A6 goes 2h → 4h (+2.0). Paid for by taking **cut #2 up front** (the `/agentic` Attribution and Health tabs, −1.0) and making the console's **Exposure tab read-only over seeded values** (−0.5). Net **+0.5h on Track P, which moves it from −0.5h to −1.0h against the day.** It is not free and this document is not going to pretend it is.

Where that hour comes from, in order: the rehearsal block is 1.5h and can compress to 1.0h; failing that, A6's own step-0 escape hatch recovers the full 2h automatically — **if either spec cannot be fetched, that protocol downgrades to declared-and-refused and the badge says so**, which means the risk here is self-limiting rather than open-ended. See §12.

**The specs were fetched before hour 0 and are pinned in §16.12** — ACP `2026-04-17` with machine-readable OpenAPI and JSON Schema, AP2 `main` @ 2026-09-20. That fetch immediately overturned two things this plan had asserted from memory: AP2 is a layer rather than an envelope, and its mandates are two (Checkout, Payment) rather than the three the launch announcement described. **Both errors would have been discovered at hour 19 instead.**

The rule stands for everything deeper: §0's "never invent an identifier" applies with full force to foreign envelope fields. Validate ACP against its published JSON Schema rather than hand-transcribing; read AP2's claim names out of the specification. A translator built from memory is a conformance claim that is not true, which is the one failure this product cannot survive.

### D4 — COD is in

ADR-0018 is right and COD is also, on inspection, the cheapest money path in the product: **no provider call, no payment link, no webhook, no link-expiry sweep, no reconcile.** It is the UPI path with the hard parts removed. What it costs is four small, specific additions, and every one of them lands somewhere the plan already goes:

1. **Authority `confirmed-intent`** (ADR-0017), whose mechanism is a closed set of two — `upi-verify` (a ₹1 verification) or `passkey` — and **never an OTP to a Contact Point**, which proves control of a phone number and binds no funding instrument, which is precisely what RTO fraud already defeats. The Transcript records *which* mechanism, because "confirmed" with no named mechanism is not evidence.
2. **A `CAPTURE` with no preceding `RESERVE`**, explicitly permitted. Escrow-zero needs no exception because it quantifies over `RESERVE` entries, so an order that never writes one cannot violate it. This is ~3 lines in the invariant and one test.
3. **A third deadline on `expires_at`** — the delivery window. That column already means "the next deadline the sidecar will act on," so this is a value, not a schema change.
4. **RTO as `cancelled` with reason `rto` and a restock** — which is already exactly what `cancelled` means: pre-money, stock returned. No ninth status. The canonical set stays 8 and every adapter mapping holds.

COD is also what makes two things already in this plan *legible* rather than arbitrary: dispatch and `invoice_number` are independent of order status (a COD order dispatches and gets a legal invoice while still `confirmed`, before `paid` exists at all — CGST Rule 46 ties the invoice to removal of the goods, not to payment), and the Ledger's entries are about money only while stock is held by door 3. Building COD is how those two stop being assertions.

Budget: ~2h total across both tracks — 1h on Track P (`confirmed-intent`, the COD Ledger path, `collect()`), 0.5h on Track S (the two admin actions), 0.5h in copy and tests. Already counted in §12's totals.

**Said out loud, because the demo should say it too:** on the cash path the Merchant asserts that cash was collected and nothing in the system can check it. That is correct and not a weakness — it is the Merchant's own money and their own Ledger. What the Merchant is buying with `confirmed-intent` is evidence that this basket was committed to by someone who controls a real funding instrument.

And the gap ADR-0018 leaves open stays open with its name on it: **no money is held across a COD delivery**, because no UPI primitive available to us holds it. Reserve Pay is a standing prepaid reserve debited by customer action on the merchant's platform, not an authorize-then-capture, and OpenStore holding the funds itself is refused outright by ADR-0021 and ADR-0023. Named candidates (evidence in place of custody, a block used as evidence then released, partial prepayment) are phase two, not v1.

### D5 — Real notifications become a notification log

The Merchant site owns customer comms (`SPEC.md §10`) and in the MVP it "sends" them to a `notifications` table rendered as a pane in demo-admin, plus a structured log line. Nobody in the demo has an inbox. The send seam is one function, so a real SMTP driver is a later hour, not a later refactor.

### D6 — One Postgres container, two databases, two credentials

`SPEC.md` forbids a shared DB "demo included." One Postgres *container* with `spoiledduckie` and `sidecar` as separate databases, separate roles, and no cross-grant is not a shared DB; it is two databases that happen to share a process, the way two apps on one managed instance do. `main` already ships the `docker/postgres-init/` pattern for exactly this. The buyer chat gets SQLite in its own volume and cannot reach Postgres at all. A test asserts the sidecar role cannot `SELECT` from any `spoiledduckie` table.

### D7 — The buyer chat's model seam defaults to a deterministic planner

Ollama has no models pulled on this machine and a 5GB download mid-demo is a failure mode we choose not to have. The seam is real (`plan(messages, tools) -> ToolCall[]`) with three drivers pinned in §16.9: `scripted` (deterministic, the demo default and what CI runs, with its exact tool-call sequence written out), `ollama` (`qwen2.5:7b`), and `anthropic` (`claude-sonnet-5`). Neither of the latter two is ever required for the demo or for CI. The chat *looks* identical across all three because the model only ever proposes — deterministic code validates against real Merchant data before anything renders (`PLAN-buyer-chat.md B1`). The scripted driver is therefore not a fake demo; it is the same path with the proposer pinned.

### D8 — TLS and DNS are out; the origin contract is in

The demo runs on `http://spoiledduckie.localhost` behind a Caddy container doing the path split. **Chrome and Firefox resolve `*.localhost` to loopback and treat it as a secure context**, so WebAuthn works with no certificate. **Safari does not resolve `*.localhost`** — if the demo must run there, add `127.0.0.1 spoiledduckie.localhost chat.localhost` to `/etc/hosts`, which also makes the demo work on any browser. Rehearse on the browser you will actually present from.

A reference `Caddyfile` with real TLS ships alongside it, unused. `openstore up <domain>` is a script that writes compose + `.env` + keys and prints the first-run URL; it is not a DNS tutorial.

**The `.localhost` name is browser-side only.** Container-to-container calls use compose service names — see §10.1, which exists because getting this wrong is the single most likely way to lose an afternoon.

### D9 — Scope inside each surface is trimmed by depth, never by spine

Kept because they are the spine or because they are cheap and load-bearing: the 9 doors, the Gate's full check order, `quote-fresh`, `quote-consistent` arithmetic, GST both splits + inclusive flag, shipping zones, public/private discount codes, atomic CAS stock, both expiry sweeps, partial refunds, `REVERSAL` as a ledger kind, salted PII commitments with an erasure action, the 5-section sealed receipt, the offline verifier, agent self-registration with SSRF hardening, rate-limit tiers, JSON-LD + product feed, attribution.

Trimmed by depth, **and further by cuts 5 and 4a (§12)**: no direct storefront checkout, no CSV at all, no bulk archive, no pricing-setup CRUD (zones, GST identity and codes are seeded), multi-image galleries (one hero + two alts, generated), dispute UI (the Ledger kind exists, the console does not), retention scheduler (the erasure button exists, the cron does not), Prometheus (counters on `/healthz`, no scrape), the seed *list* reader beyond direct-add + one custom URL.

---

## 3. Reuse map — what comes from `main`

`main` has ~900 passing tests and a lot of correct, expensively-debugged code. The rebuild branch has no `src/` at all. This table is the difference between a 48-hour build and a two-week one.

### Take almost verbatim

| From `main` | Lines | Goes to | Change needed |
|---|---|---|---|
| `core/poai.py` — `canonical_json_bytes`, `hash_section`, `build_hash_chain`, `sign_merchant_jws_compact` | ~160 of 518 | `sidecar/evidence/canon.py`, `chain.py`, `sign.py` | None on canonicalization; collapse the section set to the 5 sections |
| `core/webauthn_rp.py` + `devtools/virtual_authenticator.py` | 621 + 204 | `sidecar/authority/passkey.py` | Challenge becomes the `cart_hash`+amount+expiry binding; add the attestation-vs-`none` fallback (`SPEC.md §7`) |
| `core/idempotency.py` | 156 | `sidecar/core/idem.py` | Add the `cart_id:attempt` key form for `orders.create` |
| `core/oauth.py` — client-credentials, ES256 JWT mint/verify, JWKS | 501 | `sidecar/admission/oauth.py` | Drop auth-code flow; keep client-credentials only |
| `core/session.py` — session + CSRF | ~170 | `sidecar/console/auth.py` **and** ported to TS for demo-admin | argon2id both sides |
| `psp/razorpay_driver.py` — signature verify, link create/fetch/cancel/refund, duplicate-reference handling, reconciliation sweep | ~600 of 1335 | `sidecar/provider/razorpay.py` | Reshape to the 4-method trait; strip checkout-model coupling |
| `verify/checks.py` + `verify/cli.py` | 660 + 181 | `sidecar/verify/` | Keep exit codes 0/1/2; drop campaign/delegation/AAL checks; add `unopened` reporting for PII sections |
| `core/audit.py` (148), `core/health.py` (227), `core/database.py` (405), `config.py` (338) | 1118 | `sidecar/core/` | Settings shape changes; the rest stands |
| `tests/GOLDEN/canonical/*`, `ledger/*`, `razorpay/*` | 18 files | `tests/GOLDEN/` | Copy as-is |
| `Dockerfile`, `docker-compose.yml`, `alembic/`, `pyproject.toml`, `.github/workflows/ci.yml` | — | root | Service names and roots change |
| `scripts/registry_diff.py` | — | `scripts/` | Points at the new enums for `docs/CODES.md` |

### Take the shape, rewrite the body

| From `main` | Why not verbatim |
|---|---|
| `core/compiler.py` (322) | Keep `CompilerContext`/`CompilerResult` frozen dataclasses, the `REASON_CODES` registry, and `get_compiler_digest()`. The check *order* is now fixed by `SPEC.md §4` and splits into `decide()`/`settle()` per ADR-0017 — that is a rewrite of the body against a known-good skeleton. |
| `core/ledger.py` (374) | `RESERVE/CAPTURE/RELEASE/REFUND` and the balance invariants are right. Add `REVERSAL`; make `REFUND` carry `amount_minor` with `sum(REFUND) ≤ captured − already_refunded` enforced at write time; separate the escrow-zero invariant from the refund bound so they fail independently. **Two traps, both verified in the file — read this row before copying anything:** (1) `main` is **double-entry with named accounts** (`merchant_revenue`, `customer`, paired legs per event); this build is **single entry per money event**, because the plan's invariants are stated per order and mixing the two makes the arithmetic silently disagree — do not carry the account legs over. (2) `create_refund_entry` keys idempotency on `checkout_id` **alone**, so a **second partial refund returns the first entry and writes nothing, silently**. Partial refunds are in scope here, so the key must include the refund's own id. |
| `core/inventory.py` (792) | The atomic compare-and-set (`WHERE available >= qty`) is the reference implementation and moves to the **merchant site** in TypeScript — stock is Merchant truth now, not sidecar truth. The sidecar keeps only `available_qty` reads and the bucketing rule. |
| `surfaces/mcp_server.py` (1859) | Take `tools/list`, the tool JSON schemas, `_ucp_envelope`, and `_to_ucp_product`; rewire every body to the new core. The goldens in `tests/GOLDEN/conformance/` are the acceptance test for this port. |
| `surfaces/wellknown.py` (296) | `build_agent_commerce_manifest` and `build_ucp_manifest` port with field edits; the conformance goldens come along. |
| `core/webhooks.py` (286) | HMAC-on-raw-bytes and `event_id` dedupe port directly; the handlers rewrite against the new order model. |

### Do not bring

`agents/*` (2639-line buyer agent, LangGraph, campaign/merchant bots), `core/campaigns.py`, `core/merchandising.py`, `core/holdcancel.py`, `core/handoff.py` (replaced by the resume-token design), `core/policy_signing.py`, `core/aal.py`, `surfaces/studio.py`, `surfaces/adapters/*` (Shopify/Woo/Wix/Magento/BigCommerce/Zoho — phase two, `PLAN-distribution.md`), `buyer_cli.py`, `surfaces/webcart.py`, `notifier.py`.

That is ~7,000 lines deliberately left behind. Bringing any of it back is how a 48-hour build becomes a port of the old system.

---

## 4. Design system — importing the portfolio

Source: `~/projects/portfolío/frontend` (note the accented directory name). SvelteKit 2, Svelte 5, Tailwind v4, GSAP, Lenis. The token layer in `src/app.css` is finished and opinionated; we take it whole.

### What gets copied into `design/`

```
design/
  tokens.css          # from src/app.css — @theme, palette, type scale, .sec/.rail-rule
  fonts/              # 9 woff2 + 2 licences, from static/fonts/
  README.md           # the role rules below, so nobody guesses
```

**Fonts and their roles** (from the portfolio's own comments, kept):
- `EB Garamond` — the one display face, used once per page. Here: the shop name, the chat name, the receipt's title.
- `Open Sauce Sans` — body and titles. 400/500/600 only.
- `Iosevka Term SS08` — **load-bearing**, not decoration. Every price, every paise figure, every SKU, every reason code, every hash, every ledger entry, every countdown. This font is what makes the money surfaces look like instruments instead of a landing page.
- `PP Kyoto` — serif flourishes and pull quotes only.

**Licensing flag:** the folder ships `LICENSE-EBGaramond.txt` and `LICENSE-Iosevka.txt` (both OFL 1.1) and **no licence file for the other two**. Open Sauce Sans is believed OFL but that is unverified here. **PP Kyoto is a commercial Pangram Pangram face.** All of this is fine for a demo on your own machine and is a question the moment anything is recorded, hosted, or shared. Before that happens: verify Open Sauce Sans, and substitute or buy PP Kyoto. Noted now so it is not discovered later.

**Palette** — unchanged token names, so no component ever references a literal colour:
- Light (default): paper `#f6f5f2`, raised `#fffdfa`, ink `#16161a`, line `#e2e0da`, accent `#c2415a`, accent-2 `#2f6b95`, ok `#2f8a4d`.
- Dark: Tokyo Night — bg `#1a1b26`, fg `#c0caf5`, accent `#f7768e`, accent-2 `#7dcfff`, ok `#9ece6a`.
- Theme is stamped on `<html>` before first paint by the portfolio's inline script; the media query only decides for visitors who never chose.

### How the three surfaces stay distinct without three design systems

Same tokens, different weightings:

- **Merchant site** — the portfolio look at full strength. Warm paper, generous whitespace, EB Garamond on the shop name, `--accent` for the buy actions, the `.sec` two-track section shell on the home page, Lenis smooth scroll, GSAP only on the hero. This is the surface that has to look like a shop somebody runs.
- **Buyer chat** — Claude-shaped. Centred 68ch column, message rows, streaming text, tool-call cards that expand to show the exact request JSON, permission modals as centred dialogs over `--scrim`. `--accent-2` is primary here so chat and shop never look like the same app. Mono on every tool name and every rendered quote line.
- **`/agentic` console** — instrument panel. Mono-dominant, hairline rules everywhere (`--line`, never the accent), `--bg-sunken` for panels, tabular figures, no animation. Data density over whitespace. It should read like something you check at 2am, because `SPEC.md §14` says that is what it is for.

Accessibility is not negotiable at any density: `:focus-visible` outlines stay, `prefers-reduced-motion` kills every animation, `--comment` is decoration-only and never body copy, every interactive target ≥ 44px on touch.

---

## 5. Repository layout

```
OpenStore/
├── SPEC.md  CONTEXT.md  PLAN.md  PLAN-*.md      # law, unchanged
├── SPECS/PLAN.md                                # this document
├── docs/adr/  docs/CODES.md                     # CODES.md is generated
├── design/                                      # assets only, read-only to surfaces
├── src/openstore/sidecar/
│   ├── core/          # settings, db, idem, canon, errors, codes (the closed sets)
│   ├── trait/         # the 9-door client + the conformance fake
│   ├── gate/          # decide(), settle(), transcript, cart_hash, quote checks
│   ├── ledger/        # entries, invariants
│   ├── authority/     # kinds.py (the closed set), upi_pin.py, passkey.py, confirmed_intent.py
│   ├── admission/     # oauth.py, profile.py (self-registration + SSRF), ratelimit.py
│   ├── provider/      # trait.py, fake.py, razorpay.py, webhooks.py
│   ├── evidence/      # bundle, chain, sign, public receipt viewer (/receipt/<id>)
│   ├── protocols/     # mcp.py, ucp.py, registry.py (acp/ap2 declared-refused)
│   ├── console/       # /agentic routes + templates
│   ├── verify/        # offline CLI
│   └── app.py         # FastAPI wiring, /healthz, /readyz
├── demo/merchant-site/    # SvelteKit; src/routes/{,shop,p,checkout,lookup,admin}, src/lib/trait/ (the 9 doors)
├── demo/buyer-chat/       # SvelteKit; src/routes/{,api}, src/lib/{model,tools,mcp,identity}
│                          #   lib/identity/ is authored in A4c by Track P — same root, no import crossing
├── lists/seed.json        # scaffolded, empty + example
├── scripts/               # openstore_up.py, seed, demo runner, registry_diff
├── tests/                 # pytest: sidecar + firewall + goldens
├── docker-compose.yml  Caddyfile  Caddyfile.tls.example  Makefile
└── .env.example
```

---

## 6. Contracts frozen at hour 1

Nothing downstream can start until these are written down and committed. They are the last things that change cheaply (`PLAN.md` build order step 1).

### 6.1 The nine doors

All over HTTP on the private network, `POST`, JSON in/out, HMAC-SHA256 over raw body in `X-OpenStore-Signature`, replay window 60s with a nonce. Every mutating door takes `Idempotency-Key`. Every refusal is `{"error": {"code": "<closed reason code>", "detail": "<human>", "fields": {...}}}` — never a 500, never a coerced default.

**The HTTP status is derived from the reason code by one table in `core/codes.py` and is never chosen at the call site.** Broadly: a business refusal against a well-formed request is `409` (`sold-out`, `code-invalid`, `price-changed`, `cap-exceeded`), a malformed or wrongly-shaped request is `400` (`variant-required`, `addon-without-parent`, `quote-inconsistent`), auth is `401`/`403`, `not-found` is `404`, `rate-limited` is `429`. The table is the authority; this sentence is a summary of it. Two implementers choosing statuses independently is how one client ends up retrying what the other treats as fatal.

| # | Door | In | Out |
|---|---|---|---|
| 1 | `catalog.read` | `{since?}` | `{groups: [{id, slug, name, description, media[], option_axes{}, tags[], status}], items: [{sku, group_id, options{}, name, price_minor, tags[], media[], status, low_stock_threshold, hsn_sac, gst_rate_bp}]}` |
| 2 | `stock.read` | `{skus[]}` | `{stock: {sku: int}}` — **exact integers, private network only** |
| 3 | `reserve` | `{order_id, lines[{sku,qty}], discount_code?}` idem `order_id:attempt` | `{reserved: true}` or `409 sold-out` / `code-invalid` / `variant-required`. Atomic CAS `WHERE available >= qty`. Also holds the discount code under the same key. |
| 4 | `commit` | `{order_id}` idem | `{committed: true}` |
| 5 | `release` | `{order_id}` idem | `{released: true}` or `409 no-hold` |
| 6 | `restock` | `{order_id, lines[{sku,qty}]}` idem | `{restocked: true}` |
| 7 | `orders.create` / `orders.read` | create keys on **`cart_id:attempt`** | the order row incl. `tracking_number`, `carrier`, `dispatched_at`, `invoice_number` (all pass-through, sidecar computes none) |
| 8 | `orders.set-status` | `{order_id, status, reason}` idem | same-key-same-result; the serialization point for tap vs expiry vs shop-reject |
| 9 | `quote` | `{lines[{sku,qty}], destination, fulfillment_option_id?, discount_code?}` | the Quote (below). **Read-only, side-effect-free, byte-identical for identical inputs against unchanged state, no clock-derived value anywhere** |

Door 9 carries no clock. A fulfillment ETA is a **day count, never a date** — the trap that turns midnight into a spurious `price-changed`.

### 6.2 The Quote

```json
{
  "currency": "INR",
  "subtotal_minor": 0,
  "lines": [{"sku": "", "qty": 1, "unit_price_minor": 0, "line_total_minor": 0,
             "hsn_sac": "", "gst_rate_bp": 0, "place_of_supply": "KA",
             "addons": [{"sku": "", "amount_minor": 0}]}],
  "discount_lines":   [{"label": "Launch code", "code": "SPOILED10", "amount_minor": -5000}],
  "fulfillment_options": [{"id": "", "label": "", "cost_minor": 0, "eta_days": 0}],
  "fulfillment_chosen": {"id": "", "cost_minor": 0},
  "tax_lines": [{"kind": "CGST|SGST|IGST", "label": "", "rate_bp": 0,
                 "amount_minor": 0, "informational": false}],
  "round_off_minor": 0,
  "total_minor": 0,
  "tax_inclusive": true
}
```

This block is the **shape**, not a literal to copy: `CGST|SGST|IGST` is an enum notation and the zeros are placeholders. Paise integers only, sign intrinsic (discounts negative — `-5000` is ₹50 off). Add-ons fold into their parent line and inherit its rate, HSN/SAC, and Place of Supply — an orphan refuses `addon-without-parent`. `informational: true` means the tax is already inside the subtotal (Indian MRP, the common case) and **must not be added again**; getting this backwards double-charges every order, so both directions are seeded and tested.

### 6.3 `cart_hash` preimage — frozen, never edited after hour 1

`sha256` of UTF-8 JSON, sorted keys, no whitespace, of:

```
{lines: [{sku, qty, price_minor}] sorted by sku,
 quote_hash, destination_hash, contact_hash,
 fulfillment_option_id, total_minor, currency, merchant_domain, expiry_utc}
```

- `quote_hash` = sha256 of canonical Quote bytes (breakdown lines sorted by type then label).
- `destination_hash` / `contact_hash` = `HMAC-SHA256(order_salt, canonical PII bytes)`, where the 128-bit `order_salt` **lives only in the Merchant order row and never in the bundle**. Erasure takes the salt and the commitment can never be opened again. (A bare hash of a phone number is 10^10 guesses — no protection at all.)
- The Consumer authorizes the **delivered total**, which is why `fulfillment_option_id` and `total_minor` are in here and the item subtotal alone is not.
- Anything shown on the approve page is covered **on display**: a change after render invalidates the token even when the total did not move, so a Destination edit cannot redirect a parcel someone already paid for.

A golden vector file pins this at hour 1 and CI compares bytes on every commit.

### 6.3a Where `order_salt` lives, and why it is not obvious

The sidecar must have `order_salt` to compute `destination_hash` and `contact_hash`. The spec says the salt "lives only in the Merchant order row and never in the bundle." Both are true and they need a custody rule, or an implementer will reasonably persist it sidecar-side and quietly break ADR-0011's erasure guarantee — after which erasing an order no longer makes its PII commitment unopenable, which was the entire point.

The rule:

- The **Merchant generates** the 128-bit salt at `orders.create` (door 7) and stores it on the order row.
- It is returned in the `orders.create` response **and in no other response, ever** — not on `orders.read`, not in any agent-facing payload.
- The sidecar holds it **in request memory** for the length of the decision, uses it to compute the two commitments, and **never writes it** to any table, cache, or log.
- Erasure deletes the row and the salt with it. The commitments in the sealed bundle remain, permanently unopenable.

**Test:** grep every sidecar table and every log line for the salt after a completed order; both must be empty. This is in A4's DONE WHEN.

### 6.4 The closed sets — `docs/CODES.md`, generated from the enums, diffed in CI

A code that exists in prose but not in the enum is a **red build**.

- **Ledger kinds**: `RESERVE`, `CAPTURE`, `RELEASE`, `REFUND`, `REVERSAL`
- **Order statuses** (8, no ninth ever): `pending`, `confirmed`, `paid`, `cancelled`, `expired`, `failed`, `refunded`, `completed`
- **Scopes** (4): `search`, `build-basket`, `start-checkout`, `confirm`
- **Authority kinds** (4): `upi-pin`, `passkey`, `confirmed-intent`, `mandate` — the first three live, `mandate` registered/recorded/refused
- **`confirmed-intent` mechanisms** (2, closed): `upi-verify`, `passkey`. An OTP is **not a member** and never becomes one.
- **Binding**: what ∈ `cart|amount|none`, by ∈ `payer-device|payer-bank|merchant`
- **Transcript check results** (3): `pass`, `fail`, `deferred`. `deferred` exists because `upi-pin`'s authority arrives with the money — recording it as `pass` at `decide()` would put a **false statement into signed evidence**. `settle()` must resolve every `deferred` check to `pass` before it captures, and a bundle carrying an unresolved `deferred` fails verification.
- **Payment methods**: `upi`, `cash-on-delivery` live; `card`, `netbanking` declarable by an adapter and enableable without touching the money core
- **Cancellation reasons**: `consumer-walkaway`, `consumer-declined`, `shop-reject`, `rto`
- **Refund request states** (3): `requested`, `approved`, `declined` — the state of an agent's *ask*, never of the money. The movement is `LedgerKind.REFUND` and the order's own `refunded` status; conflating the two would let a request look like money the shop has already sent. Added post-A4 under §0's standing rule 1 when `request-refund` was wired; logged in `LOGS.md`.
- **Reason codes**: `sold-out`, `variant-required`, `addon-without-parent`, `price-changed`, `quote-inconsistent`, `code-invalid`, `destination-unserviceable`, `method-not-supported`, `amount-mismatch`, `time-limit-reached`, `payment-window-elapsed`, `authority-missing`, `authority-kind-not-enabled`, `authority-stale`, `cap-exceeded`, `qty-exceeded`, `count-exceeded`, `window-closed`, `blocked-item`, `tag-refused`, `currency-mismatch`, `merchant-mismatch`, `signature-invalid`, `no-hold`, `rate-limited`, `profile-refused`, `agent-blocked`, `not-found`, `delivery-window-elapsed`, `intent-mechanism-not-enabled`, `dispatch-not-allowed`, `cancel-not-allowed`, `untrusted-key`
- **Tool names** (the closed agent-facing action set, each mapped to its scope in `core/codes.py`): `search`, `read-item`, `add-line`, `remove-line`, `set-destination`, `set-contact`, `choose-fulfillment`, `apply-public-code`, `start-checkout`, `place-order`, `order-status`, `cancel-order`, `request-refund`
- **Trait doors** (9): `catalog.read`, `stock.read`, `reserve`, `commit`, `release`, `restock`, `orders.create`, `orders.read`, `orders.set-status`, `quote`
- **Gate checks** (12, in §6.5's order — these names are written into the Transcript): `authority-present-and-accepted`, `currency`, `merchant`, `window`, `count`, `qty`, `blocked`, `tags`, `caps`, `quote-consistent`, `quote-fresh`, `method-enabled`
- **Provider operations**: `make-link`, `check-status`, `cancel`, `refund`, plus the declarable-and-unconsumed `block`, `capture-block`, `release-block`
- **Availability buckets** (3): `in-stock`, `low-stock`, `sold-out`
- **Ceremony** (2): `enrollment`, `assertion`
- **Discount visibility** (2): `public`, `private`
- **Protocols** (4): `mcp`, `ucp`, `acp`, `ap2`
- **Stock-move channels: `site-direct`, `agent-reserve`, `agent-commit`, `agent-release`, `refund`, `restock`, `admin-adjust`, `rto`

Three codes and one tool name were added at hour 0 under §0's standing rule 1 (registry and spec in the same commit): `dispatch-not-allowed` (ADR-0020 bounds dispatch to `confirmed` onward), `untrusted-key` (PLAN-sidecar S8 already refused a post-revocation signature with it and it was in no set), and `cancel-order` + `cancel-not-allowed`, which close `docs/REVIEW-findings.md` R4 — without them an agent that built a basket could only abandon it, holding a Quote for 24h and, after a tap, holding stock until the payment window lapsed, which is an inventory-denial path any self-registered stranger can open.

`not-found` is load-bearing: an agent-facing order read that belongs to a different `agent_id` returns exactly `not-found`, indistinguishable from an order that never existed. Admission is open by design (ADR-0012), so an unscoped read is an enumeration oracle over other Consumers' order state.

### 6.5 The Gate's fixed check order

`decide()` runs, in this order, stopping at the first failure and recording `reason_code` = that failure:

1. `authority-present-and-accepted` — the kind is one the Merchant enabled, and it is present if it lands before the Gate. **When each kind lands is fixed by the Binding table below and declared by the kind, never by the caller.** `passkey` and `confirmed-intent` land before the Gate and resolve to `pass` or `fail` here; `upi-pin` lands with the money and resolves to `deferred` here, which `settle()` must turn into `pass` before it captures.
2. `currency` → 3. `merchant` → 4. `window` → 5. `count` → 6. `qty` → 7. `blocked` → 8. `tags` → 9. `caps`

   *(5, 6, 9 evaluate at the **Product Group**, so two of each colour cannot walk through a two-per-order cap.)*
10. `quote-consistent` — lines sum to total, signs correct, currency matches, subtotal equals `sum(qty × attested price)` from the pinned Attestation, tax additive-or-informational per the flag, delivery and discounts apportioned and rounded **exactly per §16.11** (which the Merchant's door 9 also implements — the two must agree to the paise or this check fires on a correct quote). Checking a Merchant's sums is not computing prices — without it a buggy or compromised Merchant gets its total signed unchallenged.
11. `quote-fresh` — **once, immediately before `RESERVE`, and never after payment.** Re-call door 9 with identical inputs, byte-compare against the pinned Quote, fail closed with `price-changed` naming the moved line. After money moves, the pinned Quote is frozen and the only check is the Provider amount/currency reconcile — failing a paid order over a Merchant price edit strands real money against no order.
12. `method-enabled` — anything outside the Merchant's enabled set refuses `method-not-supported` **naming what is enabled**.

### The call sequence — write this on the wall

`quote-fresh` is check 11 and `method-enabled` is check 12, which reads as though a check runs after the reserve. It does not. **All twelve checks are inside `decide()`, and nothing happens between them.** The sequence is:

```
   orders.create (door 7)        → order_id, order_salt        [status: pending]
   ── Consumer taps ──
   decide()  = checks 1..12, all of them, Transcript written
   reserve (door 3)              → stock held, code held
   orders.set-status(confirmed)                                [status: confirmed]

   PREPAID                                  COD
   provider.make-link                       (nothing — no provider at all)
   ── payer approves in PSP app ──          ── days pass, parcel moves ──
   webhook → settle()                       admin Record collection → collect()
     verify provider record                   verify nothing (there is no rail)
     resolve deferred checks                  Authority already resolved at decide()
     record Authority + Binding               CAPTURE, no preceding RESERVE
     RESERVE→CAPTURE                          [status: paid]
     [status: paid]                          or admin Record RTO → cancelled + rto
```

So `quote-fresh`'s rule — "once, immediately before `RESERVE`, never after payment" — holds literally: `decide()` is the last thing before the reserve, and nothing re-quotes afterwards. `method-enabled` sitting after it is harmless because it is a static check on a set the Merchant configured, and because **nothing runs between check 11 and check 12 that could move a price**. SPEC §4 froze this order and the Transcript's bytes depend on it, so it is not reordered for tidiness.

**Three entry points, not two.** `decide()` and `settle()` are the prepaid pair. COD needs a third, and conflating it with `settle()` is a mistake worth naming: `collect()` is called by the Merchant's admin through the trait when cash is taken at the door. It verifies no provider record because there is none, records nothing about Authority because `confirmed-intent` already resolved at `decide()`, writes exactly one `CAPTURE`, and sets `paid`. It may not run any Gate check — the goods are already delivered and there is nothing left to refuse.

**What each kind binds** — normative, carried in the Transcript and the receipt, printed by the verifier, never inferred:

| Kind | Mechanism | what | by | Lands |
|---|---|---|---|---|
| `upi-pin` | — | `amount` | `payer-bank` | with the money (`settle()`) |
| `passkey` | — | `cart` | `payer-device` | before the Gate (`decide()`) |
| `confirmed-intent` | `upi-verify` | `none` | `payer-bank` | before the Gate |
| `confirmed-intent` | `passkey` | `cart` | `payer-device` | before the Gate |
| `mandate` | — | — | — | refused, `authority-kind-not-enabled` |

A bundle never reads "verified" unqualified. Ranking the kinds would have been the easier move and the wrong one: a standard admitting only the strongest claim describes one rail, while one that makes every implementer declare the strength of its own claim describes all of them.

The Transcript is byte-stable and stored with the decision. Every check's result is in it — `pass`, `fail`, or `deferred` — and a `deferred` that `settle()` never resolved is a verification failure, not a rounding error.

---

## 7. Surface A — Sidecar

### A1 · Skeleton and guardrail harness — 1.5h

- `src/openstore/sidecar/` laid out per §5. FastAPI app, `/healthz`, `/readyz`.
- `core/codes.py` and `scripts/registry_diff.py` already exist from hour 0. A1 **wires them into CI**: `docs/CODES.md` regenerated and diffed on every push, red when an enum and the doc disagree.
- Money lint (paise integers, no float anywhere in a money path) and time lint (UTC, no naive datetimes) as AST checks over `sidecar/`.
- Import-firewall test: walks all four roots, asserts no cross-root import in runtime, types, or tests, and asserts `design/` holds only asset extensions.
- `.env.example` fresh, with **every variable named in §16.1**: provider keys, webhook secret, RP ID + origin (= the Merchant domain), signing key path, OAuth credentials, DB URLs and roles, `ADMIN_SEED_PASSWORD`, `OPENSTORE_DEV_PROFILE_HOSTS` (empty, documented in place as the SSRF exception it is), `DEPLOY_PSEUDONYM_KEY`. Secrets are generated by `openstore_up.py`, never hand-written.
- **DONE WHEN**: app boots behind the proxy split; harness goes red on three planted violations (unregistered code, float money, cross-root import) and green otherwise.

### A2 · Trait client + conformance fake — 2h

- Typed client for all 9 doors (§6.1) with HMAC signing, idempotency, and closed-code error mapping.
- **The bucketing rule lives here, at the boundary, not at the door**: door 2 returns exact integers; every agent-facing response carries only `in-stock | low-stock | sold-out`, cut at that item's own threshold, with a group reading in-stock when any item is. The one exception is a quantity refusal the Consumer is actively waiting on, which names the remaining count and is rate-limited and counted as the oracle it is.
- In-memory conformance fake implementing all 9 doors: negative-proof stock, external-sale simulation, byte-deterministic quote, and a quote that deliberately moves between calls to prove the Gate catches it.
- **DONE WHEN**: conformance suite green — retry-safety (same key, same result), external-sale visibility, quote determinism byte-for-byte, a flat-price Merchant's zero-value tax and fulfillment lines passing unchanged, `variant-required` on a group id at every door taking a SKU.

### A3 · Gate + Ledger + provider — 4h *(the longest single block; start it early)*

- `gate/cart_hash.py` — §6.3, frozen, golden-pinned.
- `gate/decide.py` / `gate/settle.py` — §6.5, byte-stable Transcript, dry-run with zero side effects.
- `ledger/` — append-only, **one row per money event** (`kind`, `order_id`, `amount_minor`, `currency`, `created_at`, `idempotency_key`). `main`'s double-entry account legs are deliberately **not** carried over: the invariants below are stated per order, and mixing the two representations makes the arithmetic disagree without failing anything loudly. Kinds: `RESERVE → CAPTURE/RELEASE/REFUND/REVERSAL`. Two invariants, deliberately separate and separately testable: **holds close** (each `RESERVE` ends in exactly one `CAPTURE` or `RELEASE`) and **refunds are bounded** (`sum(REFUND) ≤ captured − already_refunded`). A `REVERSAL` is bounded by neither and is written **even when it drives net cash negative** — money that has already left must be recorded; negative net raises an alert, never a refusal.
- **COD in the Ledger** (ADR-0018): the Ledger's entries are about money only; stock is held by door 3 alone. `cash-on-delivery` writes **nothing** at order time, a single `CAPTURE` **with no preceding `RESERVE`** when the Merchant records collection (permitted explicitly — money that never passed through a hold cannot close one), and **nothing at all on RTO**, because nothing moved and the goods return through `restock`. Escrow-zero is untouched and needs no exception: it quantifies over `RESERVE` entries.
- `provider/trait.py` — `make-link / check-status / cancel / refund`, plus a declared-but-unconsumed block capability (the seam ADR-0024 wants; **not** a Reserve Pay COD hold, which ADR-0018 retired). `fake.py` declares every method; `razorpay.py` declares UPI and refuses live keys at boot.
- Webhooks: HMAC on **raw bytes**, `event_id` dedupe, out-of-order safe, amount/currency reconcile against the order (mismatch → `failed` + `RELEASE` + `amount-mismatch`). The Provider decides the money-moved fact only, never authority.
- **DONE WHEN**: golden decision vectors byte-identical; `sold-out` writes no `RELEASE` and the trait refuses one if attempted; `amount-mismatch` releases exactly its own hold; **a COD order writes its first Ledger entry at collection and no entry of any kind at order time, writes nothing on RTO, and never touches the Provider**; 50 concurrent taps on 5 units yield exactly 5 `confirmed` and stock never goes negative **against the conformance fake** (B2 repeats this against real Postgres — they test different things and both are required); over-refund refused; **two successive partial refunds on one order both write, with distinct idempotency keys** (this is the `main` trap in §3 — a test that would have caught it silently doing nothing); a `REVERSAL` after a full refund lands net-negative and alerts; a quote that moves between pin and capture fails `price-changed` and moves no money; a disabled method refuses naming the enabled set; webhook forgery, replay, and reorder all rejected; crash-mid-link adopts via `check-status` instead of double-creating.

### A4 · Authority + orders — 4.5h *(3.5h + 1h for `confirmed-intent` and the COD lifecycle)*

- `authority/kinds.py` — the closed set with each kind declaring when it lands (before the Gate vs with the money) and what it binds.
- `upi_pin.py` — the default. Payer authenticates in their own PSP app against a named payee and an exact amount; the basket binds by reference through the Quote the approve page rendered; the Provider's signed record closes the chain. Binding: `amount` / `payer-bank`.
- `passkey.py` — port from `main`. Per-domain enrollment performed **inside** the first approve ceremony — one page, enroll-and-tap, so roaming five shops costs five taps and not five signups. A single WebAuthn `create()` whose challenge **is** the `cart_hash`+amount+expiry binding, UV required. Under `none` attestation nothing in the response is signed, so we request attestation, verify it, and fall back to an immediate `get()` over the same challenge when we cannot get one — two prompts, named and counted, rather than a binding asserted and not proven. The Transcript records `ceremony = enrollment | assertion`, distinct from the Authority kind. Binding: `cart` / `payer-device`.
- `confirmed_intent.py` — the COD Authority. No payment moves, so it binds whatever the Merchant chose from the closed set of two: `upi-verify` (a ₹1 verification, Binding `none` / `payer-bank`) or `passkey` (Binding `cart` / `payer-device`). **An OTP to the Contact Point is deliberately not a member** — it proves control of a phone number, which is exactly what RTO fraud already defeats, and binds no funding instrument. A mechanism the Merchant has not enabled refuses `intent-mechanism-not-enabled`. The Transcript records the mechanism, because "confirmed" with no named mechanism is not evidence.
- `consumer_id = HMAC(DEPLOY_PSEUDONYM_KEY, payer_handle)` — per-Merchant-domain, never a cross-merchant identifier. The payer handle plaintext lives only in the Merchant order row.
  - **One path has no payer handle**: a COD order authorized by the `passkey` mechanism never touches a payment rail, so there is no VPA to derive from. There, the **passkey credential ID** is the handle source, and the Transcript records which source was used (`payer-handle` or `credential-id`) so the derivation is never ambiguous to a verifier. Leaving this unstated would produce a null `consumer_id` on exactly the path where attribution matters most.
- `admission/` — two routes, one authority. OAuth client-credentials for allowlisted agents; self-registration for strangers: fetch the Agent Profile from its well-known URL, pin its ES256 JWKS, verify RFC 9421 signatures (method, target, `Content-Digest` per RFC 9530, `created`, `expires`, nonce, short window) on every call, `agent_id` = RFC 7638 thumbprint, issue a short token on the spot.
- **The profile fetcher is an SSRF sink and is hardened as one**: HTTPS only, public IPs only, loopback / RFC1918 / link-local / `169.254.169.254` refused, resolve-then-pin against DNS rebinding, no cross-host redirects, hard size and timeout caps, its own registration rate limit. The dev exception (`OPENSTORE_DEV_PROFILE_HOSTS`) admits **named hosts only, never a CIDR**, **permits `http` for exactly those entries** (§10.1 — without this the demo's own chat is refused by our own hardening), keeps the metadata address refused unconditionally, logs every use, flags it in health output and the `/agentic` banner, and **refuses to boot alongside live provider keys**. A bypass that is silent, broad, or bootable in production is how bypasses reach production.
- Rate limits **per §16.8**: per-tier (self-registered low, allowlisted high) and per-IP on every `/agent/*` route, with separate throttles on tap-token issuance, approve attempts, and discount-code attempts. Every wrong code refuses as the same `code-invalid` with **no message and no timing tell** — otherwise door 9 answers "is this a code?" all day.
- `/agentic/approve?t=<one-time, 5-min, single-use>` — the tap page. Optional `Have a code?` field for `private` codes: applying one re-calls door 9, re-renders the total, and rebinds the ceremony to the post-application `cart_hash` **before anything is signed**, so the code never transits the agent. Auto-return via a resume URL carrying an **unguessable, single-use, session-bound** token that resolves to `order_id` + `chat_thread_id` server-side — never those two as bare URL parameters, which would hand anyone with the link someone else's checkout.
- Order lifecycle through the trait, **8 statuses on both paths**: `pending` (24h, ₹0, **no stock held** — that window is the Quote's validity, not an inventory hold) → `confirmed` (stock held by door 3) → `paid` / `cancelled` / `expired` / `failed` / `refunded` / `completed`. Tap, expiry, shop-reject, and COD collection all serialize on one `set-status` key.
  - **Prepaid**: `confirmed` means the Consumer initiated payment; it is time-boxed to the payment link's own expiry, default 15 min and never longer than the Provider's lifetime, because an abandoned tap must not hold stock.
  - **COD**: `confirmed` means the intent is verified and stock is held with **no money held at all**; `paid` is the cash collected at delivery; an RTO is `cancelled` with reason `rto` and a restock, which is already what `cancelled` means — pre-money, stock returned.
  - `expires_at` is one column meaning "the next deadline the sidecar will act on" and now carries **three**: the 24h `pending` Quote-validity sweep (`time-limit-reached`, nothing held, nothing to return), the `confirmed` payment-window sweep on the prepaid path (`payment-window-elapsed`, plus a `RELEASE`), and the **delivery window** on the COD path (`delivery-window-elapsed`, which alerts the Merchant rather than auto-cancelling — a parcel that is late is not a parcel that is lost, and only the Merchant knows which).
- **DONE WHEN**: happy path green for all three live kinds, each recording its own Binding and, for `confirmed-intent`, its mechanism; a COD order reaches `paid` with one `CAPTURE` and no `RESERVE`, and an RTO reaches `cancelled` with reason `rto`, a restock, and an empty Ledger; an OTP offered as a `confirmed-intent` mechanism is refused at the enum and cannot be configured; a declined `upi-pin` releases its hold inside the collect window and the SKU is immediately buyable again; a hold cannot be created without spending an approve token; a **Destination edit after render invalidates the token even though the total did not move**; an amount change owes a fresh Authority under `upi-pin` while a Contact Point fix does not, and both re-hash; `consumer_id` is stable across two orders here and differs under a second deploy key, and is non-null on a passkey-mechanism COD order; no payer-handle plaintext appears in any Transcript, bundle, or log; **`order_salt` appears in no sidecar table and no log line after a completed order** (§6.3a); replayed or stale signed agent requests refused by window and by nonce; profile URLs at loopback, RFC1918, and the metadata IP each refused **before any fetch**; the dev allowlist admits exactly its named host and nothing else in that range; boot fails when the allowlist meets live keys; a stranger transacts end to end with no prior Merchant action; a blocklisted profile is refused at registration; an abandoned `confirmed` order releases stock at link expiry; **no agent-facing payload in the whole suite carries an exact stock integer except a waited-on quantity refusal**.

### A4c · Agent signing kit — 1.5h *(Track P, but the files live in `demo/buyer-chat/`)*

The chat has to **sign** RFC 9421 requests and the sidecar has to **verify** them. Those are the same specification read from two ends, and A4 is already writing the verifier. Splitting them across two builders in two languages means the signature base gets debugged twice, separately, by two people who each think the other side is wrong — which is the classic way a day disappears.

So whoever writes the verifier writes the signer, in the same sitting, against the same test vectors.

- `demo/buyer-chat/src/lib/identity/` — ES256 keypair generation at first boot via Node's built-in WebCrypto (P-256 / SHA-256, no heavy dependency), key file outside the session DB and gitignored, additive `kid` rotation.
- Agent Profile publishing at the chat's well-known URL with its JWKS.
- RFC 9421 request signing over method, target, `Content-Digest` (RFC 9530), `created`, `expires`, plus a nonce. **Use the `http-message-signatures` npm package rather than hand-rolling the signature base** — it is the fiddly part, it is someone else's solved problem, and the plan's rule is to reuse before writing.
- Shared test vectors: one fixture set, signed by this code and verified by A4's verifier, asserted in **both** test suites.

**This does not touch the firewall.** The firewall forbids *imports across roots*, not authorship — a person is not a root. These files live in `demo/buyer-chat/`, import nothing from the sidecar, and reach it only over HTTP. The firewall test runs against them unchanged.

- **DONE WHEN**: the chat signs a request the sidecar's verifier accepts, and one deliberately corrupted signature base is rejected by that verifier with a named code — both assertions present in both suites.

### A5 · Evidence + verifier — 2h

- 5-section sealed bundle — **bought / tapped / decided / told / moved** — hash-chained, Merchant-ES256-signed, no Merkle (ADR-0009), carrying its own JWKS snapshot so offline verify is really offline, opening by unguessable 128-bit `receipt_id` with no login.
- `tapped` carries the Authority kind, its mechanism where it has one, and its Binding **verbatim**, and the verifier prints all three rather than reporting an unqualified "verified." The honest claim for the default kind is: tamper-evident throughout, third-party attested at the money step by the payer's own bank, buyer-attested over the basket **only** under `passkey`. On a COD receipt the `moved` section shows a `CAPTURE` the **Merchant asserts** — no rail attested it, the verifier says so in those words, and that is the truthful thing to print rather than the flattering one.
- `bought` carries the Quote breakdown verbatim (shipping and GST exactly as the Consumer saw them) and commits to Destination and Contact Point by salted HMAC. The PII binding is therefore an **online** check: offline verify covers the chain, the signatures, and every non-PII fact, and reports those two sections `unopened` rather than failing.
- Each order pins the Attestation `digest` it used — `sha256(sku | price_minor | sorted tags | canonical resolved-options JSON)` — so a later rename of an option axis cannot rewrite what was bought.
- Refunds **version**: v1 stays verifiable as-is; a refund appends a `moved` entry and a fresh signature as v2.
- **When a COD bundle is sealed**, which is not obvious and would otherwise be guessed: money moves days after `confirmed`, so there is no payment moment to seal at. The bundle is sealed **at `confirmed`** with an **empty `moved` section** — every other section is already final, because the basket, the Quote, the Authority and the decision all exist — and **collection appends a `moved` entry as v2 through exactly the path a refund already uses**. No new machinery, and the Consumer has a verifiable receipt from the moment they commit rather than only after they pay.
- `openstore verify <bundle>`: exit `0` valid, `1` tampered **naming the exact link**, `2` untrusted key (including a key revoked *before* the bundle's timestamp, while bundles signed before revocation stay valid, ADR-0014).
- Browser receipt viewer at **`/receipt/<id>`, a public route outside the console's auth boundary**. It cannot live under `/agentic`, which is session-authenticated for the Merchant — a receipt that opens by unguessable ID *with no login* is the whole point, and putting it behind the Merchant's session means no Consumer can ever open their own. The ID is the only credential; 128 bits is the protection. In the design system, printable.
- **DONE WHEN**: clean bundle verifies; a one-digit tamper fails naming the link; an erased order's bundle still verifies and renders Destination as `erased` while an altered row renders `tampered`; a partial refund produces v2 showing `Refunded ₹X of ₹Y` with v1 still valid; a bundle signed by a since-revoked key verifies if signed before revocation and fails `untrusted-key` if after; live keys refused at boot.

### A6 · Protocols — 4h *(all four live; was 2h for two — see D3)*

> **STEP 0 IS DONE.** The specs were fetched on 2026-09-20 and the results are pinned in **§16.12** — read it before writing a line of this phase. ACP is spec `2026-04-17` with machine-readable OpenAPI and JSON Schema; AP2 is `main` @ 2026-09-20 and is **a security layer over UCP, not a fourth envelope**. Validate ACP against its published JSON Schema rather than transcribing field names by hand, and take AP2's claim names from its specification. Anything §16.12 does not answer comes from the documents it links, never from memory.

- `protocols/mcp.py` — live. `tools/list`, tool schemas and envelope ported from `main`; every body rewired to the new core. Least-privilege per tool, scope-checked.
- `protocols/ucp.py` — live. UCP's totals breakdown maps 1:1 onto the Quote, so the translator carries **no pricing logic**. Direct-checkout-inside-AI maps to our approve handoff, which is UCP's own buyer-escalation path.
- `protocols/acp.py` — live, targeting spec **`2026-04-17`** and never `unreleased`. Implements the five checkout-session operations across four paths (§16.12) against the published OpenAPI: `create` / `update` / `get` / `cancel` map onto Pending Cart, quote, cart read and `cancelled`; **`POST /checkout_sessions/{id}/complete` refuses the delegated-payment-credential step** with a named code and returns the approve URL. ADR-0008 and ADR-0013 enforced at the envelope boundary, not an unimplemented gap. Honour ACP's native `Idempotency-Key` and `API-Version` headers — the first maps straight onto our per-attempt keys.
- `protocols/ap2.py` — live, and **it is a layer on `ucp.py`, not a peer of it** (§16.12). Verify the Checkout Mandate JWT, confirm our merchant-signed Checkout artifact hashes to its `checkout_hash` claim, honour the `vct` schema version, and return a Checkout Receipt JWT. **Direct / human-present** completes through the approve ceremony. **Autonomous / human-not-present** refuses `authority-kind-not-enabled` — that mode is the `mandate` kind (ADR-0017). Reuse A4c's and A4's ES256 verification; do not write a second JWT verifier.
- `protocols/registry.py` — one place naming every protocol, whether it is an **envelope** (MCP, UCP, ACP) or a **layer** (AP2), its live/refused capabilities, and its deviation text. The header toggle and the badge both read from it, so a protocol cannot be live in one and stale in the other.
- **Conformance badge** names its deviations inline, per protocol: capability supported, redirect-only completion, which payment instruments this Merchant has enabled, and **which completion step this envelope wanted that we refuse and why**. A golden replay asserts the deviation text is present for every protocol. This is what keeps "conformant" from becoming a lie.
- Header toggle `[MCP | UCP | UCP+AP2 | ACP]` replays the same flow. AP2 appears as a layer on UCP because that is what it is — a fourth peer tab would be a nicer-looking lie.
- **DONE WHEN**: one golden end-to-end replay **per protocol** (search → allow → cart → tap → fake-UPI → receipt → verify) with the **core Transcript pinned byte-identical across all four** — envelopes differ, core decision bytes do not, and that assertion is the single most valuable test in this phase; an ACP `complete` carrying a delegated credential refuses with its named code and returns an approve URL while the other four operations succeed; an AP2 Checkout Mandate whose `checkout_hash` does not match our Checkout artifact is rejected, a matching one completes through the approve ceremony, and an autonomous-mode mandate refuses `authority-kind-not-enabled`; `main`'s MCP and UCP conformance goldens still pass; the badge's per-protocol deviation text is asserted **and names the ACP spec version it targets**; a test asserts the vendored OpenAPI matches `spec/2026-04-17` so a quarterly revision is noticed rather than silently drifted past.

**Scope boundary, so this does not become six hours:** ACP and AP2 are **request-path live and golden-replay proven — there is no client integration for either.** You can POST a correct envelope, or a signed mandate, and get a correct response including the named refusal. Only MCP has a live client (the demo chat). That is genuinely live rather than stubbed, and it is what the 4h buys. ACP's published JSON Schema does a large part of the validation work for free, which is why five endpoints fit inside this budget.

### A7 · `/agentic` console — 1.0h *(2.5h less cut #2 and a read-only Exposure tab — funding A6's four protocols, see D3)*

Instrument-panel styling per §4. Tabs:

- **Keys** — enroll / rotate (additive by `kid`, never re-signs history) / revoke (invalidates the future, not the past) / export. First run refuses to continue until the encrypted export is acknowledged as saved.
- **Policy** — window / count / qty / blocked / tags / caps, CRUD, with caps evaluated at the Product Group stated in the UI so nobody wonders. **Seeded values in §16.4.**
- **Provider** — adapter, enabled method subset, webhook status, link lifetime. **Seeded: `fake`, methods `upi` + `cash-on-delivery`, link lifetime 15 min (§16.4, §16.7).**
- **Authority** — which kinds this Merchant accepts. **Seeded: `upi-pin`, `passkey`, `confirmed-intent`; mechanisms `upi-verify` + `passkey` (§16.4).**
- **Exposure** — which policies are public to agents. **Read-only over the seeded values (§16.4)** — display, no editing.
- **Agents** — allowlist, blocklist, tier, `agent_id`, last seen, revoke.
- **Receipts** — list, open, verify-in-browser.
- ~~**Attribution**~~ — *(**cut #2, taken up front to fund A6.** Full scope kept for later: agent-sourced orders and revenue split by `agent_id` over a date range — the number that tells a Merchant whether any of this is working. The orders already carry `agent_id`, so this is a query and a table when it returns, not a redesign.)*
- **Health** — ~~the full panel~~ *(cut #2)*, reduced to the two things that cannot be dropped: **overdue holds** (the sidecar owns all three expiry clocks, so a wedged sidecar holds stock forever and the only defence is that somebody can see it) and the **dev-allowlist banner** when it is set. Counters still emit to structured logs and `/healthz`; they just have no panel.
- **DONE WHEN**: every shipped tab renders against seeded data; policy edits change Gate outcomes without a restart; an overdue hold appears within one sweep interval and the documented release path goes through the sidecar and never a Merchant-side write; the dev-allowlist banner shows when the allowlist is non-empty.

### A8 · Mount, install, operations — 1.5h

- Caddy path split on one origin (passkeys are happy, ADR-0008). **The auth boundary is per route and is part of the contract:**

  | Path | Served by | Auth |
  |---|---|---|
  | `/` and everything else | store | public (admin below) |
  | `/admin/*` | store | Merchant session + CSRF |
  | `/.well-known/agent-commerce.json`, `/.well-known/ucp.json`, `/.well-known/jwks.json` | sidecar | public, unauthenticated by design |
  | `/agent/*` | sidecar | agent token (OAuth or self-registered), rate-limited by tier |
  | `/agentic/approve` | sidecar | the one-time tap token — **not** the Merchant session |
  | `/agentic/*` (everything else) | sidecar | Merchant session + CSRF |
  | `/receipt/<id>` | sidecar | **public** — the unguessable 128-bit ID is the only credential |

  `/agentic/approve` and `/receipt/<id>` being public inside an otherwise-authenticated prefix is deliberate and is the kind of thing a proxy config gets wrong once and then serves wrong forever, so it is written down here rather than left to the `Caddyfile`.
- `scripts/openstore_up.py`: writes compose + `Caddyfile` + a fresh `.env`, generates the signing key, prints the encrypted export to save, prints the first-run `/agentic` URL. **A Merchant who can point DNS can run it; anything needing a hand-edited YAML before first boot is a bug in this gate.**
- `/agent/feed.json` over the exposed catalogue in **Google Merchant Center attribute names** (`id`, `item_group_id`, `title`, `description`, `link`, `image_link`, `availability`, `price`, `brand`, `condition`, `color`, `size`, `gtin`/`mpn`), one item per Catalogue Item, grouped by `item_group_id`. `availability` takes **the reader's published enum** and never the Availability Bucket verbatim — `low-stock` is not a member of it and folds into in-stock; a feed carrying an invented value is rejected by the only reader that matters.
- Structured money-path logs: order, agent, consumer, reason code, duration. Never secrets, never PII plaintext. Transcripts are forensic; logs are operational; they are not the same artifact.
- **DONE WHEN**: `make up` on a clean checkout reaches a verified receipt with zero hand-edited files.

---

## 8. Surface B — Merchant site (SpoiledDuckie)

### B1 · Schema, seed, storefront — 4.5h *(2h schema + seed, 2.5h storefront)*

> **CUT 5 IS IN FORCE (§12).** Build **no direct cart and no direct checkout**. The storefront browses and hands off to an agent; it never creates an order itself. The `site_carts` table and its sweeper are **not built** either — they existed only to hold stock for that cart. The full-scope text below keeps the description for a later phase; what you build is browse + lookup + the agent hand-off. Everything else in this phase is unchanged and still required.

**Tables** (Postgres, migrations from hour 1):

`product_groups` (slug unique, name, description, media JSON, `option_axes` JSON, tags, status — **presentation only, never sellable, never a cart line**) · `catalogue_items` (sku unique, group_id, `options` JSON = resolved values with a unique constraint per group so one combination cannot exist twice, name, `price_minor` int ≥ 0, tags, media, status, `low_stock_threshold`, `hsn_sac`, `gst_rate_bp` — **price, stock, and threshold live here and never on the group**) · `stock` (one row per Catalogue Item, int ≥ 0, `CHECK (available >= 0)`, no nulls; missing or negative fails catalogue load loud) · `stock_moves` (append-only: when, sku, delta, channel, actor, reason) · `shipping_zones` (matcher, flat `cost_minor`, `eta_days`) · `discount_codes` (code unique, `amount_minor` or `percent_bp`, window, per-order cap, `visibility` public|private, `max_uses` — private defaults to 1 — `uses_count`) · `code_reservations` (unique on code where held — the row that makes single-use codes race-safe inside `reserve`) · ~~`site_carts`~~ *(cut 5 — not built; it existed only to hold stock for the direct cart)* · `merchant_tax_identity` (GSTIN, registered state, price-inclusive flag) · `orders` (the 8 statuses, lines, pinned Quote, `refunded_minor`, Destination + Contact Point + payer handle + resolved options as **the only plaintext copy**, `agent_id`, `consumer_id`, `cart_hash`, `quote_hash`, `attestation_hash`, `order_salt` 128-bit, `tracking_number`, `carrier`, `dispatched_at`, `invoice_number`, `erased_at`, `expires_at` (one column, **three** deadlines — `pending` Quote validity, the prepaid payment window, the COD delivery window — always meaning "the next deadline the sidecar will act on"), `authority` (kind + mechanism + Binding), `payment_method`, `collected_at` (COD cash collection, nullable), `cancel_reason`, `receipt_id` 128-bit) · `refunds` (order_id, `amount_minor`, reason, per-line restock flags, provider ref) · `invoice_sequences` (financial_year, next_number) · `notifications`.

Why one `stock` row per **item** and not per group: one count for "the tote" would let black selling out mark red sold out, and would point `reserve`'s compare-and-set at the wrong row.

**Seed — 12 Product Groups → exactly 15 Catalogue Items.** Counted, not estimated, because the seeder is a checklist and "~20" is how a seed ends up with a group that has no sellable item in it. **§16.3 carries the authoritative table with every price, HSN, GST rate, stock count and threshold — build the seed from that, not from this summary**, and §16.2/16.4/16.5/16.6 carry the tax identity, policy, zones and codes:

| # | Product Group | Axes | Items | Note |
|---|---|---|---|---|
| 1 | Tote | colour {black, red} × size {M, L} | **3** | black/L was never made — **absent**, not zero-stocked. This is what the picker must render as unavailable. |
| 2 | Cap | size {S, M} | **2** | |
| 3 | Sticker pack | — | 1 | |
| 4 | Keychain | — | 1 | |
| 5 | Hair clips | — | 1 | |
| 6 | Phone charm | — | 1 | |
| 7 | Pin set | — | 1 | |
| 8 | Plush mini | — | 1 | `limited` tag, 10 units, policy cap 2 per order |
| 9 | Charm-bar seat | — | 1 | a **service**, countable slots, taxed where performed — this is what makes a two-place-of-supply basket possible |
| 10 | Gift-wrap | — | 1 | Add-on, never standalone |
| 11 | Extra charm | — | 1 | Add-on, never standalone |
| 12 | Recalled item | — | 1 | `recalled` tag, so the Gate's `blocked` check has something to refuse |
| | | **Total** | **15** | |

Plus: tax identity, per-item GST rate + HSN/SAC, two shipping zones (intra-state and inter-state, so **both GST splits are exercised**), one unserviceable postal range, one public code, one private code, one 24h-pending example order, and **one COD order sitting at `confirmed` with stock held, an invoice number, and an empty Ledger** — the fastest way to see that stock and money have come apart.

**Seeding rule, because the Ledger is sidecar-owned and this table is not:** any seeded order that must be visible on **both** sides — in admin *and* in `/agentic` — is created by **driving the real flow** from `scripts/seed_demo.py` over HTTP, never by inserting rows. A row inserted merchant-side has no Transcript, no Ledger, no receipt, and no attribution, so it would demo a hollow order and its "empty Ledger" would be vacuously true for the wrong reason. Rows inserted directly are fine only for catalogue, pricing, and stock, which are Merchant truth by definition. Product images are generated placeholders in the design palette — no scraping in the MVP.

**Storefront**: home (hero, featured, search) · shop (category/tag/price/availability filters, ordered results, pagination) · **group page** (gallery, picker over the group's axes, add-ons, where picking resolves to exactly **one** Catalogue Item before `Add` is enabled, showing its own price and its own `in-stock/low-stock/sold-out`, with unavailable combinations shown as unavailable) · ~~direct cart + checkout~~ *(**cut 5 — do not build.** Full scope, kept for a later phase: takes no payment, a second money path being forbidden; the order is created when the shop confirms it and marked paid by hand in admin; the cart holds stock for 30 min so an abandoned browser cart cannot leak inventory)* · a **Buy via agent** panel on every group page carrying the sidecar card URL, which is what replaces it and is the surface the demo actually uses · order lookup **by unguessable token only**, with the human order number additionally requiring a matching Contact Point under a throttle — a guessable lookup key is an IDOR that hands strangers other people's addresses.

`ProductGroup` + `hasVariant` + per-variant `Offer` JSON-LD on every group page, publishing the shopper-facing price consistent with the tax-inclusive flag — India requires tax-inclusive prices in feeds, and an `Offer.price` that excludes GST understates every listing.

- **DONE WHEN**: store boots alone; seed loads with zero null-stock rows; catalogue validation rejects missing/negative/float stock or a missing GST rate or HSN with a **named** error; home → shop → group → resolved variant → **Buy via agent panel showing the card URL** → lookup works with zero sidecar imports; the unavailable combination renders as unavailable; **no public storefront route creates an order** — door 7 `orders.create` (B2, private network, sidecar-called) is the only path that does, and cut 5 means nothing else may. Assert it, so a second money path cannot reappear by accident.

### B2 · The nine doors — 3h

Implemented natively over HTTP on the private network, per §6.1.

The three pricing rules that decide whether the invoice is legal — **the exact arithmetic, rounding order and worked example are in §16.11, and that is the authority**; these are the reasons behind it. Two implementations that guess differently trip the Gate's `quote-inconsistent` check on a correct quote:

1. **Delivery is taxed.** A delivery charge follows the principal supply, so the fulfillment amount is apportioned across lines by taxable value and taxed at each line's own rate and Place of Supply.
2. **Discounts apportion the same way**, by taxable value, largest-remainder, half-up to the paise.
3. **Rupee rounding is an explicit `round_off` line**, never a silent adjustment to a total.

Place of supply is **per line**: goods take the Destination state; a service sold at the premises takes the place it is performed. So one basket holding a tote and a charm-bar seat legitimately carries IGST on one line and CGST/SGST on the other — and that basket is in the seed precisely so this is exercised rather than assumed.

- **DONE WHEN**: sidecar's conformance suite passes against the real store, not just the fake; `reserve` under 50-way concurrency on 5 units yields exactly 5 successes **against real Postgres** (A3 runs the same shape against the conformance fake; the fake proves the sidecar's logic, this proves the `WHERE available >= qty` actually holds under Postgres' isolation level — both are required); a repeated idempotency key returns the identical response bytes; `quote` is byte-identical across repeat calls and contains **no date, only day counts**; both GST splits appear in one mixed basket; the inclusive and exclusive directions are both seeded and both tested (getting this backwards double-charges every order).

### B3 · Shop-ops admin — 1.5h *(3.0h less cut 4a)*

> **CUT 4a IS IN FORCE (§12).** Build: dashboard, catalogue create/edit + variant matrix, stock, price, inventory moves, orders + Timeline, dispatch + invoice, refund dialog, Record collection, Record RTO. **Do not build: CSV export, bulk archive, or the pricing-setup CRUD** — shipping zones, GST identity and discount codes are **seeded in B1 and edited by re-seeding**. Use a headless table/form primitive for the variant matrix; the design system supplies the styling. The claim this preserves is "a Merchant can operate it," so everything on the *operating* path stays and only the setup-once screens go.

**Access control first.** Session-authenticated (single operator, argon2id, CSRF on every mutation, bound to the private network). It holds the Refund button, which moves real money through the sidecar — an unauthenticated admin is a refund endpoint for anyone who can reach the container, and that is the one hole a demo must not ship.

Dashboard (open orders, `refund-requested`, low stock, `failed`/`sold-out` counts) · Catalogue (group CRUD + a **variant matrix** beneath it: generate combinations from the axes, then set price, stock, threshold, HSN/SAC, GST per item, leaving never-made combinations **absent rather than zero-stocked**; `active/archived` at both levels, no hard delete with orders; ~~CSV export~~ and ~~bulk archive~~ *(cut 4a)*) · ~~Pricing setup~~ *(**cut 4a — seeded instead.** Full scope kept for later: shipping zones, GST identity with a 15-character state-code + PAN + entity-shape check on the GSTIN, discount code table with `visibility` and `max_uses`, and a minimum-entropy check refusing guessable private codes at creation. **The entropy rule still applies to the seeded private code** — seeding is not a licence to seed `TEST1`.)* · Inventory (adjust with reason + actor, moves audit with channel filter, low-stock badges, restock) · Orders (searchable list; detail with **Timeline** = status history + Transcript ref + stock moves + receipt link + invoice number; a **dispatch action** available any time from `confirmed` onward, independent of status, recording optional tracking number + carrier, assigning the gapless financial-year `invoice_number` by atomic increment **on first dispatch only** — CGST Rule 46 ties the invoice to removal of goods, not to payment — and firing the shipped notification; shop-reject and mark-completed; a **Refund** button on `paid`/`completed` opening a dialog with reason + amount defaulting to the full remaining and capped at it + per-line `[x] return stock to sale` + notify toggle; and on a COD order at `confirmed`, two actions the prepaid path does not have: **Record collection** (→ `paid`, writing the `CAPTURE`) and **Record RTO** (→ `cancelled` with reason `rto` and a restock, writing no Ledger entry at all)).

**Refund, shop-reject, COD collection, and RTO all call the sidecar over HMAC and never write the order row directly.** The sidecar holds the `RESERVE`, so a local write strands a Ledger hold, and routing through it serializes shop-reject against a Consumer tap and the expiry sweep on one `set-status` key.

Explicitly not here: keys, provider secrets, policy, exposure, receipts — those are `/agentic`. Demo-admin is shop ops only, so the demo stays replaceable.

- **DONE WHEN**: create item → **set stock and price** (GST and zone come from the seed, cut 4a) → place test order → adjust → cancel → partial refund → full refund, with the audit showing every move and over-refund refused; one order gets exactly one gapless invoice number and two concurrent dispatch calls cannot double-assign; **a COD order records dispatch, a tracking number, and its invoice number while still `confirmed`, before `paid` exists at all**, then reaches `paid` by Record collection or `cancelled`+`rto` by Record RTO; status flip alone never moves money.

### B4 · Notifications + health — 0.5h

Order confirmation with receipt link, refund notice, shipped notice, new-order alert — written to `notifications`, rendered in admin, logged. The sidecar sends no email or SMS and never receives pushed PII: `order.changed` is consumed by the sidecar only, and the chat polls order status through its own closed action.

---

## 9. Surface C — Buyer chat

An **air-gapped stranger**. Own folder, own process, own deps, own SQLite. Zero imports from either other root. HTTP only. Admitted exactly like any external agent — by its own published Agent Profile, with Merchant-issued OAuth as the allowlisted alternative.

### C1 · Scaffold, tool loop, model seam — 3.5h *(identity and request signing moved to A4c)*

- Chat UI: threads, streaming text, tool-call cards that expand to the exact request JSON, permission modals, status widgets. Claude-shaped, in the design system, `--accent-2` primary.
- **Session DB holds threads and display only.** No carts, no money state, no Consumer PII. The cart lives in the sidecar; chat cart-ops are calls, not local state. Destination and Contact Point are collected in C3 and held **in memory for the length of that checkout only** — posted to the sidecar, never written to the DB, never logged, dropped at thread end. An agent that persists them has become a place PII leaks from, which is the thing the air-gap exists to prevent. **A planted write of either into the session DB fails the build** — a claim about not storing PII with no test behind it is just a sentence.
- Agent identity and RFC 9421 request signing are **built in A4c by Track P** and land in `demo/buyer-chat/src/lib/identity/`. C1 consumes that module and owns nothing about the crypto. The requirement it must still satisfy: a rebuilt container re-registers with the same key, because a signer with no key story is a demo that cannot survive a `docker compose down`.
- Model seam per D7: `plan(messages, tools) -> ToolCall[]` with `scripted` (default, CI), `ollama`, `anthropic` drivers.
- Closed action set, each mapped to the scope it needs: `search` + `read-item` (`search`); `add-line` / `remove-line` / `set-destination` / `set-contact` / `choose-fulfillment` / `apply-public-code` (`build-basket`); `start-checkout` (`start-checkout`); `place-order` (`confirm`, and **only ever by handing the Consumer an approve URL**); `order-status` + `request-refund` (reads, scoped — both resolve only against orders carrying **this agent's** `agent_id`, and someone else's order is indistinguishable from one that does not exist).
- Bounded steps. Malformed model output is rejected with a named error and **no state change**.

### C2 · Direct-add and contacts — 1.5h

- Paste a Merchant URL → fetch the sidecar card → show name / category / key / protocols → save as a contact.
- **A Consumer-pasted URL fetched server-side is the same SSRF sink, pointed the other way.** Same rules as the sidecar's profile fetcher: HTTPS only, public IPs only, loopback/RFC1918/link-local/metadata refused, resolve-then-pin, no cross-host redirects, size and timeout caps — including the same narrow dev-mode named-host allowlist, because an exception that exists on one side of the demo and not the other just moves the failure.
- **Merchant key trust is TOFU and pinned**: the card's JWKS is stored with the contact at add time; a later fetch presenting a *different* key for a known contact warns loudly and **blocks spend**. Silent key acceptance is how a hijacked domain gets paid. Rotation is normal though (ADR-0014), so verification is `kid`-aware: an unknown `kid` triggers exactly one JWKS refetch against the pinned domain, and only a key that fails to appear there is treated as a hijack.
- Admission is self-service: the chat publishes its Profile, signs its requests, and gets a token from a shop that has never heard of it — **no Merchant action first**. Consumer enrollment is not a step here; it happens inside the first approve ceremony, and the copy states one enrollment per shop.
- Forgetting a contact deletes the local bookmark only and revokes nothing server-side. Server revocation is separate and authoritative.
- **DONE WHEN**: unknown-URL, tampered-card, and dead-sidecar each fail with a named reason; a valid add completes against a shop with no prior knowledge of this chat using only the published Profile, **over compose with the dev allowlist set, and again against a real public HTTPS profile with it unset** — the second is the one that proves the mechanism rather than the exception, and it is not optional; a blocklisted profile is refused with the sidecar's own reason; nothing touches sidecar code.

### C3 · The shopping flow — 4.5h *(re-budgeted: eleven distinct UI states, a verbatim signed-quote renderer with a byte-equality assertion, and named copy for eleven reason codes is not a three-hour phase)*

Search → **permission modal showing the exact request JSON** with `Allow once / Always allow (reads only) / Decline` → result cards with filters → an option picker over the group's axes that **must resolve to one Catalogue Item before `ADD` is offered** (unresolved or group-level adds refuse `variant-required`; the chat never picks a size for the Consumer) → add-ons that attach to a **named parent line at the moment they are added** (an unparented one refuses `addon-without-parent`) → stock pills, which are **the only stock fact the chat is ever given** → one bag-yes modal for spend whose copy grants a **scope and never an amount** ("this shop may build a paid basket"), because no total exists yet and a modal that reads like an amount approval teaches the Consumer to click through the tap that is one → Destination + Contact Point typed by the Consumer, never invented by the model → fulfillment option picked from the Merchant's returned list → optional **public** code field, with private codes deliberately refused here and the card pointing at the approve page → **checkout card rendered verbatim from the Merchant-signed Quote**: items, discount line, shipping with ETA, GST lines, total, **the Merchant's enabled payment methods including Cash on delivery where enabled**, `Place order`, expiry countdown.

**The agent never sums, estimates, or re-labels a line.** Agent-side totals do not exist. Pre-display signature check on every Merchant-signed payload; a mismatch blocks `Place order` with `signature-invalid`.

Every refusal shows the sidecar's exact fix — except `code-invalid`, which says only that the code did not apply, because a chat that explains *why* is a code oracle with a friendly face. `price-changed` re-renders the new Quote and requires a fresh tap rather than quietly proceeding.

- **DONE WHEN**: the golden MCP transcript passes; edited-total, moved-quote, and expired-offer variants all block with codes; **the rendered breakdown is asserted byte-equal to the signed Quote, line for line**; standing approval never covers a spend step.

### C4 · Tap, pay, receipt, no context loss — 2h

`Place order` opens the approve page → fake-UPI approve by default, virtual passkey tap where the demo exercises that member → auto-return via the resume token → **sidecar restores the cart snapshot, chat restores its thread, equality asserted** → fake-UPI marked paid → signed webhook → receipt card with a verify link.

Status widgets for every ending, each naming door, reason, and next step: `cancelled` (pre-pay, free) · `failed` (`sold-out` or `amount-mismatch` — pick again) · `expired`, which has **two faces and two messages**: the 24h Quote-validity sweep ("re-add, don't re-search" — nothing was held, nothing was lost) and the payment-window sweep ("your hold was released, tap again") · `refunded` (with `Refunded ₹X of ₹Y`) · `completed`. On a COD order the endings differ and the copy must too: `confirmed` reads "your order is on its way, pay the courier ₹X", `paid` reads "cash collected", and an RTO reads "returned undelivered — nothing was charged", because nothing was.

### C5 · Protocol toggle and smoothness gates — 1h

Header toggle per §A6 with the conformance badge. And the smoothness law as **tests, not aspirations** (`SPEC.md §11`): golden replays assert **one Allow-once and one tap per spend**, zero re-search, zero re-login, and exact resumed state on both sides. Context loss is a red build.

---

## 10. Cross-cutting

### Compose

```
caddy       :80     two site blocks — spoiledduckie.localhost AND chat.localhost
store       :3000   SvelteKit + adapter-node
sidecar     :8000   FastAPI
postgres    :5432   two databases, two roles, no cross-grant
buyer-chat  :3001   SvelteKit + SQLite volume
```

Caddy needs **both** site blocks. `chat.localhost` with nothing serving it is a browser error page where the third surface should be.

`buyer-chat` can reach the sidecar's public doors and nothing else. A test asserts it cannot reach Postgres.

### 10.1 Two addresses for one shop — read this before writing any fetch

This is the single most likely way to lose an afternoon, so it is stated once, here, and everything else refers back to it.

**`spoiledduckie.localhost` is a browser-side name.** Chrome and Firefox resolve `*.localhost` to loopback. **A container does not.** Inside the `buyer-chat` container, `spoiledduckie.localhost` resolves to that container's own loopback, where nothing is listening. So:

| Caller | Target | Address to use |
|---|---|---|
| Browser → shop / console / approve page / receipt | any | `http://spoiledduckie.localhost/...` |
| Browser → chat | chat | `http://chat.localhost/...` |
| buyer-chat **server** → sidecar (card fetch, MCP, `/agent/*`) | sidecar | `http://caddy/...` with `Host: spoiledduckie.localhost` |
| sidecar → buyer-chat's Agent Profile | chat | `http://buyer-chat:3001/.well-known/agent-profile.json` |
| sidecar → merchant site (the 9 doors) | store | `http://store:3000/trait/...` |

The `Host` header on the chat→sidecar call matters: the sidecar issues tap tokens and passkey challenges bound to the Merchant **domain**, and a card fetched as `http://caddy/` would advertise the wrong origin back to the browser. The chat sends the real domain in `Host` and Caddy routes on it.

**The SSRF exception must cover the scheme, not only the host.** The hardened fetcher is HTTPS-only and public-IP-only; both of those refuse `http://buyer-chat:3001`. So `OPENSTORE_DEV_PROFILE_HOSTS` admits `host[:port]` entries **and permits `http` for exactly those entries** — otherwise the demo's own self-registration is refused by its own hardening, which is a genuinely confusing hour to spend. Everything else about the exception is unchanged and still fenced: named hosts only and never a CIDR, `169.254.169.254` refused unconditionally even here, resolve-then-pin and the redirect/size/timeout caps still applied, every use logged and shown in the `/agentic` banner, and boot refused when the allowlist is non-empty alongside live provider keys.

**The proof that the mechanism works, not just the exception:** C2's DONE WHEN requires one registration against a real public HTTPS profile with the allowlist **unset**. The compose path proves the demo runs; that one proves the design does.

### Environment

`.env.example` is written in A1 and is the only place a variable is introduced. Local `.env` is never committed. Demo keys are test-marked; the sidecar refuses live keys at boot in demo mode, and refuses to boot at all if the dev profile allowlist is non-empty alongside live keys.

### CI

Every push: firewall check · `docs/CODES.md` diff · money lint · time lint · pytest (sidecar + goldens) · `vitest` (both SvelteKit roots) · `svelte-check` · the golden protocol replays with the core Transcript byte-compared · the planted-violation tests. Any red is a stop.

---

## 11. Test and verification gates

The tests that are not optional, because each one is a claim the pitch makes out loud:

| Claim in the pitch | The test that earns it |
|---|---|
| "No agent holds spending authority" | `confirm` without a fresh accepted Authority is refused on every admission route |
| "The Consumer authorizes the delivered total" | `cart_hash` golden covers `fulfillment_option_id` and `total_minor`; a Destination edit after render invalidates the token |
| "We never oversell" | 50 concurrent taps on 5 units → exactly 5 `confirmed`, stock never negative |
| "The Merchant's arithmetic is checked, not trusted" | a deliberately wrong Quote total refuses `quote-inconsistent` |
| "Price moves fail closed" | a quote that moves between pin and capture fails `price-changed` and moves no money |
| "Money that has left is always recorded" | a `REVERSAL` after a full refund lands net-negative and alerts rather than refusing |
| "The receipt is tamper-evident" | one flipped byte → exit 1 naming the exact link |
| "Erasure does not break verification" | an erased order's bundle verifies and renders `unopened` |
| "Any agent can self-register" | a stranger transacts end to end with no prior Merchant action |
| "...and that door is not an SSRF hole" | loopback, RFC1918, and `169.254.169.254` each refused before any fetch, metadata refused even with the dev allowlist set |
| "Exact stock is never leaked" | no agent-facing payload in the suite carries an integer count except a waited-on quantity refusal |
| "One core, many envelopes" | core Transcript byte-identical across MCP and UCP replays |
| "One tap, no context loss" | golden replay asserts one Allow-once, one tap, exact resumed state |
| "Stock and money are separate facts" | a COD order holds stock with an empty Ledger, and escrow-zero still closes |
| "COD ships against evidence, not a phone number" | an OTP cannot be configured as a `confirmed-intent` mechanism; the enum refuses it |
| "An RTO costs the Consumer nothing" | RTO writes no Ledger entry, restocks every line, and lands `cancelled` + `rto` |
| "The invoice follows the goods, not the payment" | a COD order carries a gapless invoice number while still `confirmed` |
| "Erasure really is erasure" | `order_salt` appears in no sidecar table and no log after a completed order (§6.3a) |
| "Partial refunds actually write" | two successive partials on one order both land — the `main` trap in §3 |
| "Signed evidence contains no false statements" | a `deferred` check that `settle()` never resolved fails verification |
| "Self-registration is a mechanism, not a demo fixture" | one registration against a real public HTTPS profile with the dev allowlist **unset** |

---

## 12. The 48-hour schedule

### Staffing — stated, because the plan is worthless without it

**This plan assumes two builders working in parallel, roughly 13 hours a day for two days**, plus whoever is reviewing. Track P is Python/sidecar; Track S is SvelteKit/surfaces. They are genuinely independent — the firewall guarantees it — and they meet at three integration points.

**One builder cannot do this in 48 hours.** If you are solo, take the hour-0 configuration below *and* cuts 1–3, before starting, not at hour 30 when they are discovered.

### The reuse asymmetry — why Track S is the heavy track

The hour columns hide this and it is the most important fact about the schedule. **`main` has no `package.json` and no `node_modules` at all** — its entire UI is Jinja templates plus three vanilla JS files served from FastAPI.

So: **Track P is a port.** It inherits ~7,000 lines of debugged Python — the Gate skeleton, the ledger, WebAuthn RP, OAuth, the Razorpay driver, the offline verifier — and can crib `/agentic` (A7) from main's existing `policy_studio.html`, `orders_admin.html` and `evidence_viewer.html`.

**Track S is a build.** Both SvelteKit apps are greenfield; it inherits nothing from `main` except ideas. Its only reuse is the portfolio design system.

Equal hours, unequal risk. Plan accordingly, and when something slips, expect it to slip here.

### Track S also gates every integration point

Track P's A3 is the longest single block, but Track S sets every gate time: Integration 1 needs B2 (the doors), Integration 2 needs C2 (self-registration), Integration 3 needs C4 (tap → receipt). Track P can finish A8 with margin while Track S is still what everyone is waiting on. That asymmetry is why A4c exists and why the third builder, if one appears, goes to Surface C.

### The arithmetic, so the margin is visible rather than implied

Phase hours, plus the three integration windows and the closing pass, against 13-hour days. The integration windows are counted here because they are real hours where neither track is building.

| Track P (sidecar) | h | | Track S (surfaces, Option B) | h |
|---|---|---|---|---|
| Hour 0 contracts (shared) | 1.0 | | Hour 0 contracts (shared) | 1.0 |
| A1 skeleton + harness | 1.5 | | `design/` import + both app scaffolds | 1.5 |
| A2 trait client + fake | 2.0 | | B1 schema + seed | 2.0 |
| A3 Gate + Ledger + provider | 4.0 | | B1 storefront *(cut 5 applied)* | 2.5 |
| A4 Authority + orders (incl. COD) | 4.5 | | B2 the nine doors | 3.0 |
| **A4c agent signing kit** | **1.5** | | B3 admin *(cut 4a applied)* | 1.5 |
| A5 evidence + verifier | 2.0 | | B4 notifications + health | 0.5 |
| A6 protocols (**all four**) | 4.0 | | C1 chat scaffold + tool loop | 3.5 |
| A7 `/agentic` console *(cut #2 applied)* | 1.0 | | C2 direct-add + TOFU | 1.5 |
| A8 mount + install + feed | 1.5 | | C3 shopping flow | 4.5 |
| C5 toggle + smoothness gates | 1.0 | | C4 tap + pay + receipt | 2.0 |
| **Phase subtotal** | **24.0** | | **Phase subtotal** | **23.5** |
| 3 × integration window | 1.5 | | 3 × integration window | 1.5 |
| Rehearsal + `make up` | 1.5 | | Polish pass | 1.5 |
| **Total** | **27.0** | | **Total** | **26.5** |
| Available (2 × 13h) | 26.0 | | Available (2 × 13h) | 26.0 |
| **Margin** | **−1.0h** | | **Margin** | **−0.5h** |

Track P is **an hour over** (it absorbed D3's four-protocol decision) and Track S **half an hour over**. The only compressible things left are the closing passes: Track P's rehearsal block and Track S's polish. Track P also carries A6's automatic escape — an unfetchable spec downgrades that protocol and returns 2h without a decision being needed. That is a tight plan, not a comfortable one, and it is the honest number rather than a flattering one. **Without cuts 4a and 5, Track S is 26.5 + 3.0 = 29.5 against 26.0 — three and a half hours over.** Which is why the configuration below is decided at hour 0 and not discovered at hour 22.

### The configuration — SETTLED: **Option B, two builders, no third**

**Decided 2026-09-20. Cuts 5 and 4a are in force from hour 0.** This is not a choice left to whoever executes the plan — it is made, and the day tables, the phase budgets and the §8 scope notes all reflect it. Do not re-open it; if circumstances change, change it here and in `LOGS.md` first, deliberately, and re-budget §12 before touching code.

Option A is recorded below as the path **not** taken, so that if a third pair of hands appears mid-build the restoration is mechanical rather than a redesign.

**Option A — a third builder takes Surface C** *(not taken)*. B and C share no state, no code and no database; the firewall already guarantees the seam. Surface C is **11.5h** and lifts out cleanly, taking C1, C2, C3 and C4 with it. Track S then runs Surface B alone at **18.0h against 26.0 (margin +8.0h)** and the third track carries C at **13.0h**; cuts 4a and 5 are **not needed**, and the direct storefront checkout and the full admin both survive. **If there is any third person available, this is the move** — it is the only option that costs the demo nothing, and it converts a −0.5h squeeze into a wide margin on every track.

**Option B — two builders, two cuts taken now** *(SELECTED — what everything below is written for)*:
- **Cut 5** — no direct storefront checkout; browse + buy-via-agent only *(−1.5h from B1)*. The cheapest cut in the plan: that path deliberately takes no money anyway, so almost nothing is lost on stage.
- **Cut 4a** — admin **trimmed, not gutted** *(−1.5h from B3)*: keep create/edit, the variant matrix, stock, price, dispatch and refund; drop CSV export, bulk archive and the pricing-setup CRUD, seeding zones, GST identity and codes instead. This keeps "a Merchant can operate it," which is Surface 2's entire reason to exist — **do not substitute the blunter read-only-admin cut**, which trades that claim away for the same ninety minutes.

### Hour 0 — both tracks, together (1h)

Freeze §6 and commit it. **Nothing else starts until this is committed** — these are the last things that change cheaply, and everything downstream pins their bytes.

Do it in this order; each step needs the one before it:

1. **Repo skeleton.** `src/openstore/sidecar/{core,trait,gate,ledger,authority,admission,provider,evidence,protocols,console,verify}/` with `__init__.py`, plus `tests/`, `scripts/`, `design/`, `lists/`. Copy `pyproject.toml` from `main` and change the package path; `uv sync`. No application code — empty packages so step 2 has somewhere to live.
2. **`src/openstore/sidecar/core/codes.py`** — every closed set in §6.4 as a Python enum, **plus the reason-code → HTTP-status table** (§6.1). This file is written **here, at hour 0, and not in A1.** A1 consumes it and builds the lint harness around it.
3. **`scripts/registry_diff.py`** (port from `main`) → generate `docs/CODES.md` from the enums.
4. **`tests/GOLDEN/cart_hash/vectors.json`** — the §6.3 preimage, three cases: minimal single line; the §16.5 destination-B mixed basket; one with a discount line. Committed as bytes, compared by CI forever.
5. **Door and Quote schemas** — §6.1 and §6.2 as JSON Schema in `src/openstore/sidecar/trait/schema/`. Shapes only, no implementation.
6. **`.env.example`** per §16.1, every variable named, no values.
7. **The D1 amendment** — one line each in `SPEC.md §1` and `PLAN-merchant-site.md`, Next.js → SvelteKit. A contracts commit is where spec edits belong.

Commit as `feat(h0): freeze the contracts`. **Track S does not wait for this** — the `design/` import and app scaffolds (its hour 1–2.5 slot) depend on none of it, so if hour 0 runs long, Track S starts anyway.

### Day 1

Written for **Option B** (two builders, cuts 5 and 4a taken at hour 0). Under Option A, restore both and move the C rows to the third track.

| Hour | Track P (sidecar) | Track S (surfaces) |
|---|---|---|
| 1–2.5 | A1 skeleton + guardrail harness | `design/` import; both SvelteKit apps scaffolded; tokens verified in light **and** dark before any component is written |
| 2.5–4.5 | A2 trait client + conformance fake | B1 schema + migrations + seed (the 16-item table) |
| 4.5–7 | **A3 Gate + Ledger + provider** *(the long one — if it is not green by 8.5, take cut #3 now)* | B1 storefront: home, shop, group page + picker, lookup, JSON-LD *(no direct checkout — cut 5)* |
| 7–8.5 | A3 continues | B2 the nine doors starts |
| 8.5–10 | A4 pt 1: kinds, `upi-pin`, admission, SSRF hardening | B2 continues *(done by 10.0 — Integration 1 depends on it)* |
| 10–11.5 | A4 pt 1 continues | B3 admin, trimmed: dashboard, variant matrix, stock, orders + Timeline, dispatch, refund dialog, Record collection / Record RTO |
| **11.5–12** | **★ Integration 1** *(both tracks, 30 min — commands below)* | |
| 12–14 | A4 pt 2: `passkey`, `confirmed-intent` + COD lifecycle, approve page, resume tokens | B4 notifications + health, then C1 starts |

*End of day 1 (~hour 14): a basket can be priced, gated, reserved and paid on fake money from a script, on both the prepaid and the COD path, and a Merchant can operate the shop. No chat yet — the chat is downstream of all of it.*

### Day 2

| Hour | Track P | Track S |
|---|---|---|
| 14–15.5 | **A4c agent signing kit** *(files land in `demo/buyer-chat/`; same sitting as A4's verifier)* | C1 continues: chat UI, session DB, tool loop, model seam |
| 15.5–17.5 | A5 evidence + verifier | C1 finishes *(consumes A4c's identity module)*, then C2 starts |
| 17.5–21 | **A6 protocols — MCP, UCP, ACP + AP2 layer + badge** *(step 0 is done; specs pinned in §16.12 — read it first)* | C2 finishes by 17.5 → **C3 shopping flow** starts |
| **21–21.5** | **★ Integration 2** *(both tracks, 30 min — needs C2 green, which it is)* | |
| 21.5–22.5 | A7 `/agentic` console *(trimmed — cut #2 taken up front)* | C3 continues |
| 22.5–24 | A8 mount + install + feed | C3 finishes → C4 starts |
| 24–25 | C5 protocol toggle *(now four tabs)* + smoothness gates | C4 tap + pay + receipt |
| **25–25.5** | **★ Integration 3** *(both tracks, 30 min)* | |
| 25.5–26 | `make up` from a clean checkout; rehearse §13 | Polish: empty and error states, dark mode, mobile, the receipt's print view |

The last row is where both overruns live: polish is budgeted 1.5h and gets 0.5h, rehearsal 1.5h and gets 0.5h. **Polish is the buffer. The rehearsal is not** — present an unrehearsed demo and the whole plan was wasted, while plain empty states go unnoticed. If Track P is behind at Integration 3, take cut #1 (ACP and AP2 back to declared-and-refused) rather than eating the rehearsal; it is a clean reversal and the badge still tells the truth.

**A4c is scheduled first on day 2 for a reason**: C1 consumes it, so it has to exist before Track S needs it, and A4's verifier is still fresh in the writer's head from the previous evening.

### The three integration points, as commands

Each is a hard gate. If it does not pass, the tracks do not move on — they fix it together. Half an hour is budgeted for each; if one runs long it comes out of polish, never out of a DONE WHEN gate.

**★ Integration 1 — the sidecar talks to the real store** (~hour 11.5)

```
make up
uv run pytest tests/trait_conformance -k real_store   # A2's suite, pointed at Postgres not the fake
uv run python scripts/smoke_quote.py                  # SD-TOTE-BLK-M + SD-CHARMBAR-SEAT → destination B (§16.5)
```
Expect: conformance green against the real store; the quote shows **CGST/SGST on one line and IGST on the other**; `reserve` under 50-way concurrency on 5 units yields exactly 5; two identical `quote` calls are byte-identical and contain **no date**.

**★ Integration 2 — the stranger is admitted** (~hour 20.5)

```
make up
uv run python scripts/smoke_register.py               # chat's Profile → sidecar, no prior Merchant action
```
Expect: a new row in `/agentic` → Agents with the chat's RFC 7638 thumbprint as `agent_id`; a search returns result cards; **no exact stock integer anywhere in the response**; the dev-allowlist banner is visible in `/agentic` because the exception is in use.

**★ Integration 3 — the whole thing** (~hour 25)

```
make demo                                             # drives §13 beats 1–6 headless
uv run openstore verify out/bundle.json               # expect exit 0
uv run openstore verify out/bundle_tampered.json      # expect exit 1, naming the link
```
Expect: exit 0 then exit 1; the receipt prints the Authority kind, its mechanism, and its Binding; the chat thread resumes with **one** Allow-once and **one** tap recorded.

### If you are behind — cut in exactly this order

Decide at an integration point, never mid-phase. Each cut names what it costs on stage, because that is the thing you are actually trading.

**Cuts 4a and 5 are taken at hour 0 under Option B; cut 2 is taken up front to fund A6's four protocols (D3).** None of the three is available again. What remains, in order:

1. **ACP and AP2** → back to declared-and-refused, MCP + UCP live *(−2h Track P; costs the four-envelope beat, but the badge still tells the truth — this is a clean reversal, not damage)*
1b. **UCP too** → MCP only *(−1h more; take only if A6 is genuinely drowning, since it costs "one core, many envelopes" entirely)*
3. **`passkey` Authority** → `upi-pin` + `confirmed-intent` only *(−1.5h Track P; costs the biometric moment, keeps COD and keeps A4c, which the chat still needs)*
4. **Admin down to read-only** — the blunt version of 4a, if trimming was not enough *(−1.5h more from Track S; costs "a Merchant can operate it," which is Surface 2's whole purpose — this is the first cut that takes a claim away rather than a convenience)*
5. **C3's non-essential UI states** — keep the picker, the verbatim quote card, the signature check and the eleven reason codes; drop filters and the add-on flow *(−1h Track S; the thesis survives, the polish does not)*
6. **COD** → prepaid only, `cash-on-delivery` refuses `method-not-supported` *(−2h; last, because it is the beat that separates this from a card-shaped product and the Ledger work it demonstrates is already done by then)*

Note that cuts 1–3 are all Track P, which has the margin — so the first three cuts buy Track P slack it can spend helping Track S, rather than buying time on the track that is short. That is deliberate: **a cut on the track with margin is worth less than the same cut on the track without it.** If Track S is the one behind at Integration 2, take cut 4 or 5 and move a Track P builder onto C3, in that order.

**Never cut, in any order, for any reason:** the Gate's check order, `quote-fresh`, the Ledger invariants, the sealed receipt, the offline verifier, the approve ceremony, the import firewall, or the closed sets. Those are the product. Everything above them is the demonstration of it — and a demonstration of nothing is worse than a smaller demonstration of something.

---

## 13. The demo, six minutes

1. **`spoiledduckie.localhost`** — a real shop. Scroll the home page. Open the tote. Pick black, size M; watch the price and the stock pill resolve to *that* variant. Note the JSON-LD in view-source. *(60s)*
2. **`chat.localhost`** — a chat that has never heard of this shop. Paste the URL. It fetches the card, pins the key, publishes its own Profile, and is admitted with no Merchant action. Show the `/agentic` Agents tab gaining a row, live. *(60s)*
3. **Shop by conversation.** "Find me a black tote and gift-wrap it." Permission modal shows the exact JSON — `Allow once`. Result cards. Picker resolves the variant. Gift-wrap attaches to the tote line. Add the charm-bar seat too — *this basket now carries two places of supply*. *(90s)*
4. **Address and quote.** Type destination **A** (Bengaluru, 560038), then destination **B** (Mumbai, 400028) — both in §16.5; watch CGST/SGST become IGST on the tote while the charm-bar seat stays CGST/SGST, because a service is taxed where it is performed. The chat renders the Merchant's signed Quote verbatim and sums nothing. Countdown starts. *(60s)*
5. **The tap.** `Place order` → same-domain approve page → enter the private code `DUCK-7F3K-9QWX` (§16.6) *here, never in the chat* → total re-renders and rebinds → approve the fake UPI collect → auto-return with the thread exactly as it was. Receipt. *(60s)*
6. **The proof.** `openstore verify bundle.json` → green, printing the Authority kind and its Binding rather than an unqualified "verified." Flip one byte → red, naming the exact link. Then open admin, refund half, and show v2 appended while v1 stays verifiable. *(60s)*

7. **Cash on delivery**, which is ~60% of Indian ecommerce and the thing every competitor's demo quietly omits. Same basket, choose COD: the sidecar calls no provider, writes no Ledger entry, and holds the stock anyway. Dispatch it from admin — a gapless invoice number lands while the order is still `confirmed`, before `paid` exists — then Record collection and watch a `CAPTURE` appear with no `RESERVE` in front of it. Say out loud that **the Merchant asserts the cash and nothing here can check it**, that this is correct because it is their money and their Ledger, and that what `confirmed-intent` bought them is evidence the basket was committed to by someone holding a real funding instrument, not a phone number. *(75s)*

7b. **Four envelopes, one core.** Flip the header toggle through `[MCP | UCP | ACP | AP2]` and replay the same basket. The conformance badge changes with each, naming what that protocol wanted and what this Merchant refuses: **ACP** wanted a delegated payment token at `/complete` — refused, here is the approve URL instead, and its other four operations work fine; **AP2** rides on UCP as a security layer, its human-present mandates verify against our Quote, and its autonomous mode refuses by name. Worth pointing out that AP2's `checkout_hash` and our `cart_hash` are structurally the same primitive, arrived at independently. Then show the CI assertion that the **core Transcript bytes are identical across all four**. *(60s — this is the architecture beat. If the room remembers one thing, make it this.)*

**If there is an eighth minute**, show a refusal: `SD-PLUSH-MINI` already carries a 2-per-order cap (§16.4), so try for three and watch `cap-exceeded` fire — evaluated at the Product Group, so taking one of each variant cannot walk around it either. That is the whole thesis in one refusal.

**If someone asks about RTO** — and in an Indian room someone will — hit Record RTO instead: `cancelled`, reason `rto`, every line restocked, and an empty Ledger, because nothing ever moved.

---

## 14. Risks, named

| Risk | Shape | Mitigation |
|---|---|---|
| **A3 overruns** | The Gate + Ledger + provider block is 4h and is the only true critical path; everything on Track P is downstream of it | Start it at hour 4.5 with the fake, not the real store. If it is not done by hour 9, take cut #3 immediately rather than at hour 21. |
| **WebAuthn attestation** | Platform authenticators return `none`, and the enroll-and-tap single-prompt claim depends on getting a signed `attStmt` | The two-prompt fallback is specced and ships by default. The Transcript records which ceremony ran, so the demo tells the truth either way. |
| **`quote` determinism** | Any clock leaking into door 9 turns midnight into a spurious `price-changed` | Day counts, never dates. A test freezes the clock across a day boundary and byte-compares. |
| **Tax-inclusive inversion** | Getting the flag backwards double-charges every order and looks fine until someone adds it up | Both directions seeded, both tested, and `quote-consistent` checks it independently in the Gate. |
| **The firewall erodes under time pressure** | At hour 20 a shared type looks very reasonable | The check is a build-breaker from hour 1.5, with planted violations in CI. It is easier to keep than to restore. |
| **Design drift across three surfaces** | Three apps, one weekend, three slightly different greys | One `design/tokens.css`, copied not forked. No component references a literal colour, and a lint rule says so. |
| **COD looks free and isn't quite** | The cheap part is the money path; the cost is spread thin across Authority, admin actions, the third deadline, and copy on three surfaces | It is budgeted at 2h across both tracks and it is cut #6 — last, because by hour 21 the Ledger work it proves is already done and only the surface actions remain. |
| **PP Kyoto licensing** | Commercial font in a demo that might get recorded | Flagged in §4. Substitute before anything public. |
| **Ollama has no model pulled** | A 5GB download mid-demo | `scripted` is the default driver and the one CI runs. Ollama is opt-in. |

---

## 15. What this MVP deliberately does not do

Beyond the D-decisions in §2, and unchanged from `SPEC.md §13`: campaigns, rules engines, bots, hosted mall/search/ranking, multi-Merchant tenancy, multi-location, cancellation and restocking fees, store credit, bulk cancel, auto-fulfilment, shipping labels, subscriptions, multi-currency and duties, Consumer accounts, a self-serve returns portal, fraud scoring, abandoned-cart recovery, Merkle batch-anchoring, OMS write paths beyond the 9-door trait, and preorder/backorder (which is not a cut but a consequence: stock is `int ≥ 0` with a `CHECK`, and selling against future stock needs a second inventory model, not a flag).

And one exclusion that is the product rather than a gap: **delegated agent-held payment credentials** — Shop Pay tokens, ACP Shared Payment Tokens and `vt_…` vault tokens — are refused on purpose. They are exactly the authority we removed from agents. It costs us native in-agent completion on the gated surfaces and we pay it. Say so on stage, with the reason, before anyone asks.

Distribution — the permissionless doors that make any of this reachable — is phase two and is specced in `PLAN-distribution.md`. It starts after the install gate, not before. Reach earned before install is reach wasted.

---

## 16. Pinned values — every constant the build needs

**Nothing in this section is a suggestion.** It exists so that an unattended run never has to invent a price, a port, a tax rate or a timeout. If a value is here, use it exactly; if you need one that is not here, take ladder step 4 in §0 and log it.

All money is **paise** (integer). All time is **UTC**. Currency is **INR** everywhere; there is no second currency and no conversion anywhere in v1.

### 16.1 Infrastructure

| Thing | Value |
|---|---|
| Browser origin, shop | `http://spoiledduckie.localhost` |
| Browser origin, chat | `http://chat.localhost` |
| WebAuthn RP ID | `spoiledduckie.localhost` |
| Caddy | `:80`, two site blocks |
| store | `:3000`, SvelteKit + `adapter-node` |
| sidecar | `:8000`, FastAPI + uvicorn |
| buyer-chat | `:3001`, SvelteKit + `adapter-node` |
| postgres | `:5432`, image `postgres:17-alpine` |
| Merchant DB / role | database `spoiledduckie`, role `sd_app` |
| Sidecar DB / role | database `sidecar`, role `sc_app` |
| Cross-grant | **none** — a test asserts `sc_app` cannot `SELECT` any `spoiledduckie` table |
| Chat DB | SQLite at `/data/chat.db` in its own volume |
| Python | 3.12 (`requires-python = ">=3.12,<3.13"`, as `main`) |
| Node | 26.x (the version on this machine — pin it in `.nvmrc` and `engines`) |
| Package managers | `uv` for Python, `pnpm` for both SvelteKit roots |

**Dependency versions:** do not invent version numbers. At hour 0, `pnpm add` / `uv add` each dependency, **commit the lockfiles**, and never upgrade mid-build. The lockfile is the pin. This applies to `http-message-signatures` (A4c) and to the headless table primitive (B3).

**Secrets** are generated by `scripts/openstore_up.py` into `.env` at first boot — never hand-written, never committed. Demo admin login is seeded as `operator@spoiledduckie.test` with the password written to `.env` as `ADMIN_SEED_PASSWORD` and printed once by the install script. All 128-bit values (`order_salt`, `receipt_id`, tap tokens, resume tokens) come from `secrets.token_bytes(16)` / `crypto.randomBytes(16)` — never from `random`, never from a timestamp.

### 16.2 Merchant tax identity

| Field | Value |
|---|---|
| Legal name | SpoiledDuckie Accessories |
| GSTIN | `29AABCS1429B1ZQ` — **fabricated for the demo**, correct in shape (state `29` + PAN + entity `1` + `Z` + check char) and belonging to nobody. Never present it as real. |
| Registered state | **Karnataka (29)** — this is the "home state" every intra/inter decision compares against |
| Price display | **tax-inclusive** (`tax_inclusive: true`) — Indian MRP convention, so seeded prices already contain GST and tax lines are `informational: true` |
| Invoice series | `SD/2026-27/0001`, incrementing, gapless, financial year **1 April – 31 March** |

### 16.3 The seed catalogue — 12 groups, **15** Catalogue Items

**The table below has 16 rows and you seed 15 of them.** `SD-TOTE-BLK-L` is the deliberately-absent combination and **must not be inserted at all** — not with zero stock, not with a null price, not as an archived row. It appears here only so nobody "helpfully" fills the gap. Seeding it breaks two gates at once: "zero null-stock rows" and "the unavailable combination renders as unavailable".

Prices are MRP (GST inside). Three distinct GST rates are deliberate: they make the apportionment and largest-remainder rounding rules actually exercise.

| SKU | Group | Options | Price | HSN/SAC | GST | Stock | Low-stock |
|---|---|---|---|---|---|---|---|
| `SD-TOTE-BLK-M` | Tote | black / M | ₹899 (89900) | 4202 | 18% | 12 | 3 |
| ~~`SD-TOTE-BLK-L`~~ | — | **DO NOT SEED** — black/L was never made | — | — | — | — | — |
| `SD-TOTE-RED-M` | Tote | red / M | ₹899 (89900) | 4202 | 18% | 7 | 3 |
| `SD-TOTE-RED-L` | Tote | red / L | ₹999 (99900) | 4202 | 18% | 4 | 3 |
| `SD-CAP-S` | Cap | S | ₹649 (64900) | 6505 | 12% | 9 | 3 |
| `SD-CAP-M` | Cap | M | ₹649 (64900) | 6505 | 12% | 2 | 3 |
| `SD-STICKERS` | Sticker pack | — | ₹199 (19900) | 4911 | 18% | 40 | 5 |
| `SD-KEYCHAIN` | Keychain | — | ₹299 (29900) | 8308 | 18% | 25 | 5 |
| `SD-HAIRCLIPS` | Hair clips | — | ₹349 (34900) | 9615 | 18% | 18 | 3 |
| `SD-PHONECHARM` | Phone charm | — | ₹449 (44900) | 7117 | 3% | 15 | 3 |
| `SD-PINSET` | Pin set | — | ₹399 (39900) | 7117 | 3% | 11 | 3 |
| `SD-PLUSH-MINI` | Plush mini | — | ₹1,299 (129900) | 9503 | 12% | 10 | 2 |
| `SD-CHARMBAR-SEAT` | Charm-bar seat | — | ₹1,500 (150000) | SAC 999799 | 18% | 10 | 2 |
| `SD-GIFTWRAP` | Gift-wrap | — | ₹99 (9900) | *(inherits parent)* | *(inherits)* | 100 | 10 |
| `SD-EXTRACHARM` | Extra charm | — | ₹149 (14900) | *(inherits parent)* | *(inherits)* | 60 | 10 |
| `SD-RECALLED` | Recalled item | — | ₹499 (49900) | 4202 | 18% | 5 | 3 |

**Tags:** `SD-PLUSH-MINI` carries `limited`. `SD-RECALLED` carries `recalled`. `SD-GIFTWRAP` and `SD-EXTRACHARM` carry `addon`. `SD-CHARMBAR-SEAT` carries `service`.

**`SD-CHARMBAR-SEAT` is a service and its Place of Supply is always Karnataka (29)** — where it is performed — regardless of the Destination. That is the line that makes a two-place-of-supply basket possible, and the demo depends on it.

**Add-ons** (`SD-GIFTWRAP`, `SD-EXTRACHARM`) never stand alone: they fold into a named parent line and inherit its rate, HSN/SAC and Place of Supply. Gift-wrap on the tote is taxed as goods at 18%; gift-wrap on the charm-bar seat is taxed as that service. An orphan refuses `addon-without-parent`.

### 16.4 Policy (seeded into `/agentic`)

| Setting | Value |
|---|---|
| Per-order cap | ₹25,000 (2500000) |
| Per-order line count | 10 |
| Per-Product-Group qty | 5, except `SD-PLUSH-MINI` at **2** |
| Blocked tags | `recalled` |
| Enabled payment methods | `upi`, `cash-on-delivery` |
| Enabled Authority kinds | `upi-pin`, `passkey`, `confirmed-intent` |
| Enabled `confirmed-intent` mechanisms | `upi-verify`, `passkey` |
| Exposure | all policies public to agents |

### 16.5 Shipping zones and test destinations

| Zone | Matches | Cost | ETA |
|---|---|---|---|
| Karnataka (intra-state) | state `KA` | ₹49 (4900) **incl. GST** | 2 days |
| Rest of India (inter-state) | any other Indian state | ₹99 (9900) **incl. GST** | 5 days |
| Unserviceable | postal codes `19xxxx` | — | refuses `destination-unserviceable` |

ETAs are **day counts, never dates** — a date in a Quote turns midnight into a spurious `price-changed`.

**The two demo destinations**, which every golden vector and the demo script use:

- **A — intra-state (CGST/SGST):** `4th Cross, Indiranagar, Bengaluru, Karnataka, 560038`
- **B — inter-state (IGST):** `Dadar West, Mumbai, Maharashtra, 400028`
- **Contact Point:** `+91 90000 00001`, `demo@spoiledduckie.test`

A basket of `SD-TOTE-BLK-M` + `SD-CHARMBAR-SEAT` shipped to **B** carries IGST on the tote and CGST/SGST on the seat, in one Quote. That is the §13 beat-4 moment and the Integration-1 smoke test.

### 16.6 Discount codes

| Code | Visibility | Value | Max uses |
|---|---|---|---|
| `SPOILED10` | public | −₹100 (−10000) **off the inclusive total** | 50 |
| `DUCK-7F3K-9QWX` | private | −₹250 (−25000) **off the inclusive total** | 1 |

The private code is high-entropy on purpose: B3's minimum-entropy check refuses guessable private codes at creation, and **seeding is not a licence to seed `TEST1`.** The public code may be entered in the chat; the private code is refused there and accepted only on the approve page, where it re-quotes and rebinds before anything is signed.

### 16.7 Timings

| Clock | Value | Note |
|---|---|---|
| `pending` order (Quote validity) | 24h | `time-limit-reached`, no stock held, nothing to return |
| `confirmed` payment window (prepaid) | 15 min | `payment-window-elapsed` + `RELEASE`; never longer than the provider link's own lifetime |
| COD delivery window | 7 days | `delivery-window-elapsed` — **alerts the Merchant, never auto-cancels** |
| Approve/tap token | 5 min, single-use | |
| Resume token | 30 min, single-use, session-bound | |
| Agent access token | 15 min | |
| Trait HMAC replay window | 60s + nonce | |
| RFC 9421 signature window | 60s + nonce | |
| Expiry sweeper interval | 60s | |
| Provider reconciler poll | 5 min | |
| WebAuthn challenge TTL | 120s | as `main` |

### 16.8 Rate limits

| Surface | Self-registered | Allowlisted |
|---|---|---|
| `/agent/*` per agent | 30 req/min | 300 req/min |
| `/agent/*` per IP | 120 req/min | 120 req/min |
| Profile registration per IP | 5/hour | — |
| Tap-token issuance per session | 5/min | 5/min |
| Approve attempts per order | 10/hour | 10/hour |
| Discount-code attempts per order | 5/hour | 5/hour |
| Quantity-refusal oracle per agent | 20/hour | 20/hour |

Every wrong discount code refuses as the same `code-invalid` with **no message and no timing tell**. Exceeding any limit refuses `rate-limited`.

### 16.9 The `scripted` model driver

The demo's default driver (D7). It is not a fake — it is the same code path with the proposer pinned, and CI runs it. It emits exactly this tool-call sequence, and deterministic code validates every one against real Merchant data before anything renders:

```
1  search                {"query": "black tote"}
2  read-item             {"group": "tote"}
3  add-line              {"sku": "SD-TOTE-BLK-M", "qty": 1}
4  add-line              {"sku": "SD-GIFTWRAP", "qty": 1, "parent": "SD-TOTE-BLK-M"}
5  add-line              {"sku": "SD-CHARMBAR-SEAT", "qty": 1}
6  set-destination       {destination B — Mumbai, 400028}
7  set-contact           {+91 90000 00001, demo@spoiledduckie.test}
8  choose-fulfillment    {"id": "rest-of-india"}
9  start-checkout        {}
10 place-order           {}          → returns an approve URL, never an order
11 order-status          {polled until paid}
```

`ollama` (`qwen2.5:7b`) and `anthropic` (`claude-sonnet-5`) are alternative drivers, used only if their key or model is present; **neither is ever required for the demo or for CI.**

### 16.10 Fake provider behaviour

`provider/fake.py` declares every method and is the demo default. `make-link` returns a link to a local page with **Approve** and **Decline**; Approve fires a correctly-HMAC'd webhook after 2s, Decline fires a failure webhook immediately. Link lifetime is 15 min, matching §16.7. `razorpay.py` declares `upi` only and **refuses to boot with a live (non-`rzp_test_`) key**.

### 16.11 The arithmetic, pinned to the paise

The Merchant computes the Quote (B2) and the Gate re-checks it (`quote-consistent`, A3). **Two implementations that round differently fire `quote-inconsistent` on a correct quote**, which looks like a bug in the money core and is actually a bug in this section being missing. So it is not missing.

**Everything seeded is tax-inclusive.** Item prices (§16.3), shipping costs (§16.5) and discount values (§16.6) all already contain GST. Tax lines are therefore `informational: true` and are **never added to the total** — they report what is already inside it.

The order of operations, exactly:

1. **Line inclusive total** = `qty × unit_price` + every Add-on folded into that line. Add-ons inherit the parent's rate, HSN/SAC and Place of Supply.
2. **Discounts** apply to inclusive amounts, apportioned across lines by line inclusive total, largest-remainder.
3. **Fulfillment** apportioned the same way, across lines by line inclusive total, largest-remainder.
4. **Largest-remainder rule**, stated once so it is not re-derived: take `floor` of each raw share; the leftover paise go one each to the lines with the largest fractional parts, descending; ties break by SKU ascending. The apportioned shares must sum **exactly** to the amount being apportioned.
5. **Extract the tax** from each line's post-discount, post-shipping inclusive amount: `tax = ROUND_HALF_UP(inclusive × rate_bp / (10000 + rate_bp))`. Use exact decimal arithmetic, never binary float.
6. **CGST/SGST split of an odd-paise tax** — the trap this whole section exists for: `CGST = tax // 2`, `SGST = tax − CGST`. SGST takes the odd paise. Deterministic, stated once, never re-decided.
7. **`round_off_minor` is always `0`** in v1. The seeded Merchant does not round to the rupee, so the line exists in the Quote shape and carries zero. A non-zero value is a bug, not a feature.
8. **`subtotal_minor`** = sum of line inclusive totals *before* shipping. **`total_minor`** = subtotal + fulfillment + discounts (negative) + round_off.

#### The worked example — use it as the first fixture

Demo basket, **destination B (Mumbai, 400028)**, no discount code. This is the §13 beat-4 basket and the Integration-1 smoke test; the numbers below are computed, not illustrative.

| | |
|---|---|
| L1 `SD-TOTE-BLK-M` ₹899 + `SD-GIFTWRAP` ₹99 folded in | 99800 inclusive, 18%, POS **MH** → IGST |
| L2 `SD-CHARMBAR-SEAT` ₹1,500 | 150000 inclusive, 18%, POS **KA** → CGST/SGST |
| `subtotal_minor` | **249800** |
| Fulfillment `rest-of-india` | **9900** |
| Shipping apportioned (raw 3955.244 / 5944.756) | L1 **3955**, L2 **5945** — the leftover paise goes to L2, the larger fraction |
| L1 inclusive after shipping | 103755 → **IGST 15827** |
| L2 inclusive after shipping | 155945 → tax 23788 → **CGST 11894 / SGST 11894** |
| `round_off_minor` | **0** |
| **`total_minor`** | **259700**  (₹2,597.00) |

Both GST splits in one Quote, which is the point of the basket. If your implementation produces any other number, it is wrong — not the table.

### 16.12 Protocol specifications — fetched 2026-09-20, pinned here

**These were fetched before hour 0, not remembered.** Anything deeper than what is recorded here comes from the documents below, never from this plan.

| Protocol | Source of truth | Version read | Licence |
|---|---|---|---|
| ACP | `github.com/agentic-commerce-protocol/agentic-commerce-protocol`, `agenticcommerce.dev` | **spec `2026-04-17`** | Apache 2.0 |
| AP2 | `github.com/google-agentic-commerce/AP2`, `docs/ap2/specification.md` | `main` @ 2026-09-20 | open, Google-led |
| UCP | as carried on `main` + its published manifest | as on `main` | — |
| MCP | as carried on `main` | as on `main` | — |

#### ACP — five endpoints, and it fits us better than expected

The repository states the specification is **maintained by OpenAI and Stripe**; Stripe's own documentation describes it as **created by Stripe, OpenAI and Meta**. Both are recorded because they differ — on stage, say "OpenAI and Stripe" and you cannot be contradicted by the repo.

Machine-readable: OpenAPI at `spec/2026-04-17/openapi/openapi.agentic_checkout.yaml` and `openapi.delegate_payment.yaml`, JSON Schema at `spec/2026-04-17/json-schema/`. **Validate against the schema; do not hand-transcribe field names.**

**Pin `2026-04-17` and do not build against `unreleased`.** The `spec/` directory holds `2025-09-29`, `2025-12-12`, `2026-01-16`, `2026-01-30`, `2026-04-17` and `unreleased` — five dated releases in under a year, so this specification revises roughly quarterly. Two consequences: the conformance badge must **name the spec version it targets** (`ACP 2026-04-17`), because unqualified "ACP conformant" ages badly against a quarterly spec; and a version check belongs in the A6 DONE WHEN so drift is noticed rather than assumed away.

**Four paths, five operations** — `/checkout_sessions/{checkout_session_id}` carries both POST and GET.

| ACP operation | Path | Maps onto |
|---|---|---|
| `createCheckoutSession` | `POST /checkout_sessions` | Pending Cart + door 9 `quote` |
| `updateCheckoutSession` | `POST /checkout_sessions/{checkout_session_id}` | line edits, Destination, Contact, fulfillment choice, re-quote |
| `getCheckoutSession` | `GET /checkout_sessions/{checkout_session_id}` | cart read |
| `completeCheckoutSession` | `POST /checkout_sessions/{checkout_session_id}/complete` | **the refusal point** |
| `cancelCheckoutSession` | `POST /checkout_sessions/{checkout_session_id}/cancel` | `cancelled`, pre-money |

Headers are **per-operation, not blanket**: `Authorization` (Bearer) and `API-Version` on every operation; `Idempotency-Key` on the POSTs only (`getCheckoutSession` has none); `Content-Type` where there is a body. Top-level schemas: `CheckoutSessionCreateRequest`, `CheckoutSessionUpdateRequest`, `CheckoutSessionCompleteRequest`, `CheckoutSession`, `CheckoutSessionWithOrder`, `CancelSessionRequest`, `Error`.

Two things fall out that are worth saying aloud: **ACP has native `Idempotency-Key`**, which maps directly onto our per-attempt idempotency discipline rather than needing a shim; and its *Delegate authentication* building block is OAuth 2.0, which is already our allowlisted-agent admission route (ADR-0012).

`completeCheckoutSession` is where the buyer's delegated credential (Stripe's Shared Payment Token, or a `vt_…` vault token under OpenAI's Delegate Payment spec) is handed to the merchant. **That is the step we refuse**, with a named code and the approve URL in the response. ADR-0008 and ADR-0013 enforced at the envelope boundary — the other four operations work normally.

#### AP2 — **not a fourth envelope. A security layer, and it rides on UCP.**

This is the finding that justified fetching before building. Three sentences from the specification, quoted verbatim and re-verified against the raw document on 2026-09-20:

> "AP2 operates as a security feature within a Commerce Protocol."
> "AP2 is designed explicitly to be compatible with the Universal Commerce Protocol (UCP) and integrates seamlessly."
> "AP2 defines two Mandate types: Checkout Mandate and Payment Mandate."

Catalogue APIs and checkout updates are stated to be outside AP2's own scope.

So AP2 is not a sibling of MCP/UCP/ACP and must not be built as one. It is a mandate layer **over** our UCP translator.

Also corrected: the launch-era three mandates (Intent / Cart / Payment) are **not** what the current spec defines. It defines **two**:

- **Checkout Mandate** — proves the Shopping Agent is authorized to purchase this checkout. Carries a `checkout_hash` claim binding it to a merchant-signed Checkout JWT: *"Verify that the hash of the Checkout JWT sent for approval matches the value included for the `checkout_hash` claim."* Versioned by `vct`, e.g. `mandate.checkout.open.1`.
- **Payment Mandate** — proves authorization to pay. Carries `checkout_hash`, `transaction_id`, and a `cnf` claim holding the agent public key in autonomous mode. Versioned by `vct`, e.g. `mandate.payment.1`.

Merchant obligations: create a signed Checkout JWT; receive and verify the Checkout Mandate; validate it against the Agent Authorization verification rules; **confirm the hash of your Checkout JWT matches the mandate's `checkout_hash`**; check conformance to any open mandate's constraints; return a Checkout Receipt JWT.

The spec's own names for the two modes, verbatim — use these on stage, not paraphrases:

> **"Human Present (Direct): The User directly sees the closed Checkout and approves it and its payment explicitly."**
> **"Human Not Present (Autonomous): The User sees and approves a set of constraints over what closed Checkout and Payment would meet their intent."**

**Human Present (Direct)** is the mode we support: the agent gets a closed Checkout JWT from the merchant, builds both mandates, **presents them to a Trusted Surface for the user to review and sign** — the Trusted Surface being *"a UI surface that is trusted to get informed user consent for an Intent before creating a user-signed Mandate"*, which is exactly what `/agentic/approve` is — then forwards the signed Payment Mandate to the Credential Provider and returns with a payment credential plus the Checkout Mandate; the merchant verifies the checkout matches and initiates payment.

**Human Not Present (Autonomous)** is our `mandate` Authority kind: registered, recorded, **refused in v1** (ADR-0017).

**The convergence worth putting on a slide:** AP2's `checkout_hash` binding a signed mandate to a merchant-signed Checkout JWT is structurally the same primitive as our `cart_hash` binding an Authority to a merchant-signed Quote (§6.3). We arrived at it independently, and their Trusted Surface is our approve page. Say "structurally the same primitive," not "identical" — the field sets differ and the claim should survive someone opening both specs.

### 16.13 What is deliberately not pinned

Copy, microcopy, and empty-state wording — write them in the voice §4 describes. Product images — generated placeholders in the design palette; no scraping. Exact component structure inside a phase. Anything §12 lists as a cut.

If you find yourself wanting a constant that is not in this section, that is ladder step 4 in §0: take the conservative option, log it under `## OPEN —`, keep building.
