# Stage 09 — Demo store #1 (gelateria) end-to-end via the public install surface

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md`.
- `OPENSTORE_PRD_v3.md` §1.2 (install contract), Part 12 (demo script), §3.9 (manifest).
- All prior stages complete and green.

## SCOPE (closed)
- `gelateria.yaml` (demo merchant config: catalog, tags, Razorpay test keys via `.env`)
- `scripts/demo_seed.py` (seeds analytics history for the campaign beat)
- `tests/stage09/**` (end-to-end install test)
- `OPEN_QUESTIONS.md`

## BUILD

### S9.1 Fresh-install end-to-end
Prove the §1.2 contract exactly as a merchant would experience it: clean venv →
`uv sync --locked` → `openstore init --merchant "Gelateria Milano" --currency INR` →
fill `gelateria.yaml` (SKUs in integer paise + tags) + Razorpay test keys →
`openstore serve gelateria.yaml` → assert manifests, catalog feed, MCP endpoint, and
signed campaign feed all respond. A test automates this install path end-to-end.

### S9.2 Demo script dry-run (Part 12 beats 0–6)
Script the full demo as an executable runbook: authorize-once (IntentPolicy ₹2,000/mo,
≤₹500/txn, Gelateria, vegan) → agent shops → bounded money (HELD → RELEASED) → the
agentic failure (`policy.tag_violation` → negotiation → `NO_COMPLIANT_PATH` → amendment →
one-tap approval → ALLOW) → over-cap fail-loud (`policy.spend_per_tx_exceeded`) → campaign
beat (seeded analytics → draft → approve → `list_campaigns` discovery) → dispute
(`openstore-verify` offline on an exported bundle; tamper one digit in `amount_minor` →
verifier names the broken section/link).

### S9.3 Razorpay live test-mode confirmation
`[verify-at-build]` constants from Stage 5 re-confirmed against live test-mode during the
dry-run; any drift → OPEN_QUESTIONS.md and stop (R0.7).

## MUST NOT
- No new features, no new identifiers, no spec changes. This stage assembles and proves.
- No live-mode Razorpay keys.
- Do not self-certify the human smoke steps — report them for operator verification.

## DONE WHEN (all exit 0)
- `pytest tests/stage09/ -q` → 0 failures (incl. the fresh-install test)
- The Part 12 runbook executes beats 0–6 end-to-end against test-mode without manual code
  edits
- `python -m openstore.verify <exported bundle>` offline → exit 0; tampered variant →
  exit 1 naming the broken link
- Human-verified smoke (report to operator): full Part 12 demo witnessed end-to-end.
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(09): demo-1`
