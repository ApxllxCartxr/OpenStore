# LOGS

Running record of changes and decisions for the OpenStore MVP build. Newest entry at the top. Every entry: what changed, why, and what it means for anyone executing `SPECS/PLAN.md`.

---

## 2026-09-20 · Track S re-budget — three changes applied

Prompted by a question about Track S, which turned into a second look at the schedule. Audit #1 fixed the schedule's *arithmetic*; this fixes its *estimates*, which were a separate and larger error.

### The finding behind it: the reuse asymmetry

Checked and confirmed: **`main` has no `package.json` and no `node_modules`.** Its entire UI is Jinja templates plus three vanilla JS files served from FastAPI. So Track P inherits ~7,000 lines of debugged Python *and* can crib `/agentic` from main's existing studio templates, while **Track S inherits nothing from `main` at all** — both SvelteKit apps are greenfield, and its only reuse is the portfolio design system.

Equal hours in the table, unequal risk in reality. Track S also gates all three integration points. Neither fact was written down; both are now.

### Two phases were under-budgeted — correcting my own numbers

- **C1 was 3h and could not have been.** It contained the whole chat UI *plus* ES256 key generation, Agent Profile publishing, RFC 9421 request signing, a three-driver model seam, the scoped action set and a bounded tool loop. Realistically ~5h.
- **C3 was 3h and was light.** Eleven distinct UI states, a verbatim signed-quote renderer with a byte-equality assertion, a pre-display signature check, and named copy for eleven reason codes.

Corrected: **C1 → 3.5h** (after the signing slice moves out, below), **C3 → 4.5h**.

### Change 1 — the agent signing kit moves to Track P as new phase A4c (1.5h)

The chat *signs* RFC 9421 requests; the sidecar *verifies* them. Same specification from two ends, and A4 is already writing the verifier. Split across two builders in two languages, the signature base gets debugged twice by two people who each assume the other side is wrong.

A4c gives both to one person in one sitting, against one shared fixture set asserted in **both** test suites. The files live in `demo/buyer-chat/src/lib/identity/` and are scheduled **first on day 2**, because C1 consumes them and because A4's verifier is still fresh from the previous evening.

**This does not touch the import firewall.** The firewall forbids imports across roots, not authorship — a person is not a root. The files import nothing from the sidecar and reach it only over HTTP.

### Change 2 — reuse two libraries instead of hand-rolling

- `http-message-signatures` (npm) for the RFC 9421 signature base, rather than writing it. It is the fiddly part and it is someone else's solved problem. ES256 itself needs no dependency — Node's built-in WebCrypto covers P-256/SHA-256.
- A headless table/form primitive for B3's variant matrix; the design system supplies the styling either way. **B3 3.5h → 3.0h.**

### Change 3 — the configuration is now decided at hour 0, not discovered at hour 22

New block in §12 with two options, one of which must be chosen and written here before any code:

- **Option A — a third builder takes Surface C.** B and C share no state, no code and no database. Surface C is 11.5h and lifts out cleanly; Track S then runs Surface B alone at 16.5h against 26.0, and **cuts 4a and 5 are not needed**. Costs the demo nothing. Take this if any third person exists.
- **Option B — two builders, cuts 5 and 4a taken up front.** Cut 5 drops the direct storefront checkout (−1.5h, cheapest cut in the plan — that path takes no money anyway). Cut 4a **trims** the admin rather than gutting it (−1.5h): keep create/edit, variant matrix, stock, price, dispatch, refund; drop CSV, bulk archive and pricing-setup CRUD. Explicitly **not** the older blunt "read-only admin" cut, which trades away Surface 2's entire reason to exist for the same ninety minutes.

### A second arithmetic error, found while applying the above

Audit #1's table omitted the three integration windows — 1.5h where neither track is building. Adding them: **both tracks now total 26.5h against 26.0 available, −0.5h each.** The earlier "+1.0h margin" on Track P was wrong.

