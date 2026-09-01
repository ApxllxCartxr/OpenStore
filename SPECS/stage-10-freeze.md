# Stage 10 — Demo store #2 (chai) + red-team + sentinel suite + feature freeze

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md`.
- `OPENSTORE_PRD_v3.md` §1.3 (import firewall), Part 7 (closed sets), Part 10 (cuts),
  Part 12 (track-bar compliance).
- All prior stages complete and green.

## SCOPE (closed)
- `chai.yaml` (second demo merchant config)
- `tests/stage10/**`, `tests/sentinel/**`, red-team suite `tests/redteam/**`
- `scripts/redteam.py` (adversarial driver)
- `OPEN_QUESTIONS.md`

## BUILD

### S10.1 Second merchant — multi-tenancy by install
`openstore serve chai.yaml` on a second port, same package, zero code changes. Prove
isolation: separate DB, separate per-merchant ES256 PoAI keypair with namespaced `kid`
(DECISIONS §11.1.10), chai's `/.well-known/poai-jwks.json` serves only chai keys, and the
verifier selects by `kid` from a JWKS directory. A cross-merchant test asserts one
merchant's policies/budgets/bundles never leak into the other.

### S10.2 Red-team suite — `tests/redteam/`
Adversarial proofs, each a named failing-then-passing test: cart-swap (INV-1), forged
token (INV-2), idempotency key reuse with different payload (INV-3), crash-mid-PSP-call
(INV-4), ledger non-reversal (INV-5/5a), duplicate + out-of-order webhooks (INV-6), lost
webhook reconciliation drift (INV-7), expired checkout confirm (INV-8), alg confusion
(INV-9), anonymous internal route (INV-10), concurrent over-cap confirm (INV-11 TOCTOU),
PII in traces (INV-12), unsigned/out-of-window campaign offer (INV-13), raw-PII to
campaign agent (INV-14), agent compiler bypass (R0.9), agent-held key (R0.10),
prompt-injection campaign copy.

### S10.3 Sentinel suite — `tests/sentinel/`
Route-table snapshot, enum exhaustiveness vs REGISTRY.json, schema snapshots, port/config
pins, import firewall. Fails the build on any drift.

### S10.4 Feature freeze
`python scripts/registry_diff.py` prints nothing; full suite green; no open
OPEN_QUESTIONS without RESOLUTION; cuts in Part 10 remain cut (no delegation chains, no
buyer swarm feature, no AXO loop, no UAP adapter). Tag the freeze.

## MUST NOT
- No new features, no new identifiers, no new config keys. Freeze means freeze.
- Do not weaken a red-team test to make it pass — a failing red-team test is a bug in the
  product, not the test; surface it via OPEN_QUESTIONS.md.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest -q` (full suite incl. stage10, redteam, sentinel) → 0 failures
- Two merchants serve concurrently; cross-merchant isolation test passes
- All sentinel + red-team tests pass
- `ruff check src tests` and `mypy src` → clean
- Human-verified (report to operator): two-merchant demo + full Part 12 run on gelateria.

## COMMIT GATE
`stage(10): freeze`
