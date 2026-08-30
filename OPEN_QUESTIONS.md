# OpenStore — Open Questions
# Append-only. One entry per ambiguity. Never edit a resolved entry.

## Q-001 | stage: 00 | date: 2026-08-30T11:08:00Z
- What is ambiguous: Initial project structure and dependency decisions not fully specified in PRD
- Options considered: 
- Blocked since: 2026-08-30T11:08:00Z
- RESOLUTION:
## Q-002 | stage: 02 | date: 2026-08-30T00:00:00Z
- What is ambiguous: verify_ledger_balances asserts `all(account balance == 0)` per reference_id
  (src/openstore/core/ledger.py). Given the recording convention (both double-entry legs stored with
  positive amount_minor), this is always False for any completed lifecycle: after a RESERVE+CAPTURE
  the `merchant_revenue` and `platform` accounts end positive, yet the ledger is internally consistent.
  There is no PRD text defining the exact balance invariant to assert.
- Options considered: (a) assert sum across all accounts == 0 after re-signing legs by
  debit/credit; (b) assert per-account non-negativity; (c) reconcile escrow accounts
  (customer_hold, merchant_pending) to zero at every terminal state, treating economic accounts
  (merchant_revenue, platform) separately; (d) pin a canonical GOLDEN vector for each lifecycle.
- Blocked since: 2026-08-30T00:00:00Z

## Q-003 | stage: 02 | date: 2026-08-30T00:00:00Z
- What is ambiguous: Cumulative spend (spend check 11 and LedgerEntry.account == "merchant_revenue"
  aggregation in create_checkout / check_spend_cap) is not scoped to policy_id or merchant_id.
  LedgerEntry has no merchant_id/policy_id column, so one checkout's spent amount counts against every
  other policy's budget. Single-tenant-per-sidecar mitigates the merchant axis but not the
  multi-policy case. PRD is silent on whether max_spend_total is per-policy or per-merchant.
- Options considered: (a) add policy_id (and merchant_id) columns to LedgerEntry and scope the sums;
  (b) join ledger entries to Checkout via reference_id -> Checkout.policy_id and scope that way;
  (c) treat all merchant_revenue as one aggregate budget (current behavior) and document that
  max_spend_total is process-wide.
- Blocked since: 2026-08-30T00:00:00Z

## Q-004 | stage: 02 | date: 2026-08-30T00:00:00Z
- What is ambiguous: When a policy requires human authority (no_human_authority == False) but no valid
  WebAuthn assertion is provided, compile_decision() reaches compute_aal_level() which raises
  ValueError (src/openstore/core/holdcancel.py:64). There is no closed-set reason code representing
  "assertion required" denial, and compile_decision's contract returns CompilerResult (allowed=False)
  for business rejections rather than raising. R0.5 requires every rejection carry a closed-set reason
  code, so it cannot be represented today.
- Options considered: (a) add a reason code (e.g. assertion_required) to REGISTRY.json reason_codes -
  blocked because AGENTS.md forbids adding identifiers to REGISTRY.json without a prior OPEN_QUESTION
  RESOLUTION; (b) reuse policy.no_human_authority - semantically wrong; (c) have compile_decision raise
  a typed DomainError on the missing-assertion path instead of returning a rejection.
- Blocked since: 2026-08-30T00:00:00Z