Without cuts 4a and 5, Track S is **29.5h against 26.0 — three and a half hours over**, which is the number that makes the hour-0 decision non-optional.

The −0.5h is absorbed by the polish pass, which is budgeted 1.5h and scheduled 0.5h. Recorded in the plan as a deliberate choice: **polish is the buffer; the rehearsal is not.** An unrehearsed demo wastes the whole plan; plain empty states go unnoticed.

### Also changed

- Day 1 and Day 2 tables rebuilt against the corrected budgets, with B2 explicitly finishing by hour 10 because Integration 1 depends on it, and C2 by 17.5 because Integration 2 does.
- Cut list renumbered: 4a and 5 are spent under Option B and no longer available. The remaining list now notes that **cuts 1–3 are all Track P, the track with margin** — so their real value is freeing a Track P builder to move onto C3, not the hours themselves.
- §5 layout shows `lib/identity/` with a note on who authors it.

---

## 2026-09-20 · Audit #1 of SPECS/PLAN.md

Full read-through of the plan against `SPEC.md`, `CONTEXT.md`, `docs/adr/0017`, `0018`, and the actual code on the `main` branch. Every claim about `main` was checked by reading the file, not by memory.

**Verdict: the plan was directionally right and had 8 things in it that would have cost real hours or broken the demo.** All 26 findings below are fixed in the same commit. Severity is about consequence, not about how wrong the sentence was.

### Blockers — would have broken the build or the demo

| # | Finding | Fix |
|---|---|---|
| B-1 | **`.localhost` does not resolve inside containers.** The chat container fetching `spoiledduckie.localhost` hits its own loopback, not the proxy. Worse, the SSRF rule is "HTTPS only" and the compose fetch is HTTP — the dev exception covered hostnames but not the scheme, so the demo's own self-registration would have been refused by its own hardening. | New §10.1 "Two addresses for one shop": browser origin vs container origin, named explicitly. Dev exception now covers scheme **and** host, both fenced. |
| B-2 | **Receipt viewer was at `/agentic/receipt/<id>`** — inside the session-authenticated Merchant console. The spec requires a receipt to open by unguessable ID with **no login**. As written, no Consumer could ever open their own receipt. | Moved to public `/receipt/<id>`, added to the mount table, and the auth boundary is now stated per route. |
| B-3 | **Door 3 `reserve` took `cart_id` but keyed idempotency on `order_id:attempt`.** Contradiction: `reserve` runs after `orders.create`, so it has an `order_id`. | Door 3 input corrected to `order_id`. A call-sequence diagram added so the ordering cannot be misread. |
| B-4 | **`order_salt` custody was undefined.** The sidecar must have it to compute `destination_hash` / `contact_hash`, but the spec says it "lives only in the Merchant order row." Who generates it, how it reaches the sidecar, and why the sidecar does not persist it were all unwritten — and getting this wrong silently breaks ADR-0011's erasure guarantee. | New §6.6 "Where `order_salt` lives": Merchant generates at `orders.create`, returns it in that response only, sidecar holds it in request memory and never writes it. A test asserts it appears in no sidecar table and no log. |
| B-5 | **COD receipt sealing time was undefined.** Money moves days after `confirmed`, so "the bundle is sealed at payment" has no meaning on the cash path. | Stated: sealed at `confirmed` with an empty `moved`, and collection appends a v2 `moved` entry through the same versioning path refunds already use. No new machinery. |
| B-6 | **Schedule arithmetic was wrong and Track S was over-subscribed.** C3 (3h) + C4 (2h) were slotted into one 3h block; B1 was labelled 4h but occupied 6h of slots; Track S totalled ~25h against ~23h of slots. The plan also never said how many people it assumes. | §12 rebuilt: every phase hour re-summed, C5 moved to Track P, staffing stated in the first line (**two builders, 13h days**), and per-track totals printed against available hours so the margin is visible instead of implied. |
| B-7 | **`main`'s `create_refund_entry` keys idempotency on `checkout_id` alone.** A second partial refund returns the first entry and **writes nothing, silently**. The plan lists partial refunds as in-scope and lists this file as near-verbatim reuse — copying it would have shipped that bug. | Flagged in the reuse map as a named trap with the required fix (key on `refund_id`), and an explicit test added to A3's DONE WHEN. |
| B-8 | **`chat.localhost` had no Caddy site block.** Nothing served it. | Added to the compose/proxy section. |

