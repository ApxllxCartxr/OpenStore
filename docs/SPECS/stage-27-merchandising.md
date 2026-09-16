# Stage 27 — Cross-sell and up-sell

Slug: `merchandising`. Goal: real suggestions for every adapter, merchant- or
agent-authored, never an out-of-stock or hallucinated SKU.

Scope: migration `0013` (`merchandising_rules` mirroring `Campaign`:
`CROSS_SELL | UPGRADE | BUNDLE`, full state machine, approval assertion
fields, nullable `campaign_id`); deterministic `core/merchandising.py`
(`suggest_for_cart`: ACTIVE rules → adapter-native fields → `related_skus`;
exclude in-cart, dedupe, Stage-26 sellability filter, cap; LLM-free selection,
LLM words copy only); UPGRADE returns honest price delta, never mutates the
cart; BUNDLE references a Campaign row for discounts (no second discount path
— check 12 recompute, R0.8 intact); `draft_merchandising_rules` fed only by
`get_analytics_view` + aggregate co-occurrence (INV-14 preserved), activating
solely on a `{"mode":"merchandising","rule_id":...}` passkey assertion with
replay binding; `/merchant/merchandising` (author/review/why-stat);
`/chat` "Goes well with"/"Upgrade to"; Discord cross-sell rename (DEF-13)
plus a real up-sell embed; MCP `suggest_related` tool + conformance regen +
README tool-count sweep.

Decisions: DECISION-048 (bundles reuse campaign discounts). DEF-6 closes for
real adapters via native fields (Woo `cross_sell_ids`/`upsell_ids`,
Magento/BigCommerce `related_products`).

## DONE WHEN

- WooCommerce native cross-sell/up-sell appears with zero manual configuration.
- Out-of-stock SKU never suggested. Agent draft cannot go live without a
  passkey; cross-rule assertion replay fails closed.
- Bundle discount recomputed by check 12 and present in evidence; paused/expired
  bundle silently inapplicable. Suggestion order deterministic under golden test.
- Conformance fixtures regenerated; tool count updated everywhere.
- `uv run pytest -q`, `uv run mypy src/`, `uv run ruff check src/ tests/`,
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0).
- Commit: `stage(27): merchandising`.

