# Stage 03 — Intent Compiler goldens + WebAuthn RP + Policy Studio

Self-contained stage contract per PRD v3.0 Part 10. You do not need the other stage files.

## READ FIRST
- `AGENTS.md` (repo root) — the ten rules bind this stage.
- `OPENSTORE_PRD_v3.md` §3.1, §3.2 (incl. §3.2a–c), §3.5, Part 4, Part 6, Part 7.
- `REGISTRY.json` — complete for this stage. Do not edit it.
- Existing code from Stages 0–2: `src/openstore/core/compiler.py`,
  `src/openstore/core/holdcancel.py`, `src/openstore/core/ledger.py`.

## SCOPE (closed) — files you may create or modify
- `src/openstore/core/compiler.py` (transcript recording only — see S3.1)
- `src/openstore/core/webauthn_rp.py`
- `src/openstore/core/policy_signing.py` (NEW — signing-ceremony logic)
- `src/openstore/surfaces/studio.py` (Policy Studio surface only; Campaign Studio is Stage 8)
- `src/openstore/surfaces/templates/policy_studio.html` (NEW — single-file, lean per Part 4)
- `scripts/make_compiler_goldens.py`, `scripts/make_webauthn_fixtures.py` (NEW)
- `GOLDEN/compiler/vectors.json`, `GOLDEN/webauthn/*.json` (NEW — generated, never hand-written)
- `tests/stage03/**`, `tests/test_compiler_goldens.py`, `tests/test_webauthn_rp.py` (NEW)
- `OPEN_QUESTIONS.md` (append-only, per R0.4)

No other file may be modified.

## BUILD

### S3.1 Compiler transcript recording
PoAI §3.3.6 requires `adjudication.transcript`, and AAL predicate e7 requires a re-run of
`compile_decision` to produce a byte-identical transcript. Extend `compile_decision` to
record one entry per check executed, in normative order, stopping at first failure:
`{"check": <name>, "result": "pass"|"fail", "reason_code": <code|null>}`.
Names and reason codes come from REGISTRY.json only. The transcript MUST be byte-stable:
no timestamps, no randomness, canonical JSON serialization (sorted keys, no whitespace
variance). Check names: `human_authority_present`, then `currency_match` …
`campaign_validity` (the §3.2 table names, verbatim).

### S3.2 Compiler golden vectors — `GOLDEN/compiler/vectors.json`
Generate via `scripts/make_compiler_goldens.py`. One JSON array, ≥ 40 entries; each entry:
`{"name", "policy", "context", "cart", "expected": {"allowed", "reason_code", "transcript"}}`.
Required coverage (use these exact vector names):
- `allow_happy_path`
- One failing vector per check with its exact reason code: `assertion_required`,
  `policy.currency_mismatch`, `policy.merchant_mismatch`, `policy.policy_not_yet_valid`,
  `policy.policy_expired`, `policy.tx_count_exceeded`, `policy.qty_invalid`,
  `policy.sku_duplicate`, `policy.sku_blocked`, `policy.tag_violation_all`,
  `policy.tag_violation_any`, `policy.spend_per_tx_exceeded`,
  `policy.spend_envelope_exceeded`, `policy.spend_cumulative_exceeded`,
  `policy.campaign_inactive`, `policy.campaign_outside_window`
- Ordering proofs (stop-at-first-failure): a cart violating checks 4 AND 9 MUST return
  `policy.policy_expired`; a cart violating checks 1 AND 12 MUST return
  `policy.currency_mismatch`
- `two_policy_cumulative_isolation` (Q-003 proof): policy A has prior CAPTURE legs;
  policy B is clean; B's check 11 MUST see `spent_minor = 0`
- `aggregate_cap_exceeded` (§3.2a): signing-time aggregate breach →
  `policy.aggregate_cap_exceeded`
For campaign vectors, construct minimal Campaign rows directly in the fixture per the §9.3
schema (fields are a closed set) — no orchestrator logic in this stage.
`tests/test_compiler_goldens.py` replays every vector and asserts byte-identical expected
output, including the transcript.

### S3.3 WebAuthn RP — `src/openstore/core/webauthn_rp.py`
Use `py_webauthn` (pinned). Implement per PRD §3.5 (e2, e4), INV-2, INV-10, DECISIONS §11.1.4:
- **Enrolment** (`webauthn_register_begin/complete` logic): behind the operator session
  (INV-10); `user_id` comes from the session, never the request body. Store
  `{credential_id, public_key, aaguid, attestation_format, enrolled_at, sign_count, user_id}`.
