# Expansion Guidelines: how to grow the three plans without remaking the old mess

> Formerly `docs/EXPANSION.md`; renamed during the documentation reorganisation. References to `PLAN-*.md`, `Specification.md`, and `Glossary.md` mean the files now in this directory as `Plan-*.md`, `Specification.md`, and `Glossary.md`.

Read this before you edit `Plan-Sidecar.md`, `Plan-Merchant-Site.md`, or `Plan-Buyer-Chat.md`. The plans are yours to expand. These rules keep the three surfaces honest with each other.

## 1. The document ladder (where each kind of update goes)

Every change lands in exactly the layers that it touches. A change never lands in the plan file alone.

| Change | Touch in order |
|---|---|
| New/renamed domain word | `Glossary.md` first (1 to 2 sentences + `_Avoid_`), then the plan phases using it |
| New hard-to-reverse / surprising / traded-off decision | New `docs/adr/NNNN-slug.md`, then `Specification.md`, then plan phases |
| New behavior or rule | `Specification.md` section first, then the plan phases of the owning surface |
| New phase/step inside one surface | The plan of that surface only, provided §2 holds |
| New DONE WHEN assertion | Add freely. Weakening or deleting one needs a grill round + ADR note |
| External fact from a regulator, rail, or third-party spec | The document that relies on it, with a dated citation: circular or section number, issuing body, date, and URL. Never a remembered figure |

RULE (citations): You write each number from outside this repo with its source and date. That rule covers:

- a cap
- a threshold
- a limit
- a well-known path
- a version number

A later revision then diffs against a named document, not against memory. Two review passes once changed the same figure from a wrong number and back. Neither side opened the source (`Review-Remediation.md` item #1).

`Specification.md` plus `Glossary.md` plus ADRs describe the agreed system. Plans describe how to build it. A plan never promises what the spec forbids. A spec change without updated plan phases is unfinished.

## 2. In-surface expansion (free, with two rails)

- Phase order inside a surface is advisory. You reorder, split, or merge phases as the work teaches you. You renumber the phases. You keep each DONE WHEN gate attached to the work, not to the number.
- DONE WHEN gates only tighten. You can add assertions or sharpen thresholds. If you soften or delete a gate, you run a grill round. You write a one-line ADR note that states the evidence.
- Shop-ops wording lives in the merchant-site plan. Chat wording lives in the buyer-chat plan. Money and authority wording live in the sidecar plan. You borrow terms. You do not redefine terms. You link to the phase of the owning file instead.
- No engine creep passes review in v1 (SPEC §13). A step shaped like auto-discounts, rules, bots, or stalled-loops fails on sight. You use manual prices and a hand-entered code table only. The Merchant computes shipping, tax, and discounts behind door 9. The sidecar only verifies those values (ADR-0010). A rate table, tax calculation, or code interpretation in `src/openstore/sidecar/` is an engine, no matter its name.

## 3. Cross-surface contracts (edits on both sides or no change)

These eight contracts are shared. If you change one side without the mirror side and the spec, the change is broken. Green tests do not fix it.

| Contract | Owner of definition | Mirror sides | Files to touch together |
|---|---|---|---|
| 9-door Merchant trait (doors, shapes, codes) | Sidecar S2 | Merchant-site M3 | `Specification.md` §5 + both plan phases + trait conformance suite |
| Quote shape + `cart_hash` preimage | Sidecar S2 (defined and frozen before S3) | Merchant-site M3 (door 9), buyer-chat B3 (verbatim render) | `Specification.md` §4 + ADR-0010 + all three plan spots + golden vectors |
| Agent admission (Profile, signed requests, tiers) | Sidecar S4 | Buyer-chat B2 (self-registration) | `Specification.md` §7 + ADR-0012 + both plan phases |
| Order statuses + transitions (8 canonical) | Sidecar S4 | Merchant-site M1/M3 (rows), buyer-chat B4 (messaging) | `Specification.md` §6 + all three plan spots + mapping table |
| Protocol envelopes (MCP/UCP/ACP/AP2) | Sidecar S6 | Buyer-chat B5 (toggle + replays) | `Specification.md` §9 + both plan phases + golden fixtures |
| Auth material (passkey binding, OAuth scopes, token shapes) | Sidecar S4 | Buyer-chat B3/B4 (modals, resume) | `Specification.md` §7 + both plan phases |
| Discovery card + seed-list format | Sidecar S2 | Buyer-chat B2 (reader, contacts) | `Specification.md` §3 + both plan phases + example file |
| Mount paths (`/`, `/.well-known`, `/agent`, `/agentic`) | Sidecar S7 | Merchant-site M5, buyer-chat B4 (approve/resume URLs) | `Specification.md` §10 + all three plan spots + compose file + Fly demo topology (ADR-0019) |

RULE: You update the conformance suite or golden fixture for a contract in the same pass as the contract text. Text first and fixture later starts drift.

## 4. Firewall (non-negotiable, all grills check it)

- Roots `src/openstore/sidecar/`, `demo/merchant-site/`, and `demo/buyer-chat/` share nothing. That rule bans each of these across roots, in each direction:
  - imports
  - types
  - test helpers
  - DB access
  You use HTTP plus signed webhooks only.
- A plan step that needs a cross-root import is invalid. You rewrite it as an HTTP call with shapes and codes. Or you bring it to the grill.
- The firewall test lives in the sidecar repo path. It asserts over all three roots.

## 5. How the three grills run

You run one grill per surface plan. You follow build order: sidecar, then merchant-site, then buyer-chat. Later surfaces depend on earlier contracts. You bring the expanded file. The reviewer brings cross-surface breaks.

- Entry: the surface file is expanded. Each phase has steps and DONE WHEN. No TBDs remain, except marked phase-two items.
- The reviewer checks five items:
  - cross-surface breaks (§3)
  - firewall breaches (§4)
  - gate softening (§2)
  - spec or ADR drift (§1)
  - smoothness-law violations (SPEC §11)
- Exit: zero open breaks remain. Contracts are mirrored. Gates stay intact. Then that surface is buildable. Then you move to the next grill.
- After all three grills, SPEC plus plans plus ADRs get a final consistency pass. Then Slice 1 (Sidecar S1) starts.
- `Plan-Distribution.md` stays out of this rotation. It is phase two, gated on `Plan-Index.md` step 7. It gets its own grill when that gate goes green. You do not grill reach before the reached thing is installable. That effort is wasted. Its rails stay the same:
  - no engine creep
  - no money-core change
  - the firewall extends to each new root it adds
  The WooCommerce adapter is a Merchant, not part of the sidecar.

## 6. Pre-grill self-check (run this before you call a grill)

- [ ] Every new term sits in `Glossary.md` with `_Avoid_`. Or you do not use the term.
- [ ] Every behavior change sits in `Specification.md`. Or the plan does not promise it.
- [ ] Every contract change touched all files in its §3 row.
- [ ] No phase reaches across roots except via named HTTP doors.
- [ ] You softened no DONE WHEN without an ADR note.
- [ ] Nothing from SPEC §13 phase-two list entered as a small step.
