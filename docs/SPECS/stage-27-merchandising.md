# Stage 27 — Cross-sell and up-sell

Slug: `merchandising`. Goal: real suggestions for every adapter, merchant- or
agent-authored, never an out-of-stock or hallucinated SKU.

Status: **not started**, and blocked on Stage 26 — `suggest_for_cart` filters
on sellability, which does not exist until inventory does. See
`docs/PLAN-stage-26-27.md`. DECISION-048 is cited below but is not yet
recorded in `docs/DECISIONS.md`.

## Scope

1. **Schema — migration `0013`** — `merchandising_rules`, mirroring
   `Campaign`: kind `CROSS_SELL | UPGRADE | BUNDLE`, the full state machine
   (`DRAFT | PENDING_APPROVAL | ACTIVE | PAUSED | EXPIRED | REJECTED`), the
   approval assertion fields, and a nullable `campaign_id`. Existence-guarded,
   written against a database stamped `0012`.

2. **`core/merchandising.py`** — `suggest_for_cart`, fully deterministic:
   ACTIVE rules first, then adapter-native fields, then `related_skus`.
   In-cart SKUs excluded, results deduped, Stage-26 sellability filter
   applied, capped. **Selection is LLM-free**; an LLM may word the copy and
   nothing else. Order is stable and pinned by a golden test.

3. **UPGRADE** returns an honest price delta and never mutates the cart.

4. **BUNDLE** references a `Campaign` row for its discount (DECISION-048).
   There is no second discount path: compiler check 12 recomputes it exactly
   as it does today, and R0.8 stays intact. A paused or expired bundle is
   silently inapplicable rather than an error.

5. **`draft_merchandising_rules`** — fed only by
   `core/campaigns.py::get_analytics_view` plus aggregate co-occurrence, so
   INV-14 is preserved: the agent never sees raw orders, buyer identities, or
   payment data. A draft activates solely on a passkey assertion bound to
   `{"mode": "merchandising", "rule_id": ...}`, with replay binding — an
   assertion for one rule must fail closed on another.

6. **Surfaces** — `/merchant/merchandising` (author, review, why-stat);
   `/chat` renders "Goes well with" and "Upgrade to"; the Discord cross-sell
   rename (DEF-13) plus a real up-sell embed; an MCP `suggest_related` tool
   with conformance fixtures regenerated and the README tool count swept.

DEF-6 closes for real adapters via native fields: WooCommerce
`cross_sell_ids`/`upsell_ids`, Magento and BigCommerce `related_products`.

## Invariants

- A suggestion is always a SKU the merchant can actually sell right now:
  it exists in the catalog, it is not in the cart, and Stage 26 says it is
  sellable.
- Suggestion selection is deterministic and reproducible from stored state.
  The same cart against the same rules and stock yields the same ordered list.
- No agent-authored rule takes effect without a passkey assertion bound to
  that specific rule.
- Exactly one discount path. If a bundle's discount is not recomputed by
  check 12, it does not exist.

## Explicitly remaining

- Personalisation from buyer history: deliberately out of scope — INV-14
  keeps the agent on aggregates only, and per-buyer targeting would need a
  different privacy argument than this stage makes.
- Ranking by measured conversion: the why-stat panel reports co-occurrence,
  not causal lift. Closing that needs the outcome-feedback loop pointed at
  merchandising rules, which is its own stage.

## Open question carried in

Stage 25 put `related_source` on the normalized item, and it now appears in
MCP `get_product` output. This stage adds more provenance to the same path.
Decide before freezing it whether internal provenance belongs on a published
wire shape at all — see `docs/PLAN-stage-26-27.md`, prerequisite 3.

## DONE WHEN

- WooCommerce native cross-sell/up-sell appears with zero manual configuration.
- An out-of-stock SKU is never suggested.
- An agent draft cannot go live without a passkey; a cross-rule assertion
  replay fails closed.
- A bundle discount is recomputed by check 12 and present in evidence; a
  paused or expired bundle is silently inapplicable.
- Suggestion order is deterministic under a golden test.
- Conformance fixtures regenerated **in this commit**, and the MCP tool count
  updated everywhere it is stated.
- Migration `0013` verified upgrade and downgrade against a database stamped
  `0012`.
- `uv run pytest -q` green with no pre-existing-failure carve-out;
  `uv run mypy src/`, `uv run ruff check src/ tests/`, and
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0) all clean.
- New reason codes registered in `REGISTRY.json` **and** in
  `tests/test_registry_compliance.py`'s expected set.
- Commit: `stage(27): merchandising`, describing only what the commit contains.
