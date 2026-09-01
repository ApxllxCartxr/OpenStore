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

## Q-006 | stage: 03 | date: 2026-08-30T22:00:00Z
- What is ambiguous: Stage 03 review surfaced two studio.py reason codes that are NOT in
  REGISTRY.json: (i) `policy_version_unsupported` surfaced when `LegacyPolicyError` is raised by
  complete_policy_signing on a `policy_version != 2` policy; (ii) `policy_not_built` surfaced
  defensively when `outcome.policy is None` after `outcome.ok` is True. R0.3 forbids unknown
  values; R0.2 forbids adding identifiers to REGISTRY.json without a RESOLUTION.
- Options considered:
  (i-a) Add `policy_version_unsupported` to REGISTRY.json reason_codes. (i-b) Map
  `LegacyPolicyError` to an existing closed-set code: the most semantically close is
  `policy.aggregate_cap_exceeded` (both are policy-signing gate rejections) — but the names
  diverge. (i-c) Map to a generic policy-side code already in the set; `policy.policy_expired`
  is about timing, `policy.tag_violation` is about content. No perfect fit exists.
  (ii-a) Keep the defensive `policy_not_built` and add the code. (ii-b) Delete the defensive
  check entirely: complete_policy_signing's contract guarantees `policy is not None` when
  `ok=True`, so the check is dead.
- Blocked since: 2026-08-30T22:00:00Z
- RESOLUTION (2026-08-30):
  (i) SUPERSEDED 2026-09-01. Add reason code `policy.policy_version_unsupported` to
  REGISTRY.json reason_codes (R0.2 clearance granted by this RESOLUTION). Map
  `LegacyPolicyError` -> `policy.policy_version_unsupported` in complete_policy_signing on
  `policy_version != 2`, update the Studio surface, and update the Stage-3 test that
  previously asserted the old value. The earlier mapping to `policy.aggregate_cap_exceeded`
  is void; `policy.aggregate_cap_exceeded` remains reserved for actual aggregate-cap
  breaches only (PRD v3.0 §3.2a).
  (ii) Keep: delete the defensive `if outcome.policy is None` branch entirely. The
  contract holds.

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

- AMENDMENT (2026-09-01): Keep the closed reason code `assertion_required` on the compiler
  path exactly as resolved above. Additionally, the WebAuthn RP MUST record the precise
  failure type — one of assertion_signature_invalid | challenge_mismatch | challenge_expired
  | challenge_reused | sign_count_regression | credential_not_found | uv_flag_missing — on
  the AuditLogEntry detail field and in the `#alerts` Discord trace, so security-relevant
  failures (especially sign_count_regression, the cloned-authenticator signal) are never
  flattened out of the evidence trail. These failure-type strings are local to the audit
  detail and are NOT added to REGISTRY.json reason_codes. New fine-grained reason codes
  remain future RESOLUTION material.

## Q-007 | stage: 05 | date: 2026-08-31T00:00:00Z
- What is ambiguous: The [verify-at-build] constants (R0.7) — duplicate-reference_id
  error code string, cancel-already-paid HTTP 400 body, and webhook body shapes for
  payment_link.paid / payment_link.cancelled / payment_link.partially_paid / payment.failed
  — must be captured live from Razorpay test-mode. scripts/capture_constants.py
  implements the capture flow. However, the sandbox has no outbound access to
  api.razorpay.com, so live capture cannot be performed during this build session.
  The golden fixtures in GOLDEN/razorpay/ are documented as "captured live by
  scripts/capture_constants.py" with source comments. PRD §4 states the constants
  are [verify-at-build]; R0.7 requires live confirmation or an OPEN_QUESTION.
- Options considered:
  (a) Stop the stage entirely (R0.7) until live test-mode is reachable. This would
      block Stages 5–10 since Stage 5 is on the critical path.
  (b) Use representative canonical fixtures with full source comments, implement
      scripts/capture_constants.py correctly so a developer can re-run it against
      live test-mode, and document the gap. Tests use the fixtures with mocked SDK
      calls. This is the chosen path.
  (c) Invent the constants from memory. FORBIDDEN — the unforgivable act (R0.7).
- Blocked since: 2026-08-31T00:00:00Z
- RESOLUTION (2026-09-01): Option (b) approved — proceed with representative canonical
  fixtures carrying full source comments; scripts/capture_constants.py stays implemented
  and re-runnable; tests use the fixtures with mocked SDK calls; the gap is recorded
  here. Production release remains BLOCKED by R0.7 on a live capture — option (c)
  (inventing constants from memory) remains FORBIDDEN.
  Recovery protocol (human-executed, before any production release):
  1) On a machine with outbound access to api.razorpay.com, run
     `uv run python scripts/capture_constants.py` with the real test-mode keys from .env;
  2) Confirm it writes the 4 webhook bodies (payment_link.paid / payment_link.cancelled /
     payment_link.partially_paid / payment.failed) and the 2 error shapes (
     duplicate-reference_id create error; cancel-already-paid HTTP 400) into
     GOLDEN/razorpay/ with source comments (URL + capture date);
  3) Re-run tests/stage05/ against the captured bodies — 0 failures;
  4) Commit as `chore(razorpay): pin verify-at-build constants <ISO-date>`;
  5) Update this RESOLUTION block with the capture date.
  Until then Stage 5 remains "provisional": no production code path may depend on
  unverified constants.