- **Algorithms:** accept COSE ES256 (−7) and RS256 (−257) only; anything else → hard error
  `webauthn_unsupported_alg` (already in REGISTRY.json).
- **Assertion verification:** signature, UV flag (bit 0x04 in byte 32 — the flags byte —
  of decoded `authenticator_data`), challenge match against a single-use challenge store
  with TTL (config key `challenge_ttl_seconds = 120`), and sign-count monotonicity:
  if stored and received are both 0, accept (counter-less authenticators); if
  `received != 0 and received <= stored`, hard error.
- **`challenge_binding`:** two modes — `{"mode": "policy"}` at policy signing,
  `{"mode": "cart", "cart_hash": ...}` at checkout. The binding is stored with the
  challenge and verified on completion.
- No LLM imports (import firewall). Fail loud everywhere (R0.5); every rejection carries a
  closed-set reason code.

### S3.4 WebAuthn golden fixtures — `GOLDEN/webauthn/`
Generate deterministically via `scripts/make_webauthn_fixtures.py` using fixed keys
committed under `GOLDEN/webauthn/` (a fixed EC P-256 keypair and a fixed RSA keypair).
Fixtures MUST be produced by real `py_webauthn` calls — never hand-written JSON (R0.7
applies to library behavior: build against real outputs, not docs). Required fixtures:
valid registration, valid ES256 assertion, valid RS256 assertion, wrong-challenge
assertion (must fail), EdDSA (−8) registration (must fail `webauthn_unsupported_alg`),
sign-count regression (must fail). Tests replay all fixtures.

### S3.5 Policy Studio — `/intent/studio` + `src/openstore/surfaces/studio.py`
Lean single-file HTML per Part 4: no framework, no npm, no external CSS/JS.
- Renders the IntentPolicy form — all §3.1 fields, `policy_version` fixed at 2.
- Displays the HOLD_SECONDS table (AAL3 = 0s, AAL2 = 900s, AAL1 = 3600s) BEFORE the human
  signs (§3.5, DECISIONS §11.1.5) — this is normative, not cosmetic.
- Displays `per_user_aggregate_cap_minor` from config (demo default 500000).
- Signing ceremony: builds the IntentPolicy, runs a WebAuthn `{"mode": "policy"}`
  assertion via `/internal/webauthn/*`, submits to the signing endpoint.
- Signing-time validation (§3.2a): reject any policy whose `max_spend_total_minor` would
  push the enrolled user's aggregate above `per_user_aggregate_cap_minor` →
  `policy.aggregate_cap_exceeded`, surfaced in the page. A `policy_version != 2` →
  `LegacyPolicyError`, never mapped.
- `GET /internal/policy/blast-radius` (route exists in Part 6; implemented here): returns
  `{"policies": int, "credentials": int, "envelope_ids": list[str], "projected_freeze": bool}`
  for the signed-in operator. Session-carried `user_id` only (INV-10).

## MUST NOT
- Do not implement PoAI bundle assembly, AAL computation changes, or the verifier (Stage 4).
  `compute_aal_level` from Stage 2 stays as remediated; do not extend it.
- Do not implement checkout endpoints or MCP tools (Stage 6). The four `webauthn_*` MCP
  tool names remain unimplemented until then; this stage builds the underlying RP logic.
- Do not touch `agents/`, Campaign Studio, the campaign feed, or the orchestrator
  (Stages 7–8).
- Do not add, rename, or remove any identifier in REGISTRY.json.
- Do not invent `py_webauthn` response shapes from memory — fixtures come from real calls.
- No SPA framework, no npm, no external assets in `policy_studio.html`.

## DONE WHEN (every command MUST exit 0)
- `python scripts/registry_diff.py` → prints nothing
- `pytest tests/stage03/ tests/test_compiler_goldens.py tests/test_webauthn_rp.py -q` → 0 failures
- `pytest tests/sentinel/ -q` → pass (including import firewall)
- `grep -rEi "openai|anthropic|import litellm" src/openstore/core/` → no matches
- `GOLDEN/compiler/vectors.json` contains ≥ 40 vectors, all replayed byte-identical
- `ruff check src tests` and `mypy src` → clean
- Human-verified smoke (report to the operator, do not self-certify):
  `uv run openstore serve gelateria.yaml`, open `/intent/studio`, complete passkey
  enrolment + policy signing, confirm the HOLD_SECONDS table renders before signing.

## COMMIT GATE
`stage(03): compiler-webauthn`
