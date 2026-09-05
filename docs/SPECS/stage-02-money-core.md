# Stage 02 — Money-path core (INV-1…12 + INV-5a as library code)

Self-contained per PRD v3.0 Part 10. NOTE: this stage is substantially REMEDIATED already
(commit 88c6598). If that work is present, this stage is a verification + gap-fill pass:
confirm each invariant is enforced, add the ledger golden vectors if absent, and close any
residual gaps. Do not rewrite working remediated code.

## READ FIRST
- `AGENTS.md`.
- `OPENSTORE_PRD_v3.md` §3.4 (all invariants incl. INV-5a), §3.7, Part 7.
- `REGISTRY.json` (closed sets), existing `src/openstore/core/ledger.py`,
  `src/openstore/core/idempotency.py`, `src/openstore/core/holdcancel.py`.

## SCOPE (closed)
- `src/openstore/core/ledger.py`, `idempotency.py`, `holdcancel.py`, `aal.py`,
  `compiler.py` (core decision path only)
- `src/openstore/core/models.py` (or wherever SQLModel tables live: Checkout, Order,
  LedgerEntry, IdempotencyRecord, PspIntent, AuditLogEntry, CatalogAttestation)
- `scripts/make_ledger_goldens.py`
- `GOLDEN/ledger/{reserve_capture,reserve_release,reserve_capture_refund}.json`
- `tests/stage02/**`, `tests/test_ledger_golden.py`, `tests/test_spend_cap.py`,
  `tests/test_compiler_check0.py`, `tests/test_registry_compliance.py`
- `OPEN_QUESTIONS.md`

## BUILD
### S2.1 Invariant enforcement
Wire each of INV-1…12 + INV-5a into library code per §3.4. Each invariant gets a named
test proving it holds and a named adversarial test proving the exploit it names is caught
(cart-swap for INV-1, forged-token for INV-2, key-reuse for INV-3, crash-mid-call for
INV-4, non-reversal for INV-5/5a, duplicate/out-of-order webhook for INV-6, lost-webhook
reconciliation for INV-7, expired-checkout for INV-8, alg-confusion for INV-9,
anonymous-internal-route for INV-10, TOCTOU race for INV-11, missing-attribution/PII-leak
for INV-12).

### S2.2 Ledger (Q-002 — INV-5a)
`verify_ledger_balances(reference_id)` returns True iff escrow accounts (`customer_hold`,
`merchant_pending`) net to zero per `reference_id` at terminal states AND economic
accounts (`merchant_revenue`, `platform`) are non-negative. Double-entry, append-only,
RESERVE/CAPTURE/RELEASE/REFUND. Never `UPDATE` a ledger row.

### S2.3 Ledger golden vectors
`scripts/make_ledger_goldens.py` generates the three lifecycle vectors.
`tests/test_ledger_golden.py` replays each and asserts byte-identical outcome and the
INV-5a predicate result.

### S2.4 Per-policy cumulative spend (Q-003 — §3.2c)
`compute_policy_spend` / `check_spend_cap` scope via
`LedgerEntry.reference_id → Checkout.policy_id` join; sum CAPTURE minus REFUND/RELEASE on
the policy under evaluation only. No `policy_id`/`merchant_id` columns on `LedgerEntry`.
`tests/test_spend_cap.py` proves cross-policy isolation.

### S2.5 Check 0 (Q-004 — §3.2)
`compile_decision` runs `human_authority_present` before check 1 when
`policy.no_human_authority == False`, returning
`CompilerResult(allowed=False, reason_code="assertion_required")` — never raising. No
`ValueError` on the missing-assertion path. `tests/test_compiler_check0.py` covers deny +
proceed.

### S2.6 Registry compliance
`tests/test_registry_compliance.py` enforces REGISTRY.json both directions.

## MUST NOT
- No WebAuthn RP internals (Stage 3), no PoAI assembly (Stage 4), no Razorpay calls
  (Stage 5), no MCP/routes (Stage 6), no agents (7), no campaigns (8).
- Do not change REGISTRY.json.

## DONE WHEN (all exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage02/ tests/test_ledger_golden.py tests/test_spend_cap.py tests/test_compiler_check0.py tests/test_registry_compliance.py -q` → 0 failures
- All fourteen invariant adversarial tests pass
- `ruff check src tests` and `mypy src` → clean

## COMMIT GATE
`stage(02): money-core`
