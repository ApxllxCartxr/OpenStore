# Stage 05 — Razorpay driver + webhooks + hold/cancel

Self-contained per PRD v3.0 Part 10. You do not need other stage files.

## READ FIRST
- `AGENTS.md` — especially R0.7 (the `[verify-at-build]` protocol) and the unforgivable act.
- `OPENSTORE_PRD_v3.md` §3.4 (INV-4, INV-6, INV-7), §3.7 (hold/cancel machine, cancel
  token), Part 4 (Razorpay pinned constants), DECISIONS §11.1.1–3.
- `REGISTRY.json` — do not edit.
- Stage 2 artifacts: ledger, idempotency, PspIntent, hold/cancel state machine.

## SCOPE (closed)
- `src/openstore/psp/razorpay_driver.py`
- `src/openstore/core/holdcancel.py` (webhook wiring + workers only)
- `src/openstore/surfaces/wellknown.py` (only if a route mount is required)
- `scripts/capture_constants.py`
- `tests/stage05/**` (incl. webhook replay tests against captured fixtures)
- `GOLDEN/razorpay/*.json` (captured live bodies — see S5.1)
- `OPEN_QUESTIONS.md`

## BUILD

### S5.1 `[verify-at-build]` constant capture — FIRST, before any driver logic
Run `scripts/capture_constants.py` against live Razorpay **test-mode** and pin, as
constants with source comments (URL + capture date): the duplicate-`reference_id` create
error code string (DECISIONS §11.1.3), the cancel-already-paid HTTP 400 body shape
(§11.1.2), and the real webhook body shapes for `payment_link.paid`,
`payment_link.cancelled`, `payment_link.partially_paid`, `payment.failed`. Store captured
bodies in `GOLDEN/razorpay/`. If test-mode is unreachable → OPEN_QUESTIONS.md and STOP
(R0.7). Never invent these from docs or memory.

### S5.2 Driver — `razorpay_driver.py`
Payment-link create/fetch/cancel/refund via the `razorpay-python` SDK. Create follows
INV-4 exactly: `BEGIN` IdempotencyRecord(IN_FLIGHT) + PspIntent(PENDING) → `COMMIT` →
call Razorpay with `reference_id = checkout_id` → `BEGIN` PspIntent→SUCCEEDED + Order +
ledger CAPTURE-pending + IdempotencyRecord→COMPLETED → `COMMIT`. Duplicate-create is
caught by the pinned error code ONLY (never bare `Exception`) and recovers by adopting
the existing link via fetch-all filtered on `reference_id`. Payment links return
out-of-band to the human — the agent never relays them.

### S5.3 Webhook endpoint + worker (INV-6)
Signature verify on the RAW body (HMAC-SHA256, `hmac.compare_digest`) → persist raw event →
return 200 → process in a worker. Dedupe on `X-Razorpay-Event-Id` (fallback
`sha256(raw_body)`). Explicit allowed-transition table; terminal states
`{PAID, REFUNDED, FAILED}` absorbing. Non-2xx only when a retry is wanted; after N
attempts → dead-letter table + alert to `#alerts`. Handled events: the four from S5.1.

### S5.4 Reconciliation sweeper (INV-7)
Polls Razorpay for orders in non-terminal states aged 10m–7d; the PSP answer is the source
of truth for money. Emits `reconciliation_drift_total` metric; non-zero → `#alerts`.

### S5.5 Hold/cancel live path
Hold release worker runs every 30s, moving lapsed `HELD` → `RELEASED`. Cancel per §3.7:
`POST /hold/{cancel_token}/cancel` (unauthenticated capability, 32-byte
`secrets.token_urlsafe`, single-use, expires with the hold) → attempt
`POST /payment_links/{id}/cancel` → on the pinned already-paid 400, idempotent refund +
`REFUND` ledger entry instead of `RELEASE`. Fulfilment keys off `RELEASED`, never
`ORDER_CREATED`. Every transition traced to `#money-trace` with `trace_id`.

## MUST NOT
- No live-mode keys — test mode only; assert key prefix at startup and hard-fail otherwise.
- No money movement without the INV-4 dual-write ordering.
- No agents, no MCP tools, no PoAI changes, no campaigns.
- Do not catch `Exception` around Razorpay calls; catch pinned codes only.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage05/ -q` → 0 failures, incl. webhook replay from GOLDEN/razorpay
  fixtures, duplicate-delivery, out-of-order, and crash-mid-create recovery tests
- `GOLDEN/razorpay/` contains captured bodies for all four events + both pinned error
  shapes, each with a source comment
- Kill-recovery proof: a test that simulates a crash after the first INV-4 commit and
  asserts the recovery worker adopts the existing link without a second create
- `ruff check src tests` and `mypy src` → clean
- Human-verified smoke (report to operator): end-to-end test-mode payment link paid via
  cloudflared tunnel, order lands `HELD`, cancel link cancels, ledger shows RELEASE.

## COMMIT GATE
`stage(05): razorpay`
