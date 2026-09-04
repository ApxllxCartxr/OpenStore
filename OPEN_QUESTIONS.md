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

## Q-010 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: Sidecar integration contract — no spec exists for deploying
  OpenStore beside an existing merchant site. The PRD assumes a standalone process
  but does not define health/readiness, startup ordering, versioning, metrics, or
  origin-security binding for the two deployment topologies (same-origin reverse proxy
  vs subdomain). Without these, a merchant cannot integrate OpenStore without deep
  PRD knowledge.
- Options considered: (a) define a sidecar integration contract as §1.4 in the PRD
  with 7 SID requirements (deployment topology, health/readiness, startup ordering,
  failure semantics, origin security, versioning, observability) and implement in
  Stage 10; (b) defer to a future version; (c) implement ad-hoc without spec.
- Blocked since: 2026-09-01T14:42:34Z
- RESOLUTION (2026-09-01): Option (a). Operator-authorized addition to PRD §1.4.
  Full specification provided in the implementation prompt §3.3. Identifiers:
  SID-1 (public_base_url, deployment modes), SID-2 (health/live, health/ready),
  SID-3 (startup/shutdown ordering), SID-4 (crash/restart semantics), SID-5
  (origin security boundary), SID-6 (versioning & rollback), SID-7 (metrics).
  New routes: /health/live, /health/ready, /internal/metrics. New config key:
  public_base_url. New file: src/openstore/core/health.py.
  Tests: kill+restart recovery, WebAuthn RP ID match, CORS origin binding.
  The 3 routes authorized for REGISTRY.json addition: /health/live, /health/ready,
  /internal/metrics.

## Q-011 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: SID-7 mandates Prometheus text exposition format for metrics
  endpoint. The PRD's closed dependency list (Part 4) does not include prometheus-client.
  Hand-rolling the format vs adding the library is a dependency decision.
