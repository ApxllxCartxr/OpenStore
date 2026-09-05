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

### S10.5 Sidecar integration contract (SID-1 … SID-7)
Authorized by Q-010 (PRD §1.4). Implement and test all seven SIDs:
- SID-1 deployment topology: `public_base_url` config key; `init --deployment
  same-origin|subdomain` (subdomain REQUIRES --public-base-url, fail fast);
  public URL derivation (same-origin from request, subdomain from public_base_url).
- SID-2 health/readiness: `/health/live` 200 always; `/health/ready` 200 only when
  config+DB+schema+test-mode-Razorpay+signing keys all present, else 503 with
  `health.<check>.<status>` reason codes; gated agent/money routes 503 while not ready.
- SID-3 startup ordering: serve = config → `apply_migrations()` → keys → workers →
  HTTP; schema-ready flag prevents double-migration.
- SID-4 crash/restart: two-process kill/restart op proves no double-pay and no orphaned
  HELD checkout (`tests/stage10/test_sid4_kill_restart.py`).
- SID-5 origin security: `public_base_url` host MUST equal `webauthn.rp_id` (app-build
  failure on mismatch); CORS pinned to merchant origin.
- SID-6 versioning: `__version__` = pyproject version via `importlib.metadata.version`;
  exposed on agent-card manifest and FastAPI app.
- SID-7 metrics: `/internal/metrics` hand-rolled Prometheus (no new dep, Q-011) with
  health_ready, checkout_hold_state, ledger_balance_minor, reconciliation_drift_total.
Tests: `tests/stage10/test_sid_integration.py`, `tests/stage10/test_sid4_kill_restart.py`.

### S10.6 Headless execution of PRD Part 12 steps 3–5 (B6 / Q-013)
The unattended run supersedes PRD Part 12 steps 3–5 as follows (Q-013, option b):
step 3 (bounded money) and step 4 (agentic failure) are exercised **headlessly** by
direct MCP tool calls (`checkout_initiate`/`checkout_confirm`, policy deny/recover)
using GOLDEN WebAuthn fixtures — no live Discord, no live Razorpay. Step 5 (campaign
beat) is exercised via `list_campaigns` on a seeded feed. The live-Discord-bot and
live-Razorpay-payment variants of these steps move to the operator-verified list below.

## PENDING-HUMAN-VERIFICATION (report to operator; never self-certified)
- Live Discord bot completes a full policy-aware purchase (Part 12 step 2 live).
- Live Razorpay payment-link click + webhook round-trip (step 3 live payment).
- Live Passkey/WebAuthn ceremony on a real authenticator (incl. the three `webauthn_*`
  MCP wrappers whose wiring predates the INV-10 signature change — see `type: ignore[call-arg]`
  in `surfaces/mcp_server.py`).
- Two-merchant concurrent demo (gelateria + chai) with live browsers.
- Full PRD Part 12 track-bar compliance run on a real store.

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
