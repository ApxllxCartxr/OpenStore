# Stage 08 — Campaign / Offer Orchestrator + Campaign Studio

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md` — R0.9/R0.10.
- `OPENSTORE_PRD_v3.md` Part 9 (§9.1–9.7, normative), §3.2b (check 12), INV-13, INV-14,
  §7 (`campaign_states` closed set).
- `REGISTRY.json` — do not edit.
- Stage 6 surfaces (campaign feed, `list_campaigns`/`get_campaign`), Stage 7 llm.py.

## SCOPE (closed)
- `src/openstore/agents/campaign_agent.py`
- `src/openstore/core/` — campaign store + deterministic validator + analytics view ONLY
  (new module `src/openstore/core/campaigns.py`)
- `src/openstore/surfaces/studio.py` (Campaign Studio) +
  `templates/campaign_studio.html`
- `tests/stage08/**` (incl. INV-13/INV-14 adversarial tests)
- `OPEN_QUESTIONS.md`

## BUILD

### S8.1 Analytics view (INV-14)
Deterministic SQL in `core/campaigns.py` producing the derived, aggregated view ONLY:
`sku`, `units_sold_7d`, `units_sold_30d`, `gross_minor_30d`, `attach_rate`,
`last_sold_at`. The agent NEVER reads raw orders, buyer identities, or payment data — a
test asserts the view's column set exactly.

### S8.2 Draft (R0.9)
The LLM proposes a `Campaign` per the §9.3 closed schema (`campaign_id`,
`campaign_version`, `merchant_id`, `title`, `rationale`, `offer_terms{discount_bps,
applies_to_skus, starts_at, ends_at}`, `source_signals{top_skus, slow_skus,
calendar_event?, headroom_minor}`, `draft_digest`, `approval`, `state`,
`merchant_signature`). Output is a DRAFT only.

### S8.3 Deterministic validator (no LLM)
Every SKU exists; `discount_bps` within `[campaign_min_bps, campaign_max_bps]`; window
well-formed; no SKU on any active policy's `blocked_skus` unless the campaign is
explicitly excluded from those policies; projected price integer paise; `title`/`rationale`
pass the catalog content rules (no prompt-injection copy). Any breach → fail loud (R0.5).

### S8.4 Approve / publish
Campaign Studio (`campaign_studio.html`, lean single-file per Part 4) lists drafts; the
merchant approves via one-tap WebAuthn → sign → `ACTIVE`; reject → `REJECTED`. Only
`ACTIVE` campaigns publish to `/.well-known/agent-campaigns.json` (signed, INV-13),
`/agent/campaigns`, and catalog `offers[]`. Lifecycle per §9.4:
`DRAFT → PENDING_APPROVAL → ACTIVE → (PAUSED | EXPIRED)`; `PENDING_APPROVAL → REJECTED`;
sweeper sets `EXPIRED` at `ends_at`. All transitions traced to `#merchant-trace`.

### S8.5 Compiler check 12 wiring
A cart line referencing `campaign_id` triggers `campaign_validity` (§3.2b); discount
computed server-side (R0.8) before checks 9–11; applied campaign recorded for PoAI
§3.3.9. Max `campaign_max_active` concurrent ACTIVE campaigns enforced.

## MUST NOT
- No publishing without a WebAuthn approval — adversarial test REQUIRED.
- No unsigned or out-of-window offer may appear in any feed (INV-13) — adversarial test.
- The agent MUST NOT receive raw PII (INV-14) — adversarial test.
- Do not change the §9.3 schema, the `campaign_states` set, or REGISTRY.json.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage08/ -q` → 0 failures, incl. the three adversarial tests above and a
  check-12 integration test (inactive campaign → `policy.campaign_inactive`; out-of-window
  → `policy.campaign_outside_window`)
- Human-verified smoke (report to operator): orchestrator drafts a campaign from seeded
  analytics, merchant approves in Campaign Studio, signed offer appears in the feed and in
  `list_campaigns`.
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(08): campaigns`