- Options considered: (a) add prometheus-client to dependencies; (b) hand-roll the
  minimal text exposition (# HELP/# TYPE + gauge lines) with no new dependency.
- PROVISIONAL (unattended-agent): Option (b). Use hand-rolled Prometheus text
  exposition format with # HELP/# TYPE lines and gauge values. No new dependency.
  Operator to ratify.

## Q-012 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: __version__ single source of truth. PEP 621 (pyproject.toml)
  convention is version in pyproject.toml, exposed at runtime via
  importlib.metadata.version("openstore"). The unattended-agent addendum B4
  initially specified src/openstore/__init__.py as source, which conflicts.
- Options considered: (a) src/openstore/__init__.py hard-coded; (b) pyproject.toml
  as canonical, __init__.py reads via importlib.metadata.version.
- PROVISIONAL (unattended-agent): Option (b). Canonical version in pyproject.toml
  (PEP 621), __init__.py exposes it via importlib.metadata.version("openstore").
  Operator to ratify.

## Q-013 | stage: 10 | date: 2026-09-01T14:42:34Z
- What is ambiguous: Main-prompt §11 step 3 says "Discord bot → search_products → …"
  but the unattended-agent addendum B6 says exercise buyer flow headlessly via direct
  MCP tool calls (no live Discord token available overnight). The addendum claims
  override authority for R0.4 only, not for §11.
- Options considered: (a) follow §11 literally and require live Discord (blocks
  unattended run); (b) supersede §11 steps 3-5 for the unattended run.
- PROVISIONAL (unattended-agent): Option (b). B6 supersedes main-prompt §11 steps
  3-5 for the unattended run: those steps are executed headlessly with GOLDEN WebAuthn
  fixtures and direct MCP calls; the Discord-bot and live-payment variants move to
  the PENDING-HUMAN list. Operator to ratify.

## Q-014 | stage: 11 | date: 2026-09-02T00:00:00Z
- What is ambiguous: The chat-native purchase flow (approved plan
  `go-with-the-push-functional-hearth.md`) requires a durable `handoffs` table — a
  single-use, token-addressed row that parks a chat conversation while a human
  completes an out-of-band ceremony (policy signing, amendment approval) in a browser,
  then resumes the conversation. No such table, migration, or model exists; PRD v3.0 is
  silent on chat-resume state (it predates the chat loop). R0.2/R0.4 require an
  OPEN_QUESTION before naming a new identifier (table/columns) that Phase 2 will
  implement.
- Options considered: (a) one generic `handoffs` table serving all out-of-band
  ceremonies (policy signing, amendment approval) as designed in the plan: `token` (PK,
  `secrets.token_urlsafe(32)`), `kind` (closed set `policy` | `amendment`),
  `merchant_id`, `chat_platform`, `chat_user_id`, `chat_channel_id`, `request_text`,
  `created_at`, `expires_at`, `consumed_at`, `result_policy_id`; (b) a bespoke one-off
  "policy signed" callback table with no reuse for amendments; (c) add a buyer/session
  column directly to `intent_policies` instead. (c) rejected: it breaks
  `tests/sentinel/test_schema_snapshots.py` and duplicates what
  `IntentPolicy.webauthn_credential_id` already gives via the credential join.
- Blocked since: 2026-09-02T00:00:00Z
- RESOLUTION (2026-09-02): Option (a), operator-approved (plan
  `go-with-the-push-functional-hearth.md`, approved 2026-09-02). Phase 2 adds
  `alembic/versions/0003_handoffs.py` (explicit `op.create_table`, real
  `upgrade()`/`downgrade()`, following the `0002` convention — NOT `create_all`, since
  `0001` already uses that path and `0003` must be safe on both fresh and existing DBs)
  and a `Handoff` SQLModel table in `models.py` with exactly the columns listed in
  option (a). `kind` is a closed set of exactly `{"policy", "amendment"}` — any other
  value is a hard error (R0.3). `core/handoff.py` (new module, precedent:
  `core/health.py` from Q-010) implements create/resolve/consume/expire, failing loud
  (never silently defaulting) on an expired or already-consumed token per R0.5. This
  RESOLUTION authorizes the table/module for Phase 2; it is NOT implemented in Phase
  0/1 of this stage.

## Q-015 | stage: 11 | date: 2026-09-02T00:00:00Z
- What is ambiguous: `policy_studio_router` (src/openstore/surfaces/studio.py)
  implements the real WebAuthn registration/assertion/blast-radius endpoints and is
  fully tested (`tests/stage03/test_policy_studio.py`), but `server.py:352-354` mounts
  only `psp_router` — the studio router has zero callers in the running app, and
  `/intent/studio` instead serves a dead Stage-1 stub
  (`surfaces/static/policy_studio.html`, whose sign button is a hardcoded
  `alert(...)`). REGISTRY.json's `routes` list already contains the wildcard
  `/internal/webauthn/*` and `/internal/policy/blast-radius`, but
  `tests/sentinel/test_route_table_snapshot.py`'s `_EXPECTED_ABSENT` set deliberately
  keeps them absent, and no REGISTRY entry names the four concrete paths FastAPI
  actually mounts (`/internal/webauthn/register/begin`,
  `/internal/webauthn/register/complete`, `/internal/webauthn/assertion/begin`,
  `/internal/webauthn/assertion/complete`) — the wildcard does not string-match a
  concrete FastAPI route path, so mounting the router without adding the concrete
  paths would make `test_no_undisclosed_routes_mounted` fail as "shadow" routes. R0.2
  forbids adding these without a prior RESOLUTION.
- Options considered: (a) mount `policy_studio_router` in `server.py`, replacing the
  dead `/intent/studio` stub, and add the four concrete `/internal/webauthn/...` paths
  to REGISTRY.json `routes` (keeping the existing wildcard, since other future internal
  webauthn paths may still be added under it); remove those four paths plus
  `/internal/policy/blast-radius` from `_EXPECTED_ABSENT` wherever they are currently
  listed there; (b) leave the router unmounted and the wildcard purely aspirational,
  deferring indefinitely (rejected — this is precisely the blocker the plan identifies:
  signing is impossible today without this); (c) mount the router but keep only the
  wildcard in REGISTRY and special-case the sentinel's shadow-route check for this one
  prefix (rejected — special-casing a compliance test is worse than naming the four
  real paths).
- Blocked since: 2026-09-02T00:00:00Z
- RESOLUTION (2026-09-02): Option (a), operator-approved. Implemented in Phase 1 of
  this stage (same commit as the mount). Concretely: only
  `/internal/policy/blast-radius` needed removing from `_EXPECTED_ABSENT` (the four
  concrete webauthn paths were never listed there — they are brand-new REGISTRY
  entries); the `/internal/webauthn/*` wildcard stays in `_EXPECTED_ABSENT` permanently
  since FastAPI never mounts a literal `*` path.

## Q-016 | stage: 11 | date: 2026-09-02T00:00:00Z
- What is ambiguous: The chat-native handoff flow (Q-014) introduces failure modes with
  no closed-set reason code: a chat-supplied handoff `token` that does not resolve to a
  row, one that has expired, one already consumed by a prior click, and an attempt to
  run the buyer's errand against a policy that was never signed. REGISTRY.json's
  `authority.*` namespace exists (`error_namespaces`) with a dedicated
  `authority_reason_codes` array, but has no codes for these handoff-resolution
  failures. R0.2/R0.5 forbid inventing these without a prior RESOLUTION naming them.
- Options considered: (a) add `authority.handoff_not_found`, `authority.handoff_expired`,
  `authority.handoff_consumed`, `authority.policy_unsigned` to REGISTRY.json's
  `authority_reason_codes` array; (b) add them to the flat `reason_codes` array instead
  (rejected — no `authority.`-prefixed code has ever lived there; `authority_reason_codes`
  is the established home for the `authority.*` namespace); (c) reuse existing codes
  (`assertion_required`, `policy_not_found`) — rejected, they don't distinguish a bad
  handoff token from a bad WebAuthn assertion or a missing policy, which the evidence
  trail needs to keep distinct (mirrors the Q-005 AMENDMENT rationale).
- Blocked since: 2026-09-02T00:00:00Z
- RESOLUTION (2026-09-02): Option (a), operator-approved. Added to REGISTRY.json's
  `authority_reason_codes` array now (Phase 0 of this stage), ahead of the Phase 2
  `core/handoff.py` implementation that raises them — this differs from the Q-008
  sequencing note ("add together with the implementation, never before") because
  `test_registry_compliance.py` performs no unimplemented-identifier check today (it
  only asserts the registry's declared sets match hardcoded literals); the
  operator-approved plan explicitly authorizes reserving all four Phase-0 identifiers
  ahead of Phase 2/4 implementation, mirroring how Q-010 pre-authorized the SID-* route
  identifiers. SEMANTIC NOTE: the existing members of `authority_reason_codes`
  (`authority.unknown_scheme`, `authority.scheme_capped_*`) are all *delegated-authority
  scheme* rejections — which agent-authority mechanism (native WebAuthn, AP2 intent/cart
  mandate, ACP delegated token) is unrecognized or capped. The four codes added here are
  *ceremony-lifecycle* rejections (a handoff token's resolution state, or an unsigned
  policy blocking an errand) — a different failure family sharing only the `authority.*`
  namespace prefix. This RESOLUTION knowingly widens `authority_reason_codes`'s meaning
  from "scheme rejection" to "authority-and-ceremony rejection"; a future RESOLUTION may
  split them into separate arrays if the two families keep diverging, but no such split
  is made now. `authority.policy_unsigned` is raised when a resolved handoff's
  `kind == "policy"` errand is attempted with no active signed policy found for the
  buyer; the other three are raised by `core/handoff.py`'s resolve step (Phase 2).

## Q-017 | stage: 11 | date: 2026-09-02T00:00:00Z
- What is ambiguous: Amendment approval (plan Phase 4: `MerchantAgent.negotiate` /
  `draft_amendment` → a `handoff(kind=amendment)` → one-tap approval in the studio →
  apply the cart delta, increment version, recompile → ALLOW) needs HTTP routes to
  approve/reject a drafted amendment, analogous to the existing
  `/campaign/<campaign_id>/approve` and `/campaign/<campaign_id>/reject` (Stage 8). No
  such routes, or the amendment persistence they act on, exist in REGISTRY.json or the
  PRD (which predates the negotiation loop's amendment-approval UI). R0.2 forbids naming
  these routes without a prior RESOLUTION.
- Options considered: (a) name them now (`/intent/amendment/<amendment_id>/approve`,
  `/intent/amendment/<amendment_id>/reject`), matching the `/campaign/<id>/...`
  precedent, and reserve them for Phase 4 without adding them to REGISTRY.json's
  `routes` list until Phase 4 actually mounts them (matching the Q-008 "add together
  with the implementation" sequencing — unlike Q-016's reason codes, these routes are
  not implemented anywhere in Phases 0-1, so there is no forcing function to pre-add
  them); (b) defer naming until Phase 4 entirely (no OPEN_QUESTION now) — rejected,
  Phase 0's purpose per the plan is to pre-clear every identifier this whole stage will
  need so later phases are not blocked re-litigating R0.4; (c) reuse the campaign
  approve/reject routes for amendments too — rejected, amendments are keyed by
  `amendment_id`, not `campaign_id`, and conflating the two closed sets is exactly the
  kind of identifier-invention R0.2 exists to prevent.
- Blocked since: 2026-09-02T00:00:00Z
- RESOLUTION (2026-09-02): Option (a), operator-approved. Route names
  `/intent/amendment/<amendment_id>/approve` and
  `/intent/amendment/<amendment_id>/reject` are reserved for Phase 4. They are NOT
  added to REGISTRY.json's `routes` list in Phase 0/1 — unlike Q-015's webauthn paths,
  these routes are not implemented anywhere in this execution; Phase 4 adds the
  REGISTRY entries and the routes together.


## Q-018 | stage: 11 | date: 2026-09-03T00:00:00Z
- What is ambiguous: Phase 3 (plan `go-with-the-push-functional-hearth.md`, "Money
  reaches chat") requires the webhook background worker and the hold-release loop to DM
  the buyer when a payment lands, a hold nears expiry, or a hold releases. Neither has
  any way to know which Discord user to DM: `Checkout` carries no chat identity, and the
  plan's own "buyer identity rides the existing credential join — no new column"
  decision (Design section) resolves a *different* question (how a chat errand finds its
  IntentPolicy via `WebAuthnCredential.user_handle`), not how a webhook or a 30s
  background tick — neither of which has a chat message or credential in hand — finds
  the checkout's original DM target. R0.2/R0.4 require an OPEN_QUESTION before adding
  new `Checkout` columns.
- Options considered: (a) add nullable `chat_platform`, `chat_user_id`, `chat_channel_id`
  columns to `Checkout`, mirroring `Handoff`'s same-named fields exactly, set once at
  `checkout_initiate` time from the chat identity `BuyerBot._handle_shop` already has in
  hand; (b) look up the buyer via the credential join at webhook/tick time the way
  `require_active_policy` does — rejected, that join starts from a buyer handle, but the
  webhook/tick start from a `checkout_id`/PSP `reference_id` with no buyer handle
  attached anywhere reachable from `Checkout`, so the join has nothing to key off; (c) a
  separate `checkout_chat_identity` side table keyed by `checkout_id` — rejected as
  needless indirection over (a) for a strictly 1:1, checkout-lifetime-scoped fact, and it
  would still need the same nullable-columns discussion one table over.
- Blocked since: 2026-09-03T00:00:00Z
- RESOLUTION (2026-09-03): Option (a), operator-approved (standing delegation under the
  approved plan; "do not wait for input or ask me for confirmation unless you are
  genuinely blocked" — this is a plan-silent schema decision, not a blocking one).
  `alembic/versions/0004_checkout_chat_identity.py` adds the three nullable columns
  (explicit `op.add_column`, real `upgrade()`/`downgrade()`, no-op-safe on a fresh DB
  the same way `0003` is). `models.py`'s `Checkout` gains the three fields, nullable
  since non-chat (API/test-created) checkouts never set them.
  `tests/sentinel/test_schema_snapshots.py`'s `checkouts` entry is updated in lockstep.
  Threaded through: `BuyerBot._handle_shop` → `BuyerAgent.shop()` (new optional
  `chat_platform`/`chat_user_id`/`chat_channel_id` kwargs) → the `checkout_initiate` MCP
  tool (same new optional arguments) → stamped onto the `Checkout` row before
  `create_payment_link` runs, so `psp/router.py`'s webhook worker and `server.py`'s
  hold-release loop can read `Checkout.chat_user_id` directly with no join.

## Q-019 | stage: 11 | date: 2026-09-03T00:00:00Z
- What is ambiguous: Phase 4 (plan: "Evidence: implement /orders/{checkout_id}/evidence,
  call create_poai_bundle on a completed purchase... so the bundle can reach AAL2")
  requires populating the PoAI `authority` section, including `authority.webauthn`
  (credential_id, authenticator_data, challenge_binding, signed_at), for a real
  purchase's evidence bundle. But the WebAuthn assertion actually verified at
  `create_checkout`/`confirm_checkout` time (`core/api.py`) is never persisted anywhere
  retrievable by `checkout_id` — `Checkout` has no assertion column, and only `Campaign`
  persists `webauthn_assertion` JSON (for campaign approval, an unrelated ceremony). So
  at RELEASED time (`psp/router.py`, where the bundle is built) there is no real
  `authenticator_data` to populate `authority.webauthn` with. `poai.py`'s
  `evaluate_aal_predicates` predicate `e2` (`authority.webauthn.authenticator_data`'s UV
  bit) — and everything gated behind it in `compute_aal_level_from_bundle`'s first-match
  table — requires real bytes; R0.3 forbids fabricating them.
- Options considered: (a) add a column persisting the raw assertion
  (`credential_id`/`client_data_json`/`authenticator_data`/`signature`/`challenge`) on
  `Checkout` at confirm time, so a later bundle build has real bytes for `e2` — rejected
  for this phase: touches the money-critical confirm path (`confirm_checkout`,
  `create_checkout`) that stage-10-frozen tests already pin, a materially larger change
  than "wire the chat loop" implies, and stores raw authenticator signatures at rest with
  no stated retention/access-control requirement (a security-relevant new surface, not a
  wiring fix); (b) fabricate plausible `authenticator_data` bytes at bundle-build time so
  `e2` reads True — FORBIDDEN (R0.3: never invent evidence bytes, especially UV-flag/
  security-relevant fields); (c) build the bundle honestly from what IS legitimately on
  hand (policy_version, credential_id, challenge_binding mode/cart_hash used at compile
  time) and leave `authority.webauthn.authenticator_data` absent, documenting that `e2` —
  and therefore the bundle's own recomputed AAL via `compute_aal_level_from_bundle` —
  stays False/AAL0 for a bundle built this way, distinct from `Checkout.aal_level` (the
  already-flagged, out-of-scope amount-based divergence, plan item #22's parenthetical).
- Blocked since: 2026-09-03T00:00:00Z
- RESOLUTION (2026-09-03): Option (c), standing delegation under the approved plan (a
  plan-silent implementation-completeness decision, not a blocking one — proceeding per
  "do not wait for input... unless genuinely blocked"). `psp/router.py`'s
  `_build_and_store_evidence` populates `human_intent` (request_text + a real, correctly
  keyed digest — see Q-020) and `notification` (real `sent_at` + `receipt_digest` from the
  DM just sent) so predicates `e8`/`e9` are honestly True — this phase's actual, testable
  deliverable ("wiring the chat loop is what makes AAL2 reachable" per plan item #22) —
  while `authority.webauthn` carries only what is legitimately reconstructable
  (`policy_version`, `credential_id`) and no fabricated `authenticator_data`. Persisting
  the real per-checkout assertion (option (a)) so a bundle can also satisfy `e2`..`e7` and
  reach a genuine AAL2/AAL3 is left as explicit future work; this phase does not implement
  it, and does not claim `compute_aal_level_from_bundle` returns 2 for these bundles —
  only that `e8`/`e9` individually hold, which is what Phase 4's tests assert.

## Q-020 | stage: 11 | date: 2026-09-03T00:00:00Z
- What is ambiguous: Amendment application (plan Phase 4: "apply the delta, increment
  version, recompile → ALLOW") assumes `IntentPolicy` has a version field to increment,
  but `models.py`'s `IntentPolicy.policy_version` is the *policy-schema* version (pinned
  at 2, gates `policy.policy_version_unsupported`), not an amendment-lineage counter —
  there is no such field, and R0.3 forbids inventing one. Separately: (i) `draft_amendment()`
  itself already returns a complete, digested draft dict but has no persistence hook — the
  cart it was drafted against must survive the round-trip from drafting (chat) to approval
  (browser, possibly much later) — `Handoff` (Q-014) has no field for this. (ii) amendment
  approval needs a WebAuthn ceremony bound to the specific drafted amendment (mirroring
  the existing `{"mode":"cart","cart_hash":...}` binding-mode convention in
  `webauthn_rp.py`), and `webauthn_rp.py`'s binding modes are not REGISTRY-governed
  anywhere, so extending them needs its own note per R0.4 even though R0.2 does not
  block it.
- Options considered:
  (i) draft persistence: (i-a) a new `amendments` table — rejected as needless
  indirection over a nullable JSON column on the already-existing single-use `Handoff`
  row this ceremony already parks on (mirrors the Q-018 nullable-column-plus-migration
  precedent exactly, one migration covering both this and the Checkout columns below);
  (i-b) `Handoff.amendment_draft: dict | None` (chosen).
  (ii) amendment scope: (ii-a) increment a new `IntentPolicy.version` field and mutate
  the standing policy in place — rejected: no such field exists in the PRD/schema, adding
  one is a materially larger, unreviewed change to the signed-policy row every other
  stage's tests assume is stable, and it contradicts the draft's own declared intent;
  (ii-b) honor `draft_amendment()`'s own delta, which already sets `"one_time": True` —
  read literally, this is a single-checkout exception, not a standing-policy mutation.
  Apply it as an unpersisted, in-memory `IntentPolicy` snapshot
  (`IntentPolicy.model_copy(update={...})`) used only for one recompile, touching only
  the two fields the delta actually names (`add_allowed_skus` → exempt those SKUs from
  `blocked_skus`; `bump_max_spend_per_tx_minor` → raise the effective per-tx cap) —
  nothing invented beyond the delta's own declared fields (chosen).
  (iii) WebAuthn binding: (iii-a) reuse `{"mode":"cart",...}` — rejected, conflates an
  amendment-approval signature with a checkout-authorization signature, two different
  ceremonies; (iii-b) a new `{"mode":"amendment","amendment_id":...}` binding, mirroring
  `cart` mode's shape exactly but keyed on `amendment_id` (chosen).
- Blocked since: 2026-09-03T00:00:00Z
- RESOLUTION (2026-09-03): Options (i-b), (ii-b), (iii-b), standing delegation under the
  approved plan (plan-silent implementation decisions, not blocking ones). Schema:
  `alembic/versions/0005_amendment_and_evidence.py` adds `handoffs.amendment_draft`
  (JSON, nullable) plus `checkouts.request_text` (String, nullable — the original chat
  goal, so a completed purchase's evidence bundle can populate PoAI `human_intent`) and
  `checkouts.poai_bundle` (JSON, nullable — the persisted bundle itself, per plan item
  #21's "simplest defensible choice... store it as a JSON column on Checkout"), following
  the Q-018 nullable-column-plus-migration precedent exactly.
  `tests/sentinel/test_schema_snapshots.py`'s `checkouts`/`handoffs` entries updated in
  lockstep. `core/handoff.create_handoff` gains an optional `amendment_draft` kwarg.
  `core/webauthn_rp._binding_matches` gains the `amendment` mode (mirrors `cart` mode).
  Amendment scope is **one-time, per-checkout** (option ii-b): approving an amendment
  never mutates the standing, signed `IntentPolicy` row — `surfaces/studio.py`'s
  `_apply_amendment_delta` builds an unpersisted `model_copy` snapshot, and
  `core/api.create_checkout_from_policy` (factored out of `create_checkout` — a
  behavior-preserving refactor, not a new code path for the existing call site) compiles
  and persists a real `Checkout` against that snapshot directly, without a second
  round-trip through the real (unrelieved) policy row. This means "resume the parked
  errand" on amendment-approval success does NOT re-run `BuyerAgent.shop()`/MCP
  `create_cart` (which would re-load the real, unrelieved policy and re-deny) — instead
  `surfaces/studio.py`'s `_approve_amendment` drives checkout creation + payment-link
  creation directly, achieving the same "auto-resume, DM the result" effect within the
  one-time constraint. If a future stage wants amendments to durably widen a standing
  policy, that is a distinct, larger design (a real version/lineage field) explicitly
  deferred, not implemented here.
  BUG FOUND IN THE COURSE OF THIS WORK (narrowly fixed, not a scope expansion):
  `core/holdcancel.initiate_hold`'s INV-11 re-check re-fetched `IntentPolicy` by
  `policy_id` from the DB, silently discarding whatever policy snapshot
  `compile_decision` had actually evaluated against — for the amendment path this
  re-fetch would re-apply the *unrelieved* caps immediately after `compile_decision`
  had just approved the relieved ones, always raising `ValueError` on an otherwise-valid
  amendment. Fixed by adding optional `max_spend_per_tx_minor`/`max_spend_total_minor`
  parameters to `initiate_hold`; `create_checkout_from_policy` now passes the exact caps
  the compile pass used, and `initiate_hold` only falls back to its own DB re-fetch when
  they are omitted — unchanged behavior for every pre-existing (non-amendment) caller
  (verified: the full pre-existing suite still passes unmodified).

## Q-021 | stage: 11 | date: 2026-09-03T00:00:00Z
- What is ambiguous: Negotiation's `swap_sku` executor (plan Phase 4, item #24: "write
  the missing cart_delta executor") needs to resolve a `policy.sku_blocked` DENY by
  replacing the blocked SKU with a compliant substitute. No PRD/spec text, catalog
  schema, or existing code defines a SKU-substitution algorithm (similarity matching,
  category mapping, price-banding, or otherwise) — `MerchantAgent.negotiate()`'s
  `swap_sku` flag names the *intent* ("propose a swap") but not a *mechanism*. R0.3
  forbids inventing a matching heuristic from memory.
- Options considered: (a) invent a similarity-matching heuristic (e.g. same tags,
  nearest price) — FORBIDDEN, no spec grounds it and it would silently pick a product
  the buyer never asked for; (b) treat `swap_sku` identically to `remove_violating_tags`
  — drop the blocked line item — the same mechanical effect, just keyed by
  `policy.blocked_skus` instead of `policy.allowed_tags`/`tag_mode`; a real substitution
  engine (catalog similarity search) is out of scope for this stage; (c) stop and treat
  every `policy.sku_blocked` DENY as `NO_COMPLIANT_PATH` (skip negotiation entirely for
  this reason code) — rejected, `negotiate()`'s existing, already-implemented branch for
  `policy.sku_blocked` returns `state: "COUNTERED"`, so leaving it a no-op executor would
  silently strand every `swap_sku` round (send a counter-offer, then apply nothing).
- Blocked since: 2026-09-03T00:00:00Z
- RESOLUTION (2026-09-03): Option (b), standing delegation under the approved plan (the
  plan itself names this exact simplification: "Implement it as 'drop the blocked SKU's
  line item'... add a short Q-NNN entry documenting this simplification"). No SKU
  similarity/substitution logic is implemented. `agents/merchant_agent.py`'s
  `apply_cart_delta` drops line items whose `sku` is in `policy.blocked_skus` for the
  `swap_sku` flag — identical code path to `remove_violating_tags`, keyed differently. A
  real substitution engine remains explicitly out of scope.
