# OpenStore — Open Questions
# Append-only. One entry per ambiguity. Never edit a resolved entry.

## Q-001 | stage: 00 | date: 2026-08-30T11:08:00Z
- What is ambiguous: Initial project structure and dependency decisions not fully specified in PRD
- Options considered: 
- Blocked since: 2026-08-30T11:08:00Z
- RESOLUTION:
- RESOLUTION (2026-09-15): Closed as historical — superseded by PRD v3.0 §0.3
  (same closure as Q-002/Q-003/Q-004). No code change, no live question.
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
  - PARTIAL CAPTURE (2026-09-15T05:35:32Z, networked machine): the script ran
    clean. Live duplicate-reference rejection carries code BAD_REQUEST_ERROR
    (description-text match, no usable code) — this confirms the marker-based
    `is_duplicate_reference_error` driver path and retires any remaining doubt
    about the Sept-10 fix; the legacy `REFERENCE_ID_ALREADY_EXISTS` string is
    now confirmed never-sent-live (kept only so old mocks still recover).
    NOT confirmed: cancel-already-paid 400 (script wrote its note-only
    placeholder — needs a real paid-link cancel) and all 4 webhook bodies
    (script writes synthetic placeholders — need comparison against a real
    test-mode delivery). Captured files committed on this checkout and the
    full suite re-verified green against them (711 passed); the commit message
    placeholder was corrected to the capture date. Steps 2–5 of
    the protocol remain pending; production stays gated.

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
- RATIFIED (2026-09-15): Option (b) confirmed live — `core/health.py`
  `metrics_text` still hand-rolls exposition, and `prometheus-client` appears
  in neither `pyproject.toml` nor `uv.lock` (verified this date). Correction
  to DECISION-035, which states "prometheus-client added as dependencies":
  no such dependency exists; the hand-rolled implementation is the shipped
  one and readiness/metrics are green on it. No code change.

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
- RATIFIED (2026-09-15): Option (b) confirmed live —
  `src/openstore/__init__.py` reads `metadata.version("openstore")`, version
  `0.1.0` canonical in `pyproject.toml`. No code change.

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
- SUPERSEDED (2026-09-15) as spent: the overnight run it governed is long
  complete (Stage 10 freeze, DECISION-016); headless GOLDEN-fixture exercise
  is now the permanent suite convention, and live-Discord/live-payment
  verification lives in PENDING-HUMAN / Q-007. No code change.

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

## Q-022 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: PRD §1.3 lists `core/aal.py` as a distinct file with the AAL ladder; the tree at cf7ca0a has no such file — the AAL ladder is entirely in `core/holdcancel.py` (lines 21–42, `AALLevel` IntEnum + `AAL_HOLD_SECONDS` + `AAL_LIABILITY`) and re-exported from `core/__init__.py` (lines 34–44). A stale `__pycache__/aal.cpython-312.pyc` may remain on disk in some checkouts. A direct `from openstore.core.aal import ...` would fail; callers going through `openstore.core` are unaffected. No prior DECISION or Q entry authorizes the consolidation, so the PRD source-of-truth and the tree disagree.
- Options considered: (a) update the PRD §1.3 diagram to remove `aal.py` and note in Appendix A that AAL now lives in `holdcancel.py`; (b) re-materialize `core/aal.py` as a thin re-export of `holdcancel`'s AAL surface so the diagram remains accurate literally; (c) leave the drift and document it in DECISIONS only (FORBIDDEN — the PRD is normative per §0.4). Note the second-order question: `poai.AALLevel` (plain `int` subclass, `core/poai.py:164`) and `holdcancel.AALLevel` (`IntEnum`, `core/holdcancel.py:21`) coexist with the same name — record whether Option (a)'s update should also collapse the two, or that stays a separate future Q.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (b) — re-materialized `src/openstore/core/aal.py` as a thin re-export of `holdcancel`'s AAL surface. This preserves the PRD §1.3 package layout literally while keeping the single source of truth in `holdcancel.py`. The two `AALLevel` types (`poai.AALLevel` as plain `int` subclass for bundle computation, `holdcancel.AALLevel` as `IntEnum` for the hold state machine) coexist intentionally — they serve different type contexts and are not collapsed. PRD Appendix A updated with note 5 documenting this.

## Q-023 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: `server.py`'s `/agent/mcp` bearer-token validator (lines 346–356) wraps token verification in a bare `except Exception:` and silently degrades to `client_id="anonymous"` with no scopes rather than returning 401. Downstream `_require_scope` in `mcp_server.py` (lines 13–20) enforces the scope check for exactly 4 of 14 tools (`create_cart`, `update_cart`, `checkout_initiate`, `checkout_confirm`) — the other 10 tools (`get_order`, `get_audit_log`, four `webauthn_*`, `list_campaigns`, `get_campaign`, `search_products`, `get_product`) are open to anonymous in this degraded mode. PRD R0.5 forbids silent coercion; PRD §3.10 declares OAuth scopes exhaustive and INV-9 requires asymmetric-token verification on the money path.
- Options considered: (a) surface every `validate_access_token` failure as HTTP 401 with a closed-set reason code — proposes reusing `auth.token_verification_failed` (introduced by Q-008 RESOLUTION); (b) add a new reason code (proposed name: `auth.mcp_bearer_invalid`) — not a REGISTRY edit, mirroring Q-004's authorization-before-implementation pattern; (c) ratify the current behavior as intentional and add `_require_scope` calls to every open tool so the enforcement point moves entirely to `mcp_server.py` — rejected on its face if we accept R0.5 literally, so record why the option exists for the resolver anyway.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (a) — catch `OAuthError` specifically (raised by `validate_access_token` with the closed-set reason codes including `auth.token_verification_failed`, `invalid_token`, `insufficient_scope`) and return `{"error": e.error, "message": e.description}` with the appropriate status code semantics. The bare `except Exception:` is replaced with `except OAuthError:` — any unexpected exception now propagates (fail loud). The 10 previously-anonymous tools now require valid authentication; `_require_scope` in `mcp_server.py` continues to enforce per-tool scope requirements.

