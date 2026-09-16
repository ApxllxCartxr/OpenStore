# Delivery plan — Stages 26 and 27

Status as of 2026-09-16: **not started.** `docs/SPECS/stage-26-inventory.md`
and `docs/SPECS/stage-27-merchandising.md` describe the intended work;
no implementation, migration, or test for either stage exists in the tree.

Commit `c6072c1` carries stage-26 and stage-27 blocks in its message. They
describe work that is not in the commit. Treat that message as intent, not
as a record — this plan supersedes it.

## Prerequisites (before any stage-26 code)

These are cheap, and stage 26 cannot be reviewed honestly without them.

1. **Record the decisions the specs already cite.** `docs/DECISIONS.md` stops
   at DECISION-043. DECISION-044 through 048 are referenced across the stage
   24–27 specs and commit messages but were never written down.
   DECISION-047 (pre-compiler oversell gate) and DECISION-048 (bundles reuse
   campaign discounts) are load-bearing for these two stages: they are the
   reason `core/compiler.py` stays byte-identical and the reason there is no
   second discount path. Write them before building against them.
2. **Record Q-044 through Q-047 in `docs/OPEN_QUESTIONS.md`,** which stops at
   Q-043. Stage 26's spec resolves Q-046 (check-10 no-op); stage 27 leans on
   Q-045's evidence-access model for the "why-stat" panel.
3. **Decide whether `related_source` belongs on the wire.** Stage 25 added
   `stock` and `related_source` to the normalized item shape, and both now
   appear in MCP `get_product` responses (the conformance fixtures were
   regenerated to match). `stock` is buyer-meaningful; `related_source` is
   internal provenance and stage 27 will add more of it. Settle this before
   stage 27 freezes a second provenance field onto a published shape.

## Sequencing

Stage 26 strictly precedes stage 27: `suggest_for_cart` filters on
sellability, which does not exist until inventory does. Within stage 26 the
order below is the order in which each piece becomes testable.

### Stage 26 — Inventory

| # | Work | Verified by |
|---|---|---|
| 1 | Migration `0012`, `inventory_items` + `inventory_ledger_entries` | Round-trip on sqlite from a DB stamped `0011` |
| 2 | `core/inventory.py` mirroring `core/ledger.py` | `verify_inventory_balances` reserved-nets-to-zero |
| 3 | Pre-compiler gate in `create_checkout_from_policy` | Threaded last-unit race test |
| 4 | RESERVE / COMMIT / RELEASE / RESTOCK hooks | Idempotency under both reconciliation paths |
| 5 | `_inventory_sync_loop` + drift handling + write-back DLQ | Forced-failure test lands in DLQ with alert |
| 6 | Low-stock thresholds, `detect_stalled_skus` exclusion, delete `run_sweepers` | Existing stage 15 suite stays green |

Steps 1–3 are the soundness core; 4–6 are operational. If the stage has to be
cut short, cut at 3 and ship the gate — an oversell that reaches capture is
the failure that costs real money.

### Stage 27 — Merchandising

| # | Work | Verified by |
|---|---|---|
| 1 | Migration `0013`, `merchandising_rules` mirroring `Campaign` | State-machine transitions match Campaign's |
| 2 | Deterministic `suggest_for_cart` | Golden test pins suggestion order |
| 3 | Adapter-native cross-sell/up-sell fields | WooCommerce store with zero config |
| 4 | `draft_merchandising_rules` + passkey activation | Cross-rule assertion replay fails closed |
| 5 | Surfaces: `/merchant/merchandising`, `/chat`, Discord, MCP | Conformance regen + tool-count sweep |

## Risks carried from stages 24–25

Both were reviewed on 2026-09-16; the defects below were fixed, and they
describe the failure modes most likely to recur in this code.

- **Nested sessions roll back the caller.** `effective_settings` opens its own
  `Session` when none is passed, and under SQLite's StaticPool every session
  shares one connection. Stage 26 writes inventory ledger rows inside
  `immediate_session`; any helper it calls that resolves settings or the
  catalog **must** be handed the live session. This silently destroyed
  seeded rows across three test suites before it was found.
- **Migrations lag the models.** `0001` runs `create_all` against live
  metadata, so a new table needs no migration to pass tests — and no
  deployment past `0001` will ever get it. Stage 24 shipped two tables this
  way. Write `0012`/`0013` against a database stamped at the previous
  revision, not a fresh one.
- **Duplicated patch application.** Stage 24/25 landed several functions
  twice at different scopes, including two with undefined names in the dead
  copy. `uv run mypy src/` catches this class (`no-redef`, `name-defined`);
  run it before committing, not after.
- **Wire shapes drift silently past goldens.** Adding a field to the
  normalized item changed MCP `get_product` output without any test naming
  the shape until the stage-21 fixtures failed. Stage 27 adds suggestion
  fields to the same path; regenerate fixtures in the same commit.

## Definition of done for the pair

- `uv run pytest -q` green with no pre-existing-failure carve-out. If a test
  fails, it is either fixed or deleted with a reason — the suite is the
  record, and "pre-existing" claims must be checked against the previous
  commit's **own** source tree, not against the working tree's installed
  package.
- `uv run mypy src/`, `uv run ruff check src/ tests/`,
  `uv run python scripts/registry_diff.py` all clean.
- Migrations `0012` and `0013` verified upgrade-and-downgrade against a
  database stamped at the preceding revision.
- One commit per stage, `stage(26): inventory` and `stage(27): merchandising`,
  each describing only what that commit contains.
