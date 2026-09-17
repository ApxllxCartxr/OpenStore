# Expansion Guidelines — how to grow the three plans without re-creating the old mess

Read this before editing `PLAN-sidecar.md`, `PLAN-merchant-site.md`, or `PLAN-buyer-chat.md`. The plans are yours to expand; these rules keep the three surfaces honest with each other.

## 1. The document ladder (where each kind of update goes)

Every change lands in exactly the layers it touches — never just the plan file:

| Change | Touch in order |
|---|---|
| New/renamed domain word | `CONTEXT.md` first (1–2 sentences + `_Avoid_`), then the plan phases using it |
| New hard-to-reverse / surprising / traded-off decision | New `docs/adr/NNNN-slug.md`, then `SPEC.md`, then plan phases |
| New behavior or rule | `SPEC.md` section first, then the owning surface's plan phases |
| New phase/step inside one surface | That surface's plan only, provided §2 holds |
| New DONE WHEN assertion | Add freely; weakening or deleting one needs a grill round + ADR note |

RULE: `SPEC.md` + `CONTEXT.md` + ADRs describe the *agreed* system. Plans describe *how to build it*. A plan may never promise what the spec forbids, and a spec change without its plan phases updated is unfinished.

## 2. In-surface expansion (free, with two rails)

- **Phase order inside a surface is advisory.** Reorder, split, or merge phases as the work teaches you — renumber and keep the DONE WHEN gates attached to the work, not the number.
- **DONE WHEN gates only tighten.** You may add assertions or sharpen thresholds. Softening or deleting a gate requires a grill round and a one-line ADR note saying what evidence justified it.
- **Scope ownership:** shop-ops wording lives in the merchant-site plan, chat wording in the buyer-chat plan, money/authority wording in the sidecar plan. Borrow terms, don't redefine them — link to the owning file's phase instead.
- **No engine creep:** any step shaped like auto-discounts, rules, bots, or stalled-loops fails review on sight in v1 (SPEC §13). Manual prices and a hand-entered code table only. Shipping, tax, and discounts are *computed by the Merchant behind door 9* and only ever verified in the sidecar (ADR-0010) — a rate table, tax calculation, or code interpretation appearing in `src/openstore/sidecar/` is an engine no matter what it is called.

## 3. Cross-surface contracts (two-sided edits or it didn't happen)

These eight are shared. Changing one side without the mirror side + spec is a broken change, even if tests are green:

| Contract | Owner of definition | Mirror sides | Files to touch together |
|---|---|---|---|
| 9-door Merchant trait (doors, shapes, codes) | Sidecar S2 | Merchant-site M3 | `SPEC.md` §5 + both plan phases + trait conformance suite |
| Quote shape + `cart_hash` preimage | Sidecar S3 | Merchant-site M3 (door 9), buyer-chat B3 (verbatim render) | `SPEC.md` §4 + ADR-0010 + all three plan spots + golden vectors |
| Agent admission (Profile, signed requests, tiers) | Sidecar S4 | Buyer-chat B2 (self-registration) | `SPEC.md` §7 + ADR-0012 + both plan phases |
| Order statuses + transitions (8 canonical) | Sidecar S4 | Merchant-site M1/M3 (rows), buyer-chat B4 (messaging) | `SPEC.md` §6 + all three plan spots + mapping table |
| Protocol envelopes (MCP/UCP/ACP/AP2) | Sidecar S6 | Buyer-chat B5 (toggle + replays) | `SPEC.md` §9 + both plan phases + golden fixtures |
| Auth material (passkey binding, OAuth scopes, token shapes) | Sidecar S4 | Buyer-chat B3/B4 (modals, resume) | `SPEC.md` §7 + both plan phases |
| Discovery card + seed-list format | Sidecar S2 | Buyer-chat B2 (reader, contacts) | `SPEC.md` §3 + both plan phases + example file |
| Mount paths (`/`, `/.well-known`, `/agent`, `/agentic`) | Sidecar S7 | Merchant-site M5, buyer-chat B4 (approve/resume URLs) | `SPEC.md` §10 + all three plan spots + compose file |

RULE: the conformance suite / golden fixture for a contract is updated in the *same pass* as the contract text. Text-first-fixture-later is how drift starts.

## 4. Firewall (non-negotiable, all grills check it)

- Roots `src/openstore/sidecar/`, `demo/merchant-site/`, `demo/buyer-chat/` share nothing: no imports, types, test helpers, or DB access across roots, either direction. HTTP + signed webhooks only.
- A plan step requiring a cross-root import is an invalid step — rewrite it as an HTTP call with shapes + codes, or bring it to the grill.
- The firewall test itself lives in the sidecar repo path but asserts over all three roots.

## 5. How the three grills run

One grill per surface plan, in build order (sidecar → merchant-site → buyer-chat), because later surfaces depend on earlier contracts. Bring the expanded file; I bring cross-surface breaks.

- **Entry:** the surface file is expanded (every phase has steps + DONE WHEN, no TBDs except explicitly marked phase-two items).
- **My job:** cross-surface breaks (§3), firewall breaches (§4), gate softening (§2), spec/ADR drift (§1), smoothness-law violations (SPEC §11).
- **Exit:** zero open breaks, contracts mirrored, gates intact. Then that surface is buildable and we move to the next grill.
- **After all three:** SPEC + plans + ADRs get a final consistency pass, then Slice 1 (Sidecar S1) starts.
- **`PLAN-distribution.md` is not in this rotation.** It is phase two, gated on `PLAN.md` step 7, and gets its own grill when that gate goes green — grilling reach before the thing being reached is installable is wasted effort. Its rails are the same: no engine creep, no money-core change, and the firewall extends to any new root it adds (the WooCommerce adapter is a Merchant, not part of the sidecar).

## 6. Pre-grill self-check (run this before calling me)

- [ ] Every new term is in `CONTEXT.md` with `_Avoid_` or it isn't used.
- [ ] Every behavior change is in `SPEC.md` or the plan doesn't promise it.
- [ ] Every contract change touched all files in its §3 row.
- [ ] No phase reaches across roots except via named HTTP doors.
- [ ] No DONE WHEN was softened without an ADR note.
- [ ] Nothing from SPEC §13 (phase-two list) snuck in as "just a small step".