## Q-024 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: `core/api.py::create_checkout` (lines 90–107) and `::confirm_checkout` (lines 376–390) both wrap `complete_assertion(...)` in `except Exception:` setting `assertion_verified = False` / `verified = False`. Any bug inside the RP path — a `KeyError` decoding CBOR, a `py_webauthn` exception the RP failed to re-raise as `WebAuthnError`, an unexpected DB error mid-consume — collapses to "not verified" indistinguishably from a genuine cryptographic reject. Cross-file consequence: the two call sites use different binding contexts (`create_checkout` binds `{"mode":"cart","cart_hash":...}`; `confirm_checkout` calls `complete_assertion` without an explicit binding argument), so a bundle whose evidence is built off `confirm_checkout`'s degraded path can pass the compiler's e5 check under a stale cart while showing `verified=False` on confirm. R0.5 forbids the bare-except; INV-2 requires the RP to be invoked on the money path.
- Options considered: (a) narrow to a closed exception set (`WebAuthnError` only) and let anything else propagate, mapping the propagated exception to a new reason code (proposed name to be resolved); (b) same as (a) but audit-log the raw exception type in `#alerts` first, mirroring the Q-005 AMENDMENT audit-only failure-type list; (c) leave the swallow but stamp an audit row of `resource_type = "assertion_verification_swallowed"` on every fall-through, so silent degradation becomes at least observable in the trace — rejected as it does not close the compliance gap. Record that resolving this changes only the `core/api.py` path — the RP itself already surfaces its finer-grained failure types per Q-005 AMENDMENT.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (a) — narrow both `except Exception:` blocks to `except WebAuthnError:`. The `complete_assertion` function in `core/webauthn_rp.py` already raises `WebAuthnError` for every rejection (bad signature, wrong/expired/reused challenge, sign-count regression, credential not found, UV flag missing, unsupported algorithm). Any other exception (bug in py_webauthn, DB error, etc.) now propagates up instead of being silently swallowed — fail loud per R0.5. The `create_checkout` path sets `assertion_verified = False` on `WebAuthnError` (unchanged behavior for legitimate rejections); the `confirm_checkout` path converts `WebAuthnError` to `CommerceError("assertion_required", ...)` (unchanged behavior for legitimate rejections). The Q-005 AMENDMENT audit-only failure types (`assertion_signature_invalid`, `challenge_mismatch`, etc.) continue to be recorded in `AuditLogEntry.detail` and `#alerts` by the RP itself.

## Q-025 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: `core/poai.py::create_poai_bundle` (lines 342–353) catches every signing exception with `except Exception: merchant_signature = None`. A bundle then serializes with a null signature; `verify_poai_bundle` (in-process, lines 379–408) does not check `merchant_signature` at all (only the hash chain), so the in-process verifier still returns `True`. The offline `openstore-verify` CLI's check 3 (`verify/checks.py:134–232`) either fails or degrades to `unverified_no_jwks` depending on `--merchant-jwks`. R0.5 forbids the silent swallow; PRD §3.3.10 lists `merchant_signature` as a chain top-level field (implied non-null on the ES256 path).
- Options considered: (a) raise a new exception type on signing failure and let the caller (`psp/router.py::_build_and_store_evidence`) decide whether to persist a `poai_bundle` at all; (b) persist the bundle with an explicit `chain.signing_error` sub-field naming the failure, so both the in-process viewer and the CLI can surface it — proposes a new field, not a new reason code; (c) keep the current behavior but tighten `verify_poai_bundle` to fail on `merchant_signature is None` — moves the fail-loud point to verify time; explicitly document the trade-off (evidence still built, but self-verify now denies). Note the interaction with Q-026.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (a) — added `PoAISigningError` exception class in `core/poai.py`; the bare `except Exception:` is replaced with `except Exception as e: raise PoAISigningError(...) from e`. Signing failures now propagate to the caller (`psp/router.py::_build_and_store_evidence`), which can catch and decide whether to persist an unsigned bundle (documenting the signing failure in the audit trail) or abort. This satisfies R0.5 (fail loud) while preserving the bundle structure — the caller controls the policy on unsigned bundles. The in-process `verify_poai_bundle` is unchanged (still verifies hash chain only); the CLI's check 3 behavior is unchanged (fails or degrades based on `--merchant-jwks`).

## Q-026 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: `core/poai.py::verify_poai_bundle` runs only the 9-section chain integrity check. `verify/checks.py` runs 14 checks — but (i) `check_re_execution` explicitly does not re-run `compile_decision` (docstring: "In a full verifier, the transcript would be replayed …"), so predicate e7 in PRD §3.5 is not enforced by the CLI either; (ii) `check_compiler_digest`'s `KNOWN_COMPILER_DIGESTS` set (line 421–423) contains a placeholder equal to `SHA256("")`; (iii) `check_merchant_signature` passes as `unverified_no_jwks` without `--merchant-jwks`. So there are actually three verify surfaces in play (in-process, CLI without JWKS, CLI with JWKS) with three different guarantees, and PRD §3.6 assumes one. R0.4 requires this be settled before any dispute-time story is trusted.
- Options considered: (a) declare `verify_poai_bundle` the "quick-look" surface (rename in its docstring, keep the reduced scope) and make CLI the sole authoritative verifier — document each of the three CLI gaps ((i)/(ii)/(iii)) as its own smaller Q or a scoped follow-up; (b) bring `verify_poai_bundle` up to CLI parity — same 14 checks — closing the in-process/offline split; (c) split the difference by making the in-process surface run checks 1..4 (schema, chain, signature, time_anchor) and leaving 5..14 CLI-only — this matches what a merchant server can honestly check without a per-checkout persisted assertion (see Q-019). Cite Q-019's "e2..e7 out of scope this phase" resolution as prior art for what an on-server bundle can honestly assert.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (c) — updated `verify_poai_bundle` in `core/poai.py` to run checks 1-4 (schema, chain_integrity, merchant_signature structural, time_anchor). The docstring now explicitly labels it the "quick-look surface" and names the CLI (`openstore-verify`) as the authoritative verifier for all 14 checks. The three CLI gaps are documented here:
  (i) `check_re_execution` (check 10) does not re-run `compile_decision` — structural transcript validation only.
  (ii) `check_compiler_digest` (check 9) uses a placeholder `KNOWN_COMPILER_DIGESTS = {SHA256("")}` — a real pinned digest set requires a future RESOLUTION.
  (iii) `check_merchant_signature` (check 3) passes as `unverified_no_jwks` without `--merchant-jwks` — JWKS directory required for cryptographic verification.
  These gaps are not blockers for this stage; the CLI is the authoritative dispute-time verifier. The in-process surface is a lightweight health check for server-side bundle integrity.

