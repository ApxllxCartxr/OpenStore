# Stage 26 — Inventory and stock monitoring

Slug: `inventory`. Goal: real stock, no oversell, honest drift handling.

Scope: migration `0012` (`inventory_items` unique on `(merchant_id, sku)`
with `tracked` flag; `inventory_ledger_entries` mirroring `core/ledger.py`
with unique `idempotency_key`); `core/inventory.py` (ledger idioms verbatim,
`verify_inventory_balances`); hooks (RESERVE in `create_checkout_from_policy`
→ `initiate_hold` same `immediate_session`; COMMIT beside capture in
`_apply_payment_link_paid`; RELEASE on cancel/fail/expire; RESTOCK on refund —
all idempotent under both reconciliation re-invocations); race safety
(`check_stock_available` + `compute_sku_exposure` in `immediate_session`,
`FOR UPDATE` on Postgres, genuine threaded last-unit test); `_inventory_sync_loop`
mirroring `_campaign_expiry_loop` (platform truth overlaid with reserved;
drift blocks + `#alerts` + dashboard, never negative, never auto-cancels paid;
write-back DLQ); low-stock thresholds + `detect_stalled_skus` exclusion;
delete `run_sweepers` (DEF-10).

Oversell block (DECISION-047, confirmed): **pre-compiler gate** in
`create_checkout_from_policy` before `CompilerContext` construction, raising
`CommerceError("inventory.insufficient_stock")`. Rationale is soundness: a
transcript-row check would force the offline verifier to trust an unsigned
point-in-time quantity, breaking replayability. `core/compiler.py` stays
byte-identical; check 10 left alone; auditor escape hatch (if ever needed) is
an optional attested PoAI pre-check section, never a transcript row.
`tracked=false` SKUs bypass the gate.

## DONE WHEN

- `stock: 1` → second concurrent buyer refused with
  `inventory.insufficient_stock`; threaded race proves exactly one winner.
- Capture decrements; cancel/expiry/failure restore; refund restocks —
  each idempotent under both reconciliation paths.
- `verify_inventory_balances`: reserved nets to zero at terminal states.
- WooCommerce write-back observed; forced failure lands in DLQ with alert.
- External drift below reservations blocks new sales + alerts, never negative.
- `tracked=false` bypasses entirely. PoAI goldens regenerated if `goods`
  gained `stock_checked`/`available_at_decision`; verifier green.
- `uv run pytest -q`, `uv run mypy src/`, `uv run ruff check src/ tests/`,
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0);
  `mutmut` on `core/` changes.
- Commit: `stage(26): inventory`.