### Logic errors — wrong, but recoverable

| # | Finding | Fix |
|---|---|---|
| L-1 | §6.5 check 1 named only `passkey` and `upi-pin`; a reader of the numbered list alone would mishandle `confirmed-intent`. | Check 1 now points at the Binding table as normative. |
| L-2 | `quote-fresh` is check 11, described as "immediately before `RESERVE`", while `method-enabled` is check 12 — implying a check runs after the reserve. The actual call sequence was never written down. | Explicit call sequence added to §6.5. SPEC's frozen check order is unchanged; what is now stated is that **all twelve checks are inside `decide()`, and `RESERVE` follows `decide()` returning**. |
| L-3 | Transcript check results were implicitly pass/fail, but `upi-pin` defers check 1 — so the Transcript would record a **pass for an authority that has not arrived**, which is a false statement in signed evidence. | Third result state `deferred` added to the closed set, with `settle()` required to resolve every deferred check before capture. |
| L-4 | `settle()` on COD was described as running at order time *and* capturing days later — two moments in one function. | Third entry point `collect()` named explicitly, with what it may and may not do. |
| L-5 | `consumer_id = HMAC(key, payer_handle)` — but a COD order authorized by the `passkey` mechanism has **no payer handle at all**. | Fallback stated: the passkey credential ID is the handle source on that path, recorded in the Transcript so the derivation is never ambiguous. |
| L-6 | `design/` firewall permitted only `.css/.woff2/.svg/LICENSE-*`, and §4 then put a `README.md` in it. The firewall test would have failed on the design system's own documentation. | `.md` added to the permitted list. |
| L-7 | B1 said `expires_at` carries "two deadlines"; A4 said three. | Reconciled to three. |
| L-8 | A5 said the verifier "prints both" after listing three things. | Corrected. |
| L-9 | Seed claimed 12 groups → "~20" Catalogue Items; the enumeration yields 15. | Exact counts written out per group so the seeder is a checklist, not an estimate. |
| L-10 | The seeded COD order was merchant-side only, so it could not have a sidecar Ledger or appear in `/agentic` — "an empty Ledger" was vacuously true for the wrong reason. | Seeding rule added: any order that must be visible on both sides is created by **driving the real flow** in a script, never by inserting rows. |
| L-11 | The storefront's 30-minute direct-cart stock hold had no table and no sweeper. | `site_carts` table and its sweeper added to B1. |
| L-12 | HTTP status per refusal was ambiguous — `variant-required` could reasonably be 400 or 409, and two implementers would have chosen differently. | Status is now derived from the reason code by **one table in `core/codes.py`**, never chosen at the call site. |
| L-13 | §5 layout omitted `confirmed_intent.py`. | Added. |
| L-14 | A3 and B2 both claimed the 50-concurrent-on-5-units test without distinguishing the fake from the real store. | Disambiguated: A3 runs it against the conformance fake, B2 against Postgres. Both are required; they test different things. |
| L-15 | **Ledger shape was unstated.** `main` is double-entry with named accounts (`merchant_revenue`, `customer`); the plan's invariant formulas (`sum(REFUND) ≤ captured`) assume one row per money event. An implementer copying `main` would have produced a ledger whose arithmetic does not match the plan's. | Stated explicitly: **single entry per money event**, `main`'s account legs are deliberately not carried over, and the reason is written down. |

### Unverified claims — softened rather than asserted

