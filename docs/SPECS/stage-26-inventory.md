# Stage 26 — Inventory and stock monitoring

Slug: `inventory`. Goal: real stock, no oversell, honest drift handling.

Status: **shipped** as `stage(26): inventory` (2026-09-16). See
`docs/PLAN-stage-26-27.md` for sequencing and for the prerequisites
(DECISION-047 and Q-046 are recorded in `docs/DECISIONS.md` /
`docs/OPEN_QUESTIONS.md`).

Depends on Stage 25: stock arrives through `stock_source`, which resolves
independently of `catalog_source`. A normalized `stock` of `None` means
unmanaged and must never be coerced to 0.

## Scope

1. **Schema — migration `0012`**
   - `inventory_items`: unique on `(merchant_id, sku)`, carrying a `tracked`
     flag and a low-stock threshold. `tracked=false` opts a SKU out of every
     gate in this stage.
   - `inventory_ledger_entries`: mirrors `core/ledger.py`'s row shape,
     including a unique `idempotency_key`. Quantity movements are ledger
     entries, never in-place decrements — the same discipline the money
     ledger already uses, for the same reason.
   - Existence-guarded like `0007`/`0011`, and written against a database
     stamped at `0011` rather than a fresh one (`0001`'s `create_all` hides
     missing migrations).

2. **`core/inventory.py`** — the ledger idioms transcribed from
   `core/ledger.py`: `create_reserve_entry`/`create_capture_entry`/
   `create_release_entry`/`create_refund_entry` gain inventory counterparts,
   plus `verify_inventory_balances` mirroring `verify_ledger_balances`.
   `compute_sku_exposure` sums outstanding reservations for a SKU;
   `check_stock_available` answers the gate.

3. **Lifecycle hooks**, each idempotent under both reconciliation paths (the
   webhook and the hold-loop reconcile can both fire for one payment):
   - RESERVE in `create_checkout_from_policy`, inside the same
     `immediate_session` as `initiate_hold`.
   - COMMIT beside capture in `psp/razorpay_driver.py::_apply_payment_link_paid`.
   - RELEASE on cancel, failure, and expiry (`core/holdcancel.py`:
     `cancel_hold`, `check_and_expire_checkouts`).
   - RESTOCK on refund (`refund_checkout`).

4. **Race safety** — `check_stock_available` and `compute_sku_exposure` run
   inside `immediate_session`, with `FOR UPDATE` on Postgres. Proven by a
   genuine threaded last-unit test, not a simulated one.

5. **`_inventory_sync_loop`** in `server.py`, mirroring `_campaign_expiry_loop`:
   platform truth overlaid with outstanding reservations. Drift below
   reservations blocks new sales, raises `#alerts` via `notifier.sync_alert`,
   and surfaces on the dashboard. It never goes negative, and never
   auto-cancels a paid order. Failed write-backs land in a DLQ.

6. **Merchandising inputs** — low-stock thresholds, and stalled-SKU detection
   (`core/campaigns.py::detect_stalled_skus`) excludes out-of-stock SKUs so
   the growth loop stops drafting campaigns for things that cannot ship.

7. **`run_sweepers` deleted** (DEF-10).

## Oversell block (DECISION-047)

A **pre-compiler gate** in `create_checkout_from_policy`, raised before the
`CompilerContext` is constructed, as `CommerceError("inventory.insufficient_stock")`.

The rationale is soundness, not convenience. A transcript-row check would
force the offline verifier to trust an unsigned point-in-time quantity,
breaking replayability. So: `core/compiler.py` stays byte-identical, check 10
is left alone (Q-046 — no-op, DEF-12), and the auditor escape hatch, if one is
ever needed, is an optional attested PoAI pre-check section — never a
transcript row. `tracked=false` SKUs bypass the gate entirely.

## Invariants

- Reserved quantity nets to zero at every terminal state.
- Available stock is never negative and is never fabricated: unmanaged
  (`stock: None`) is distinct from zero at every layer.
- No inventory mutation happens outside a ledger entry.
- Every helper called from inside a transaction receives the caller's
  session. Opening a nested `Session` rolls the caller back under SQLite's
  StaticPool — see `core/settings_overlay.py::effective_settings`.

## Explicitly remaining

- Multi-location / per-warehouse stock: one quantity per `(merchant_id, sku)`
  in this stage. Splitting it later is a migration, not a redesign.
- Backorders and pre-orders: out of scope; `tracked=false` is the escape.
- Write-back beyond WooCommerce: the DLQ and the adapter capability exist,
  but only Woo is proven live here.

## DONE WHEN

- `stock: 1` → the second concurrent buyer is refused with
  `inventory.insufficient_stock`; a threaded race proves exactly one winner.
- Capture decrements; cancel, expiry, and failure restore; refund restocks —
  each idempotent under both reconciliation paths.
- `verify_inventory_balances`: reserved nets to zero at terminal states.
- WooCommerce write-back observed live; a forced failure lands in the DLQ
  with an alert.
- External drift below reservations blocks new sales and alerts, never
  goes negative, never auto-cancels a paid order.
- `tracked=false` bypasses entirely.
- PoAI goldens regenerated if `goods` gained `stock_checked` /
  `available_at_decision`; `openstore-verify` green.
- Migration `0012` verified upgrade **and** downgrade against a database
  stamped `0011`.
- `uv run pytest -q` green with no pre-existing-failure carve-out;
  `uv run mypy src/`, `uv run ruff check src/ tests/`, and
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0) all clean.
- New reason codes registered in `REGISTRY.json` **and** in
  `tests/test_registry_compliance.py`'s expected set — the closed set is
  asserted in two places.
- Commit: `stage(26): inventory`, describing only what the commit contains.