## Q-008 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: INV-9 — validate_access_token() (src/openstore/core/oauth.py) parses
  the JWT, checks `jti`, `exp`, DB record, and revocation, but NEVER verifies the JWS
  signature nor pins the header `alg`. A forged token that re-uses a live token's `jti`
  claim with header `alg: none` or `alg: RS256` (no signature) is ACCEPTED. The stage-10
  red-team proofs tests/redteam/test_inv9_oauth_alg.py::test_alg_none_token_rejected and
  ::test_rs256_alg_confusion_rejected assert rejection and FAIL. Per stage-10 MUST-NOT the
  tests must not be weakened; a failing red-team test is a product bug. PRD §3.13/§5.1
  mandates ES256 asymmetric tokens with `kid` (INV-9) — the issuer path conforms, but the
  validator has no key/signature verification step.
- Options considered: (a) verify signature + pin `alg` in validate_access_token against
  the merchant JWKS before trusting the DB record (schema-from-PRD compliant, closes the
  gap); (b) declare the validator "internal-only" and defer signature verification to the
  resource-server boundary; (c) weaken/delete the failing red-team tests — FORBIDDEN.
- Blocked since: 2026-09-01T14:42:34Z
- RESOLUTION (2026-09-01): Option (a). validate_access_token must, BEFORE trusting any
  claim, verify the JWS signature AND pin the header alg, using the same JWS library as
  issuance (python-jose), explicit allowlist algorithms=["ES256"]:
  1) decode with verify_signature=True + allowlist — alg:none and RS256 are rejected by
     the library's algorithm check before signature verification (mechanism verified
     on 2026-09-01);
  2) resolve the public key by `kid` from the merchant JWKS
     (/.well-known/poai-jwks.json, DECISIONS §11.1.10); missing/unknown kid -> hard
     error;
  3) only then continue the existing jti/exp/DB/revocation checks.
  Any verification failure carries the new closed-set reason code
  `auth.token_verification_failed`, authorized by this RESOLUTION to be added to
  REGISTRY.json reason_codes — add it TOGETHER WITH the Stage-10 implementation (never
  before, so registry_diff never sees an unimplemented identifier). Tests to add:
  alg:none / RS256 impersonation / unsigned / wrong-kid unit tests;
  tests/redteam/test_inv9_oauth_alg.py::test_alg_none_token_rejected and
  ::test_rs256_alg_confusion_rejected must pass unweakened.

## Q-009 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: Prompt-injection pattern family — the closed pattern list
  _PROMPT_INJECTION_PATTERNS (src/openstore/core/campaigns.py) rejects
  "ignore previous instructions" and "ignore all previous" but NOT
  "DISREGARD ALL PREVIOUS INSTRUCTIONS" (same family, synonym verb, undetected). Red-team
  tests tests/redteam/test_prompt_injection.py parametrize this payload; the two
  variations FAIL as DID-NOT-RAISE. stage-10 MUST-NOT forbids weakening the test; the gap
  is a product bug in the deterministic validator (R0.3: unknown value must hard-error,
  never silently pass).
- Options considered: (a) extend the pattern set with "disregard all previous" and
  "ignore all instructions" variants (validator becomes a closed-set superset that still
  matches only authored patterns); (b) move the pattern list to REGISTRY.json as an
  exhaustive closed set (design change, requires a new identifier — R0.2 blocks without
  approval); (c) remove the payload from the parametrize list — FORBIDDEN (weakens the
  test).
- Blocked since: 2026-09-01T14:42:34Z
- RESOLUTION (2026-09-01): Option (a) + normalization hardening. Extend
  _PROMPT_INJECTION_PATTERNS (src/openstore/core/campaigns.py) to the full synonym
  family: ignore all previous instructions, ignore all prior instructions, ignore all
  instructions, disregard all previous instructions, disregard all prior instructions,
  disregard all instructions, forget all previous instructions, forget all prior
  instructions, override your instructions, override your previous instructions.
  Deterministic matching (no LLM): normalize input first — lowercase; collapse every
  whitespace run (including newlines and tabs) to a single space; strip all
  non-alphanumeric characters — then substring-match against the normalized family.
  Verified on 2026-09-01: lowercase + punctuation variants are caught, but
  whitespace-collapse is mandatory because multi-space and newline-separated variants
  slip through before collapsing. Both red-team payloads in
  tests/redteam/test_prompt_injection.py must pass unweakened. The wordlist stays
  maintainer-owned; migration to REGISTRY.json remains a future design change requiring
  its own RESOLUTION.

