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


- RESOLUTION (2026-08-30): Closed by PRD v3.0 §0.3 — structure normative in §1.3, stack in Part 4, STEP ZERO is the first action. No code change.

- RESOLUTION (2026-08-30): Option (c) + (d). Normative as INV-5a in PRD v3.0 §3.4: escrow
  accounts (customer_hold, merchant_pending) net to zero per reference_id at every terminal
  state; economic accounts (merchant_revenue, platform) are excluded from the zero-invariant
  and MUST be non-negative. ledger_accounts taxonomy added to REGISTRY.json. Three golden
  vectors pinned in GOLDEN/ledger/{reserve_capture,reserve_release,reserve_capture_refund}.json.
  Fix src/openstore/core/ledger.py verify_ledger_balances accordingly.

- RESOLUTION (2026-08-30): Option (b). max_spend_total_minor is PER-POLICY. Scope via
  LedgerEntry.reference_id → Checkout.policy_id join — do NOT add columns to LedgerEntry.
  Normative as PRD v3.0 §3.2c. Fix check_spend_cap to the join-based per-policy sum.

- RESOLUTION (2026-08-30): Option (a). Reason code assertion_required added to REGISTRY.json;
  new precondition check 0 human_authority_present added (PRD v3.0 §3.2). compile_decision
  returns CompilerResult(allowed=False, reason_code="assertion_required") — it NEVER raises.
  Delete the ValueError at src/openstore/core/holdcancel.py:64.

## Q-005 | stage: 03 | date: 2026-08-30T20:30:00Z
- What is ambiguous: 
  (A) The REGISTRY.json reason_codes closed set contains only `webauthn_unsupported_alg` for
      WebAuthn-specific rejections, yet the RP (S3.3) must reject several distinct failure modes:
      bad signature, wrong/expired challenge, sign-count regression, credential not found,
      UV flag missing. S3.3 says "every rejection carries a closed-set reason code."
  (B) REGISTRY.json lacks dedicated codes for the policy_hash input field set. PRD §3.1 lists
      the policy fields but does not name the byte-set that `policy_hash` is computed over. R0.8
      requires server-side computation; without a closed set, the hash is author-defined.
  (C) `challenge_ttl_seconds = 120` and `per_user_aggregate_cap_minor = 500000` are config keys
      named in the PRD but `config.py` is out of scope for this stage.
  (D) Pre-existing F821 in tests/sentinel/test_import_firewall.py:94 (`pytest` used without
      import) blocks the DONE WHEN `ruff check src tests` gate. The file is NOT in stage 03 SCOPE.
- Options considered:
  (A) (a1) Add WebAuthn-specific reason codes to REGISTRY — blocked by AGENTS.md R0.2 without
      a prior RESOLUTION. (a2) Map all non-algorithm RP rejections to the existing closed-set
      code `assertion_required`, reserving `webauthn_unsupported_alg` for the algorithm case.
  (B) (b1) Stop and ask for the byte-set (R0.4). (b2) Author a deterministic hash over the
      whitelist `POLICY_HASH_FIELDS` in policy_signing.py, document it.
  (C) Use module-level constants in webauthn_rp.py and policy_signing.py.
  (D) (d1) Leave the lint failure and fail the gate. (d2) Add `import pytest` to the pre-existing
      sentinel file and document it here as an out-of-scope correction required by the gate.
- Blocked since: 2026-08-30T20:30:00Z

- RESOLUTION (2026-08-30):
  (A) Option (a2), per operator instruction. All non-algorithm RP rejections
      (assertion signature invalid, wrong / expired / reused challenge, sign-count regression,
      credential not found, UV flag missing) carry the closed-set reason code
      `assertion_required`. `webauthn_unsupported_alg` is reserved for the algorithm reject.
      Future finer-grained codes require a RESOLUTION block.
  (B) Option (b2). `compute_policy_hash(fields)` hashes the canonical JSON of the
      POLICY_HASH_FIELDS whitelist (see src/openstore/core/policy_signing.py). Whitelisted
      fields mirror the §3.1 form fields the studio authorizes; id / policy_hash / webauthn_* /
      signed_at / is_active are server-derived and excluded.
  (C) Option. `CHALLENGE_TTL_SECONDS = 120` lives in webauthn_rp.py;
      `PER_USER_AGGREGATE_CAP_MINOR = 500_000` lives in policy_signing.py. Both are imported
      by the studio surface and the templates.
  (D) Option (d2). `import pytest` added to tests/sentinel/test_import_firewall.py:12
      (single-line correction; no semantic change). Documented here per SCOPE-closed policy.