## Q-027 | stage: 11 | date: 2026-09-04T00:00:00Z
- What is ambiguous: `psp/razorpay_driver.py::refund_checkout` (line 564) calls `client.payment.refund(payment_id, {...})` with `checkout.psp_payment_link_id`. Razorpay's Payments Refund API expects a `pay_...` id, not a `plink_...` id. No `payment_link → payment` mapping exists anywhere in the file. Tests are mocked and do not catch this. INV-8's cancel-if-unpaid → refund-if-paid fallback (DECISIONS §11.1.2) goes through this exact code path, so the fallback is broken against the live API. Not confirmed against live Razorpay in this session (R0.7 sandbox has no `api.razorpay.com` egress — see Q-007).
- Options considered: (a) add a `payment_id` lookup step — call `client.payment_link.fetch(link_id)` and read `payments[-1].id` before `payment.refund(...)`; (b) persist the payment id on the `Checkout` row at webhook receipt time (`_apply_payment_link_paid` sees the payload `payment.entity.id`) and read it from `checkout.psp_payment_id` at refund time — proposes a new nullable column, requires an Alembic migration in the resolving stage; (c) both — (b) as the primary path, (a) as the recovery path when the column is null (a checkout that pre-dates the migration). All three options are gated by R0.7 live capture per Q-007's recovery protocol before landing in production. Explicitly say this Q remains OPEN until the human resolver either allows (a)/(b)/(c) OR authorizes a `scripts/capture_constants.py` re-run to confirm the refund-endpoint id-type against test-mode.
- Blocked since: 2026-09-04T00:00:00Z
- RESOLUTION (2026-09-04): Option (c) — added `psp_payment_id` column to `Checkout` (migration 0006, nullable for legacy checkouts). Updated `refund_checkout` in `psp/razorpay_driver.py` to prefer `checkout.psp_payment_id` (primary path); if null, fetch the payment_link via `client.payment_link.fetch(link_id)` and read `payments[-1].id` (fallback path), then cache the payment_id on the checkout for future refunds. The webhook handler `_apply_payment_link_paid` does not yet populate `psp_payment_id` (the golden fixtures Q-007 don't include the payment_id in `payment_link.paid`; live capture needed per Q-007 recovery protocol). This resolves the code-level fix; live verification of the payment_id field remains gated by Q-007.


## Q-028 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: `core/campaigns.py` raises nine `CampaignValidationError` reason codes
  (`campaign.not_found`, `campaign.sku_not_found`, `campaign.discount_out_of_bounds`,
  `campaign.invalid_window`, `campaign.sku_on_blocked_list`, `campaign.empty_content`,
  `campaign.injection_content`, `campaign.no_webauthn_approval`, `campaign.max_active_exceeded`)
  and every one of them is absent from `REGISTRY.json` — neither the codes themselves nor a
  `campaign.*` entry in `error_namespaces`. This is a live R0.2 violation ("NEVER invent an
  identifier"). It survived because `scripts/registry_diff.py` is a shape validator only: it
  checks required keys, `enums_are_exhaustive`, and list duplicates, and never scans source for
  raised identifiers, so it prints nothing and exits 0 with the violation in place. The
  registered `orchestration.*` namespace has no user anywhere in the tree, which suggests it was
  reserved for exactly these codes.
- Options considered: (a) register a `campaign.*` namespace and add all nine codes to
  `reason_codes` — smallest diff, no call-site churn, and `campaign.sku_not_found` reads
  correctly at the point it is raised; (b) remap all nine onto the reserved `orchestration.*`
  namespace so no new namespace is created — honours the apparent original intent but renames
  nine identifiers across `core/campaigns.py`, `tests/stage08/`, and `tests/redteam/`, buying
  nothing semantically since `orchestration.*` would then mean "campaign validation" and
  nothing else; (c) leave both and simply document the drift — FORBIDDEN, R0.2 admits no
  documented exception, and the whole point of the closed set is that it is executable.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION (2026-09-05): Option (a), operator-ratified. `campaign.*` added to
  `error_namespaces`; the nine existing codes plus two required by the lifecycle repair
  (`campaign.webauthn_verification_failed` for a real assertion rejection, and
  `campaign.invalid_state_transition` for an out-of-order lifecycle move) added to
  `reason_codes` — eleven in total. `orchestration.*` stays reserved and unused; it is NOT
  removed, since removing a registered namespace is a wider change than this question asks.
  `scripts/registry_diff.py` is extended in the same commit to scan `CampaignValidationError(`
  construction sites and diff their first argument against `reason_codes`, so this class of
  violation cannot recur silently — the differ's blindness is the root cause, not the codes.

## Q-029 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: PRD §9.7 states the orchestrator "CANNOT publish without a WebAuthn
  approval", and INV-13 requires every feed offer to be signed. `core/campaigns.py::activate_campaign`
  enforces neither: it accepts any truthy `webauthn_assertion` dict and never calls the RP, so
  `{"x": 1}` publishes a campaign into the signed feed. Repairing it needs an assertion binding
  context, and the existing binding modes (`policy`, `cart`, `amendment`) all describe a
  different ceremony. Binding modes are not a REGISTRY closed set, so R0.2 does not gate this,
  but R0.3/R0.4 do gate inventing the value silently.
- Options considered: (a) add a `campaign` binding mode carrying the `campaign_id`, so an
  assertion approving campaign A cannot be replayed to approve campaign B — direct precedent in
  DECISION-021, which added the `amendment` mode the same way; (b) reuse the `policy` mode —
  rejected, it would make a policy-signing assertion silently sufficient to publish an offer,
  which is a privilege escalation across two unrelated ceremonies; (c) sign the campaign with
  the merchant's ES256 feed key instead of a WebAuthn assertion — rejected, that key is held by
  the process, so it proves no human approved anything, which is the entire point of §9.7.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION (2026-09-05): Option (a), operator-ratified. New WebAuthn binding mode `campaign`,
  shaped `{"mode": "campaign", "campaign_id": "<id>"}`, mirroring `amendment`. `activate_campaign`
  now calls `complete_assertion` from `core/webauthn_rp.py` with that binding and raises
  `campaign.webauthn_verification_failed` on rejection. Because the binding carries the
  `campaign_id`, the challenge is bound to one specific campaign and an approval cannot be
  replayed onto another.

## Q-030 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: repairing the PRD §9.4 lifecycle (`DRAFT -> PENDING_APPROVAL -> ACTIVE ->
  (PAUSED | EXPIRED)`) and giving the merchant a real surface needs route names that do not
  exist in `REGISTRY.json`: a pause transition, and read surfaces for campaigns and orders. PRD
  Part 6 pins `/campaign/<campaign_id>/approve` and `/reject` but no pause, even though `PAUSED`
  is a registered `campaign_states` value with no way to reach it. Separately, the manifest at
  `surfaces/wellknown.py` advertises a UCP-shaped capability set nowhere, while UCP's discovery
  convention is a fixed well-known path.
- Options considered: (a) add `/campaign/<campaign_id>/pause`, `/admin/campaigns`,
  `/admin/orders`, and `/.well-known/ucp` to `routes` — note that `/admin/*` is already
  registered as a wildcard, so the two concrete admin paths are a narrowing, not an expansion,
  and follow the DECISION-018 precedent of listing concrete paths beside a retained wildcard;
  (b) reach `PAUSED` by overloading `/campaign/<id>/reject` with a body flag — rejected, it
  conflates a terminal rejection with a reversible pause and makes the audit trail lie;
  (c) skip pause entirely and leave `PAUSED` permanently unreachable — rejected, R0.3 holds
  enums exhaustive, and a registered state no code can enter is the same defect this whole
  round is fixing.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION (2026-09-05): Option (a), operator-ratified. Four routes added to `REGISTRY.json`
  `routes`: `/campaign/<campaign_id>/pause`, `/admin/campaigns`, `/admin/orders`,
  `/.well-known/ucp`. The `/admin/*` wildcard is retained alongside the two concrete paths per
  the DECISION-018 precedent. Campaign drafting deliberately gets NO route: PRD §9.2 makes the
  merchant a reviewer, not an author, so drafting is triggered by the `openstore campaign draft`
  CLI command and the Studio is a review surface only. This is why no `/campaign/draft` or
  `POST /campaign` appears here.

## Q-031 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: `surfaces/wellknown.py`'s `protocols[]` array advertises
  `{"name": "acp", "version": "2024-11-01", "endpoint": "<origin>/agent/acp", "auth":
  ["oauth2_bearer", "http_message_signature"], "authority_schemes": ["acp_delegated_token",
  "native_webauthn"]}`. Three things are wrong with it. ACP has never published a
  `2024-11-01` release — the real spec versions are 2025-09-29, 2025-12-12, 2026-01-16,
  2026-01-30 and 2026-04-17, all post-dating that string, and the original PRD text carried
  `[verify-at-build]` on the version precisely because it was never pinned (R0.7). The
  endpoint it names, `server.py`'s `POST /agent/acp`, returns `{"error": "not implemented"}`.
  And `acp_delegated_token` is advertised as an accepted authority scheme with no code path
  that accepts one. An ACP-aware agent that trusts the manifest fails on contact — a manifest
  is a promise, so advertising a protocol the sidecar does not speak is worse than
  advertising none. Separately, UCP (Google/Shopify with Etsy, Wayfair, Target, Walmart;
  announced 2026-01-11) publishes business capabilities at a fixed well-known path, and its
  capability model maps nearly 1:1 onto surfaces this sidecar already serves — but nothing
  in the tree exposes it.
- Options considered: (a) implement `/agent/acp` against the 2026-04-17 spec — real work,
  and ACP's Shared Payment Token collides with R0.10/INV-2 (the sidecar would have to accept
  a delegated payment credential), so it is a stage of its own, not a manifest fix;
  (b) drop the `acp` entry from `protocols[]`, keep the route registered and stubbed with an
  honest `not_implemented` body, and add `/.well-known/ucp` declaring only capabilities that
  genuinely work; (c) leave the entry and pin a real ACP version string — rejected, it would
  make the lie more credible rather than less, since the endpoint still does nothing.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION (2026-09-05): Option (b), operator-ratified. The `acp` entry is removed from
  `protocols[]` and replaced with a `ucp` entry pointing at the new `/.well-known/ucp`.
  `POST /agent/acp` stays mounted (REGISTRY pins it) but returns `not_implemented` with a
  pointer to `/agent/mcp` and the UCP manifest. The UCP manifest declares exactly two
  capabilities — `dev.ucp.shopping.checkout` over the existing cart/checkout MCP tools and
  `dev.ucp.shopping.discount` over the signed campaign feed — and deliberately omits
  fulfilment and order management, which are not implemented. Its `payment_handlers` entry
  states `flow: "hosted_payment_link"` rather than a delegated token, because the buyer
  completes payment on the PSP's hosted page and no agent ever holds a credential (R0.10).
  A sentinel test asserts every operation named in the UCP manifest is in
  `REGISTRY.mcp_tools`, so the manifest cannot drift into promising a tool that does not
  exist. `authority.scheme_capped_acp_delegated_token` stays in `authority_reason_codes`:
  it is the cap applied IF such a scheme is ever presented, and removing it is out of scope.

## Q-032 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: the buyer leaves the conversation to pay. `checkout_initiate` creates a
  Razorpay hosted payment link and `build_shop_result_embed` posts its `short_url` as a "Pay
  here" field; nothing in chat tracks the payment after that. Completion arrives only if the
  webhook fires (`psp/router.py::_push_chat_notification`), so a late or lost webhook leaves
  the buyer with no signal at all until the hold-release DM tells them the order died. Every
  competing protocol closes this seam differently — ACP's Shared Payment Token is designed so
  the buyer never leaves the agent surface, UCP composes tokenized instruments — and R0.10 /
  INV-2 forbid the AGENT holding a payment credential, though not the sidecar. Two candidate
  repairs were scoped and neither is safe to land as a tail-end change.
- Options considered: (a) edit the original Discord message in place as state changes ("Pay
  here" -> "Payment received") — needs a new nullable `Checkout.discord_message_id` column and
  an Alembic migration, plus the webhook worker editing a message in a channel it did not
  post to, which is new cross-process plumbing on the money path; (b) poll `payment_link`
  status from the existing `_hold_release_loop` so a lost webhook is still reconciled before
  the hold is released — small in diff terms, but it puts a live PSP fetch inside the loop
  that INV-4/INV-8/INV-11, the ledger golden vectors, and the mutmut money-path config all
  guard, and it is exactly the surface that most deserves its own red-team pass rather than
  a change made in passing; (c) add a UPI intent deep link (`upi://pay?pa=...`) beside the
  hosted link so mobile buyers get one tap into their UPI app — REJECTED OUTRIGHT, not
  deferred: the merchant's VPA is in no config field and appears nowhere in the tree, so
  constructing the link means inventing a payee identifier. R0.7 forbids inventing a constant
  and R0.3 forbids inventing a value; a guessed `pa=` either fails or, worse, points real
  money at the wrong payee. It cannot be done honestly until a VPA is a declared config field
  with a live test-mode capture behind it (the Q-007 protocol).
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION: none yet. Recorded rather than implemented, deliberately: (a) and (b) both
  touch the money path and each warrants its own stage with its own adversarial tests, and
  (c) is not implementable without new configuration. The seam is real and is the single
  place OpenStore's UX is behind ACP; it is not closed by this round of work.
- CLOSED (2026-09-15) as implemented by DECISION-035 (Stage 16): both halves
  landed — pre-release PSP reconcile in the hold-release loop (bounded, never
  blocks release) plus `discord_message_id` stamp/edit plumbing with
  `set_order_message` ownership check. README "Payment stays in the
  conversation" records the live behavior. Option (c) (UPI intent) remains
  REJECTED outright per the original entry. No code change.

## Q-033 | stage: 12 | date: 2026-09-05T00:00:00Z
- What is ambiguous: the AAL ladder the README sells is unreachable from chat. `BuyerAgent.confirm()`
  and `BuyerAgent.hold_monitoring()` have no call sites in any bot path, so the
  `checkout_confirm` / per-cart-assertion route never runs from Discord. The consequence is
  visible in the Policy Studio: with `no_human_authority` left unchecked (its default), every
  chat order is denied `assertion_required`, because there is no chat ceremony that can
  produce a fresh per-cart WebAuthn assertion. The Studio copy states this plainly, so a
  buyer is not misled — but it means the practical choice is "sign a standing policy that
  needs no per-cart tap" (AAL1) or "cannot shop from chat at all". AAL2/AAL3, and the
  liability ladder that rests on them, are only reachable through a direct API caller.
- Options considered: (a) add a `cart` HandoffKind so a pending checkout can park a chat
  conversation, render `/intent/studio?token=...` with a challenge bound to
  {"mode": "cart", "cart_hash": ...} (the binding mode already exists in
  `core/webauthn_rp._binding_matches`), and resume through the same
  `_consume_and_resume` path policy signing uses — the machinery is all present, but
  `HandoffKind` is a closed set fixed by DECISION-017, so this needs an enum value, an
  Alembic migration, and its own red-team coverage that a cart assertion cannot be replayed
  onto a different cart; (b) leave it and treat AAL1 as the chat ceiling, documenting that
  AAL2/AAL3 are API-only — honest, but it quietly caps the product's central claim;
  (c) auto-check `no_human_authority` for chat-originated policies — REJECTED, it would
  silently weaken the authority model to make a UX problem disappear, which is the exact
  inversion R0.9 exists to prevent.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION (2026-09-10): Option (a), operator-authorised (full-production
  build, S16). `HandoffKind.CART` added (migration `0009_cart_handoff`, PG
  `ALTER TYPE ... ADD VALUE`, SQLite no-op) with `handoffs.cart_payload`
  (`{cart_id, cart, cart_hash, policy_id}`). New ceremony mirrors the
  amendment path: `GET /intent/studio?token=` renders `cart_studio.html` with
  an inline `{"mode": "cart", "cart_hash"}` challenge;
  `POST /intent/cart/<cart_id>/approve` verifies the assertion against that
  binding (replay onto a different cart fails `assertion_required`) then
  creates the checkout with `assertion_verified=True` via the existing
  `create_checkout_from_policy` path; `/reject` consumes without creating.
  New MCP tool `create_cart_handoff` (scope `catalog:read`) for federated
  buyers. Routes added to REGISTRY alongside the implementation. AAL grades
  by amount (small cart → AAL3), strictly above the old chat ceiling.

## Q-034 | stage: 14 | date: 2026-09-05T00:00:00Z
- What is ambiguous: the merchant has no conversational surface at all — only CLI, Studio
  pages gated by an `X-Operator-Id` header a plain browser click cannot set, and one-way
  `#merchant-trace` pushes the merchant can read but never reply into. The PRD never
  specifies whether the merchant should get a chat agent, what it should be allowed to see,
  or how access to it should be controlled — genuinely silent, not a mechanical gap like
  Q-028's `campaign.*` reason codes.
- Options considered: (a) no merchant bot at all — leave CLI/Studio as the only surfaces;
  (b) a read-only reporting bot with no access control, responding to any DM; (c) the same,
  but gated behind an allow-list of authorized Discord user IDs.
- Blocked since: 2026-09-05T00:00:00Z
- RESOLUTION: (b), per DECISION-028. Read-only reporting only — the action set contains no
  mutating action, so unrestricted access exposes revenue/exposure/campaign data but can
  never let anyone approve, reject, pause, or otherwise change anything. Access control was
  a live option; the user explicitly chose no restriction, matching BuyerBot's existing lack
  of gating, over building an allow-list.

## Q-035 | stage: 16 | date: 2026-09-10T00:00:00Z
- What is ambiguous: `RazorpayError`/`CommerceError`/`OAuthError` reason codes raised
  across `psp/razorpay_driver.py` and `core/api.py` were never registered in
  REGISTRY.json (`psp.checkout_not_found`, `psp.*`, `webhook.*`, unprefixed
  `policy_not_found`/`checkout_not_found`/`invalid_state`/`invalid_cancel_token`,
  OAuth `invalid_client`/`invalid_grant`/`invalid_token`/`insufficient_scope`).
  Same class as Q-028 (`campaign.*`): `scripts/registry_diff.py` only scans
  `CampaignValidationError`, so the gap is invisible to the build gate.
  OAuth `invalid_client`/`invalid_grant`/`invalid_token` are RFC 6749 protocol
  error names in the HTTP `error` field (not REGISTRY reason_codes) and stay
  unregistered by design; `insufficient_scope` (bare, oauth.py:409) is unified
  to the registered `auth.insufficient_scope`.
- Options considered: (a) register every raised literal as-is (legitimises
  unprefixed legacy names); (b) normalise to namespaced closed sets now, before
  any external client exists — `CommerceError` unprefixed codes move into
  `checkout.*`/`policy.*`, PSP codes into `psp.*`/`webhook.*`/`checkout.*`,
  bare `insufficient_scope` becomes `auth.insufficient_scope`; (c) leave the
  drift documented only — FORBIDDEN (R0.2).
- Blocked since: 2026-09-10T00:00:00Z
- RESOLUTION (2026-09-10): Option (b), operator-authorised (full-production
  build, money path open). Normalise now. `scripts/registry_diff.py` is widened
  to scan all reason-code-carrying exceptions (`CampaignValidationError`,
  `RazorpayError`, `CommerceError`, `HandoffError`, `OAuthError` for
  `auth.*`-prefixed codes only, `WebAuthnError` for registered codes only).
  New REGISTRY `reason_codes`: `policy.not_found`, `checkout.invalid_state`,
  `checkout.invalid_cancel_token`, `psp.checkout_not_found`,
  `psp.no_payment_link`, `psp.no_payment`, `psp.no_payment_id`,
  `psp.create_failed`, `psp.cancel_failed`, `psp.refund_failed`,
  `psp.amount_invalid`, `psp.currency_mismatch`, `psp.live_key_forbidden`,
  `psp.duplicate_unrecoverable`, `webhook.unknown_event`,
  `webhook.missing_reference_id`, `webhook.invalid_transition`.
  `psp.checkout_not_found` is kept distinct from `checkout.not_found` (PSP
  driver vs API lookup — different failure families sharing a name stem,
  mirrors the Q-016 authority-namespace note).

## Q-036 | stage: 17 | date: 2026-09-14T00:00:00Z
- What is ambiguous: `POST /agent/mcp` (`server.py`) speaks a bespoke envelope
  (`{"tool","arguments"}`) while the manifest advertises MCP `2025-06-18`
  (`surfaces/wellknown.py`). The MCP spec (verified 2026-09-14:
  modelcontextprotocol.io/specification/2025-06-18) requires JSON-RPC 2.0 with
  an `initialize` handshake, `tools/list` (+ `inputSchema` per tool) and
  `tools/call`, numeric envelope error codes, and `id` never null. No stage
  spec covers the transport cutover; PRD S6.3 pins the tool *semantics* (kept
  byte-identical underneath) but not the envelope. R0.2: the method names and
  the protocol-version string are new identifiers.
- Options considered: (a) hard cutover to the JSON-RPC wire path, legacy
  shape answered `400`/`-32600`, own `HttpMCPClient` migrated in the same
  commit; (b) dual-serve legacy + wire behind a compat shim; (c) leave the
  bespoke shape (rejected — the manifest would keep promising what stock
  clients cannot speak, the DECISION-026 failure mode).
- Blocked since: 2026-09-14T00:00:00Z
- RESOLUTION (2026-09-14): Option (a), operator-authorised (flagship
  reprioritisation: MCP first, hard cutover). Pin `protocolVersion`
  `2025-06-18`; methods `initialize`, `notifications/initialized`,
  `tools/list`, `tools/call`, `ping`. Envelope errors use JSON-RPC numeric
  codes only (`-32700`/`-32600`/`-32601`/`-32602`/`-32001` auth) — no new
  REGISTRY `reason_codes`; business rejections keep their closed-set codes
  inside `tools/call` `isError` content. `InProcessMCPClient` keeps internal
  dispatch (not a protocol surface). `handle_mcp_request` stays the tool
  execution core; the wire handler wraps it and maps escaping
   `CommerceError` (scope gates outside per-tool `try`) to `isError` content
   instead of the current unhandled 500.

## Q-037 | stage: 18 | date: 2026-09-14T00:00:00Z
- What is ambiguous: First real-catalog adapter (Shopify, read-only). Live
  probe of the operator's dev store (2026-09-14) forced four decisions the PRD
  (YAML-only catalog) never contemplates: (i) 18/19 sample variants carry
  `sku: null` — the normalized item shape requires a merchant-authored `sku`
  key the compiler and PoAI attestations join on; (ii) shop currency is USD
  while DECISION-015 fixes the sidecar to INR-only; (iii) Admin tokens expire
  in 86399s, so no static token can be stored; (iv) REST pagination is
  unbounded (`Link: rel=next`). Also new identifiers: `shopify:` config block
  + `store_domain`/`client_id`/`client_secret` keys, `SHOPIFY_*` env names,
  `surfaces/shopify_catalog.py` module. REGISTRY.json needs no change
  (registry_diff scans reason codes only; no new code is raised — all
  failures are RuntimeError/ValueError fail-loud, the pre-existing adapter
  convention).
- Options considered:
  (i) SKU-less variants: (a) skip loudly with a count, fail loud only when
  zero usable variants remain; (b) synthesize keys from variant GIDs —
  rejected, compiler allowlists and attestations must join on
  merchant-authored keys; (c) fail the whole sync on the first missing SKU —
  rejected, one untidy variant would nuke the catalog.
  (ii) Currency: (a) fail loud on any shop currency != merchant currency (no
  conversion — converting money invents money); (b) convert — FORBIDDEN.
  (iii) Tokens: mint at runtime via client-credentials grant, cache to
  expiry-60s, persist only ID/secret in `.env` (never the token).
  (iv) Pagination: follow `Link rel=next`, cap 40 pages (~10k products), fail
  loud past the cap.
- Blocked since: 2026-09-14T00:00:00Z
- RESOLUTION (2026-09-14): Options (i-a), (ii-a), (iii), (iv),
  operator-authorised (live probe ran in-session). `load_catalog` branches to
  the Shopify source iff `config.shopify` is set, otherwise YAML byte-identical
  to today. TTL cache 60s keyed by domain, separate from the YAML path+mtime
  cache. Scope posture: token response must carry `read_products`
  (`write_products` implies it and is tolerated, but the recommended version
  is read-only — least privilege for a credential that only ever GETs).

## Q-039 | stage: 19 | date: 2026-09-14T00:00:00Z
- What is ambiguous: Flagship webchat slice A (anonymous storefront-lite)
  needs a new GET route serving a static page. No stage spec names it;
  REGISTRY `routes` is a closed set and
  `tests/sentinel/test_route_table_snapshot.py` enforces both directions
  (mount-without-registry and registry-without-mount both fail the build).
  R0.2 forbids naming the route without a prior RESOLUTION.
- Options considered: (a) new `/chat` route + REGISTRY entry, static
  `chat.html`, ungated (discovery surface like `/`, unlike the gated
  `/agent/*` feeds it reads); (b) reuse `/` by replacing the Stage-1
  storefront stub — rejected, `/` sits on the sentinel infra allowlist and
  hiding a commerce surface there repeats the shadow-route pattern Q-015
  removed; (c) no page.
- Blocked since: 2026-09-14T00:00:00Z
- RESOLUTION (2026-09-14): Option (a), operator-authorised (flagship webchat
  slice A). Same-origin relative URLs only (CORS-clean on any origin, no
  SID-5 change). Anonymous GETs only — catalog + signed campaign feed +
  evidence/studio links; no mutations, no new scopes, no new reason codes,
  so `registry_diff.py` is unaffected. Checkout stays on the buyer agent /
  studios; the page never takes payment input.

## Q-040 | stage: 20 | date: 2026-09-15T00:00:00Z
- What is ambiguous: UCP MCP catalog binding aliases (README "How far from any
  agent": UCP's MCP catalog tools are `search_catalog` / `lookup_catalog` /
  `get_product` with `meta.ucp-agent` + `ucp` response envelopes; OpenStore
  uses `search_products` with a bespoke shape; DECISION-036 deferred vocabulary
  aliases until after wire conformance). No stage spec covers the alias
  cutover; PRD S6.3 pins tool semantics, not the UCP vocabulary. R0.2:
  `search_catalog` / `lookup_catalog` are new identifiers (`mcp_tools`,
  `TOOL_NAMES`, `TOOL_SCHEMAS`, dispatch, manifest operations). Verified
  2026-09-15 against the UCP Catalog MCP binding
  (ucp.dev/2026-08-25/specification/shopping/catalog/mcp/): tools
  `search_catalog` (Search) / `lookup_catalog` + `get_product` (Lookup); every
  request carries arguments `{meta: {ucp-agent: {profile}}, catalog: {...}}`;
  responses carry a required `ucp` envelope (`{version, capabilities}`) plus
  `products[]` (search/lookup) or `product` (get_product); lookup partial
  success returns found products + info/`not_found` messages at transport
  success level; implementations MAY support SKU as a secondary identifier;
  SHOULD accept >= 10 ids per lookup, MAY enforce a max with `-32602`.
- Options considered: (a) alias + envelope inside the existing text-content
  wire path: `search_catalog` / `lookup_catalog` as thin adapters over
  `search_catalog_items` / `get_catalog_item` (same handlers underneath, R0.9),
  SKU as the canonical identifier, `meta` tolerated-and-ignored (anonymous
  catalog reads predate UCP; failing closed on missing `meta` would break
  existing clients), `context` / `filters` / `signals` / `attribution`
  accepted-and-ignored (provisional signals per spec; enforcement stays at
  checkout via R0.8), lookup misses answer success + `messages` (never
  `isError`), no batch cap (SHOULD >= 10 trivially satisfied; MAY-cap
  deferred), `ucp` envelope version `2026-08-25`, `get_product` extended to
  `{sku}` | `{id}` | `{catalog: {id}}` with a superset response
  `{item, product, ucp}`; (b) a separate `/ucp/mcp` endpoint — rejected, the
  manifest advertises MCP transport at `/agent/mcp` and a second endpoint
  splits the tool surface; (c) `structuredContent` responses — rejected, the
  wire path (DECISION-036) standardised on text content + `isError` and every
  client parses it; the UCP envelope rides inside the JSON text.
- Blocked since: 2026-09-15T00:00:00Z
- RESOLUTION (2026-09-15): Option (a), operator-authorised (slice picked
  2026-09-15; wire conformance already landed). Add `search_catalog` +
  `lookup_catalog` to REGISTRY `mcp_tools` alongside the implementation (Q-008
  sequencing: together, never before). No new reason codes (misses are
  messages, not rejections; malformed ids reuse `internal_error`), no new
  routes/scopes. `search_catalog` inputSchema required `[]`;
  `lookup_catalog` required `["catalog"]`. `get_product` inputSchema required
  `[]` (was `["sku"]`) — `{}` now answers `isError` `catalog.sku_not_found`
  instead of envelope `-32602`; the pinned stage-17 test is updated in the
  same commit. The UCP manifest gains `dev.ucp.shopping.catalog.search`
  (`[search_catalog]`) and `dev.ucp.shopping.catalog.lookup`
  (`[lookup_catalog, get_product]`); the stage-06 capability test is updated.
  The internal buyer keeps `search_products` (no client change).

## Q-041 | stage: 21 | date: 2026-09-15T00:00:00Z
- What is ambiguous: Third-party-verifiable conformance (the xpaysh
  `conformance-fixtures` precedent: "a manifest is a promise" applies to wire
  claims too). The repo pins crypto/compiler goldens in `tests/GOLDEN/` but
  nothing pins the MCP wire bytes or the UCP discovery documents a stranger's
  agent actually speaks to. No stage spec covers fixtures; PRD S6.3 pins tool
  *semantics*, not a fixture policy. Decisions needed: (i) fixture scope and
  equality rule; (ii) what origin the discovery goldens pin (manifest URLs
  embed the request origin, so byte-exact goldens pin `http://testserver`);
  (iii) `tools/list` pins all 22 schemas (large, deliberately brittle);
  (iv) `serverInfo.version` pins the installed package version (breaks
  deliberately on release bumps); (v) the manifest's UCP `version:
  "2026-01-11"` (announcement version, DECISION-026) vs the `2026-08-25`
  binding version inside tool envelopes — restate or change?
- Options considered: (a) byte-exact canonical-JSON goldens under
  `tests/GOLDEN/conformance/` for the deterministic surfaces (initialize,
  tools/list, search/lookup/get_product incl. miss shapes, unknown-tool
  isError, legacy-shape rejection, missing-catalog `-32602`, both discovery
  manifests) over a fixed 2-item seeded catalog, plus structural
  origin-parameterized assertions alongside (all manifest URLs start with the
  request origin) so the portable claim survives outside `testserver`;
  (b) subset-match fixtures only — rejected, weaker than the repo's golden
  discipline and blind to envelope drift; (c) change the manifest version to
  `2026-08-25` — rejected, out of scope: the manifest version predates this
  slice (DECISION-026) and no spec requires them to match; recorded here as
  known, not changed.
- Blocked since: 2026-09-15T00:00:00Z
- RESOLUTION (2026-09-15): Option (a), operator-authorised (slice picked
  2026-09-15). No new identifiers of any kind: no routes, tools, codes,
  scopes, or migrations — test files + fixture files only, so REGISTRY.json
  is untouched. Fixture updates on future tool/version changes follow golden
  discipline (the vector is right only after human review; code drift breaks
  the build deliberately). Deviations from upstream specs asserted by these
  fixtures are exactly the Q-040 set (`meta` optional, no batch cap, SKU as
  identifier, text-content envelope) — restated, not reopened.

## Q-042 | stage: 22 | date: 2026-09-15T00:00:00Z
- What is ambiguous: Buyer-in-browser checkout (webchat slice B — the
  browser-safe auth design DECISION-039 deferred to its own Q-entry). `/chat`
  is browse-only; the POLICY/CART handoff ceremonies, `cart_studio.html`,
  `cart_approve`, and `cancel_checkout_by_id` all exist but are only reachable
  via a Discord buyer agent. No stage spec names browser routes; REGISTRY
  `routes`/`reason_codes` are closed sets (R0.2). Sub-questions: (i) browser
  identity without logins/cookies; (ii) cart-line shape the compiler trusts;
  (iii) closed-set codes for unknown-SKU / bad-qty edge rejections;
  (iv) `_consume_and_resume` auto-resume would mint a stray checkout + DM for
  a web signer when `buyer_bot_enabled`; (v) rate limiting on anonymous mint;
  (vi) what the status surface may expose (pay link? cancel token?);
  (vii) campaign discounts in browser carts.
- Options considered:
  (i) `chat_platform="web"` + `buyer_key` (`token_urlsafe(16)` in
  `localStorage`, validated `^[A-Za-z0-9_-]{16,64}$`, fits `chat_user_id`
  max 64); no cookies/sessions/secrets in the browser; authority is ALWAYS a
  passkey tap — the browser path mints a CART handoff and goes through
  `cart_approve`, never `create_cart`/`checkout_initiate`, so a stolen key
  alone authorizes nothing. `chat_platform` is not a REGISTRY closed set
  (free string today) — noted, not registered.
  (ii) Lines stamped server-side as `{sku, qty, unit_minor, tags}` from
  `load_catalog` (the established line shape, stage-16 CART precedent; R0.8
  — client prices never trusted); no `campaign_id` attachment this slice
  (full price; campaign-in-browser is future work).
  (iii) Unknown SKU → `404` + `catalog.sku_not_found`, REGISTERED here (it
  is already raised in-tree by `get_product` — same legitimize class as
  DECISION-023 `campaign.*`); bad qty (non-int/`<=0`) → `422` +
  `policy.qty_invalid` (same meaning as the compiler check); envelope shape
  via pydantic models (framework 422s, `CartDecision` precedent).
  (iv) `_consume_and_resume` auto-resumes only when
  `chat_platform=="discord"` (found in scoping: otherwise a web signing
  mints a checkout + pay link the browser never sees).
  (v) No rate limiting (anonymous handoff mint ≈ anonymous MCP search
  exposure; the passkey ceremony is the real gate; future Q if abused).
  (vi) Status exposes `short_url` (refresh recovery) + `evidence_url` when a
  bundle exists; `cancel_token` is NEVER exposed — cancel is an
  ownership-checked POST reusing `cancel_checkout_by_id`.
  (vii) No item-count cap (tiny catalogs, 1h TTL rows — same call as Q-040).
- Blocked since: 2026-09-15T00:00:00Z
- RESOLUTION (2026-09-15): All of the above, operator-authorised (slice
  approved 2026-09-15). Routes `/web/cart`, `/web/order/<checkout_id>`,
  `/web/order/<checkout_id>/cancel` added to REGISTRY alongside the
  implementation (Q-008 sequencing). Ungated like `/chat` (readiness gate
  only — `is_gated` is about readiness, not auth). No new scopes, tools,
  HandoffKind values, binding modes, or migrations.
