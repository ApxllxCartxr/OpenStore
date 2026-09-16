# Stage 25 — Catalog adapter SDK and platform adapters

Slug: `adapter-sdk`. Goal: any supported platform (or none) connects; YAML is
one adapter among many; silent-₹0 pricing is impossible.

Scope: `surfaces/adapters/` (`base.py` Protocol + `AdapterCapability`,
`errors.py` `AdapterError(CommerceError)`, `registry.py` mirroring
`agents/llm.py::register_provider`, unified `cache.py`, `normalize.py`);
`catalog_source` + `stock_source` discriminated unions resolving
**independently** (stock defaults to catalog adapter when unset; shared
`StrictModel` base carrying `extra="forbid"`); legacy `catalog_path`/`shopify`
normalization (DEF-11, `catalog.adapter_multiple_sources`); fail-loud pricing
(DEF-1, `prce_minor` regression test) and merchant currency (DEF-5);
`load_catalog` → thin dispatch; six adapters (CSV/Sheets, WooCommerce,
BigCommerce, Magento first; Wix, Zoho last — both need the Stage-24 OAuth
install flow); Shopify migration without behaviour change; Q-047 (`e6` gap,
fix if trivial).

Normalized shape freezes once: `stock: int | None` (`None` = unmanaged),
`related_source` provenance. Back-compat mandatory: `configs/*.yaml` and
stage-18 tests pass untouched (import/exception-type edits only).
Unicommerce is **25b**: stock-only adapter composed with a storefront catalog
source. Zoho data-centre (`.in`/`.com`/`.eu`) is a config field, never inferred.

Decisions/questions: DECISION-046 (SDK + independent resolution),
Q-045 (`catalog_attestations_valid`), Q-046 (check-10 no-op, DEF-12, Q only).

## DONE WHEN

- Six adapters return byte-identical normalized shape; `compute_catalog_digest`
  stable across adapters for equivalent data.
- `prce_minor` typo fails loud naming the SKU; no zero-priced item served/attested.
- `catalog_source`/`stock_source` resolve independently — proven with YAML
  catalog + WooCommerce stock; legacy blocks load unchanged; dual catalog
  sources fail loud.
- `/merchant/setup` connects/tests a live WooCommerce store and imports its catalog end to end.
- Stage-18 tests green with import-level edits only. PoAI goldens regenerated
  if `goods` gained fields; verifier green.
- `uv run pytest -q`, `uv run mypy src/`, `uv run ruff check src/ tests/`,
  `uv run python scripts/registry_diff.py` (prints nothing, exit 0).
- Commit: `stage(25): adapter-sdk`.