| # | Finding | Fix |
|---|---|---|
| U-1 | "Open Sauce Sans is OFL" was asserted from memory. The fonts folder ships `LICENSE-EBGaramond.txt` and `LICENSE-Iosevka.txt` and **no licence for Open Sauce Sans or PP Kyoto**. | Downgraded to "believed OFL, licence file absent — verify before anything public", alongside the existing PP Kyoto flag. |
| U-2 | "`localhost` is a secure context, so WebAuthn works" — true in Chrome and Firefox for `*.localhost`; **Safari does not resolve `*.localhost` at all**. | Browser support stated, with the `/etc/hosts` fallback written out. |
| U-3 | The Quote example carried `"amount_minor": -0`, which is not a meaningful JSON literal and could be copied as one. | Replaced with a real negative value, and the whole block labelled shape-not-literal. |

### Verified as correct (checked, not assumed)

- Line counts in the reuse map: `compiler.py` 322, `ledger.py` 374, `poai.py` 518, `webauthn_rp.py` 621, `oauth.py` 501, `idempotency.py` 156, `verify/checks.py` 660, `verify/cli.py` 181, `mcp_server.py` 1859, `wellknown.py` 296, `webhooks.py` 286, `inventory.py` 792. `virtual_authenticator.py` is **204** (plan said ~180 — corrected) and `core/health.py` is **227**.
- `core/health.py`, `core/database.py`, `core/audit.py`, `config.py` all exist on `main` as cited.
- The `rebuild` branch genuinely has no `src/` — this is a greenfield build on top of `main`, as the plan says.
- ADR-0017's Binding table, ADR-0018's COD ledger rules, and the `confirmed-intent` two-mechanism closed set are quoted accurately.
- The portfolio design system is at `~/projects/portfolío/frontend` (accented `í`), SvelteKit 2 / Svelte 5 / Tailwind v4, with 9 woff2 files and 2 licences — counted, not estimated.

### Added for execution safety

The plan was written to be *read*. It is now written to be *executed*, including by someone or something that will not push back when a step is ambiguous:

- **§0 Execution protocol** — the order phases run in, the commit discipline, and three standing rules: never invent an identifier, never widen scope to make a test pass, stop and ask rather than guess.
- **Per-phase file checklists** — each phase now names the files it creates, so "done" is countable.
- **Integration points as runnable commands** — the three ★ gates in §12 are now commands with expected output, not descriptions.

---

## 2026-09-20 · SPECS/PLAN.md — D1 and D4 settled

- **D1 (merchant site stack)** resolved to **SvelteKit**, on the operator's "whichever is easiest." It is the easier path: the portfolio design system is already SvelteKit 2 / Svelte 5 / Tailwind v4, so it becomes a dependency rather than something to re-implement in Next.js. Costs a one-line amendment to `SPEC.md §1` and `PLAN-merchant-site.md`, which lands in the hour-0 contracts commit.
- **D4 (COD)** flipped from **cut** to **in**, on the operator's "let COD in, it's easiest." Verified against ADR-0018 and ADR-0017 before flipping: COD is the cheapest money path in the product — no provider call, no payment link, no webhook, no link-expiry sweep, no reconcile. Cost is four specific additions (`confirmed-intent` Authority, `CAPTURE` with no preceding `RESERVE`, a third `expires_at` deadline, RTO as `cancelled`+`rto`), budgeted at 2h and ranked **cut #6 — last**, because by the time it is built the Ledger work it demonstrates is already done.
- Propagated through 14 sections: closed sets, the Binding table, Ledger rules, order lifecycle, receipt honesty, schema, admin actions, chat copy, four new test gates, the schedule, and a 7th demo beat.

## 2026-09-20 · SPECS/PLAN.md created

Comprehensive 48-hour MVP build plan covering all three surfaces, written against `SPEC.md` with departures named in §2. Grounded in a survey of the `main` branch (~7,000 reusable lines identified, ~7,000 deliberately left behind) and the portfolio design system.
