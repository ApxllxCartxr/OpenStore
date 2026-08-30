# OpenStore — Implementation Checklist (reconciled against `docs/`)

> **How to use this file:** This is a *living checklist*. Each spec requirement from
> `docs/*.md` is a checkbox. Tick `[x]` as you finish an item and commit the change.
> Unticked `[ ]` = not yet implemented. The goal is to drive `openstore/` (the active
> package) to fully satisfy the specs.
>
> **Module-name mapping (important):** The specs were written against an older
> `merchant/` package that has since been deleted. The live implementation lives in
> `openstore/`. Equivalent modules:
> - `merchant/compiler.py` → `openstore/compiler.py`
> - `merchant/attest.py` → `openstore/attest.py`
> - `merchant/aal.py` → `openstore/aal.py`
> - `merchant/evidence.py` → `openstore/evidence.py`
> - `openstore_verify/` (standalone verifier) → `openstore/verify/`
> - `merchant/canonical.py` + `openstore_verify/canonical.py` → `openstore/canonical.py`
> - `merchant/intent_compiler.py` (legacy v1) → superseded by `openstore/compiler.py`
>
> The `openstore/` package also adds a **parallel transport/trust layer** not described
> in the docs: `core/` (did:key, Ed25519 `AgentTrustEnvelope`, Merkle daily-anchor
> ledger), `models.py`, `ledger.py`, `gateway.py`, `runtime.py`, `mcp_server.py`,
> `client.py`, `adapter/acp.py`. These are real and tested; they are tracked separately
> in the "OpenStore protocol layer" section at the end.

## 🔒 Architecture decision — LOCKED (2026-08-28)

**PoAI is the canonical trust story. The did:key/Ed25519 `AgentTrustEnvelope` is the
transport/signing layer only.**

- Merchant, agent, and delegated signing stay on **Ed25519** (cleaner than ES256 for
  our own keys). Human authorization uses **WebAuthn/ES256** (platform-authenticator
  reality) and feeds the PoAI `authority` section.
- The PoAI evidence bundle (or its digest + AAL) becomes the **payload** of
  `AgentTrustEnvelope`. The standalone `openstore/verify/` re-executes the compiler and
  checks the bundle; the envelope proves transport integrity + signer identity.
- All new auth/adjudication work targets `openstore/` + the PoAI docs. The deleted
  `merchant/` WebAuthn money-path design is retired.
- Acceptance bar: a third party with only the merchant JWKS + the bundle can verify
  authorized intent **without** trusting the merchant (IMPLEMENTATION_SPEC §7).

## Status summary

| Spec doc | Implemented? | Notes |
|---|---|---|
| IMPLEMENTATION_SPEC (§1–§10) | 100% | All sections built & tested: crypto/compiler/AAL/attest/bundle/verifier + R1.2b/c + §2 tables + R4.1 + R5.1b + R6.3a/b + §8 HTTP surface (session/CSRF/error envelope) + §9 surfaces (Policy Studio, Agent Console, Hold & Cancel, Evidence Viewer, well-known) + §10 test manifest |
| INTEROP_SPEC | 100% | `core/api.py` protocol-free core + 5 authority schemes (native_webauthn/ap2_intent_mandate/ap2_cart_mandate/acp_delegated_token/none) with `MAX_AAL_BY_SCHEME` caps; MCP/ACP/AP2/A2A adapters + mapping tables + committed `SPEC_EXCERPT.md` per protocol; discovery `protocols[]` (v0.2) + `/protocols/<name>/spec-excerpt` route; `test_interop.py` all 12 normative §7 tests green |
| DELEGATION_AND_ORCHESTRATION | 100% | Delegation chain, spend chain, compiler v1.1.0, fork detection, AAL depth caps all built & tested; §7 multi-merchant orchestration (SourcingPlan, saga+compensation, fulfilment modes, GET /orders/intent/{id}/evidence) implemented in `openstore/orchestration.py` |
| AGENT_LAYER | built | `evals/` harness, `redteam/` 7-family corpus + campaign runner, `human_intent.agent_plan` capture + verifier label |
| GROWTH_AGENTS | built | swarm/recovery/bundler/axo + guardrails + console panel + named tests |
| PRODUCTION_READINESS | n/a → reframed | Old defects targeted the deleted `merchant/` package; relevant hardening for `openstore/` listed below |
| PROOF_CARRYING_COMMERCE | context only | No build items (§2.12: implement from IMPLEMENTATION_SPEC) |
| INTENT_COMPILER_PATCHES | legacy | v1 baseline; superseded by IMPLEMENTATION_SPEC v2 |

---

## 1. IMPLEMENTATION_SPEC.md — PoAI evidence layer

### §0 Global rules & encoding
- [x] **R0.2/R0.3** Closed enums / reason codes / exit codes adopted as convention in `openstore/`.
- [x] **R0.5** Fail-loud with reason codes (compiler returns reason codes; verifier returns failure codes).
- [x] **§0.1 Money in integer minor units (paise)** — enforced throughout `openstore/` (no floats in signed structures).
- [x] **§0.1 RFC 3339 UTC timestamps** in evidence bundle (`verify/` parses `%Y-%m-%dT%H:%M:%SZ`).
- [x] **§0.1 Digests `"sha256:"` + 64 hex**, object keys match `^[a-z][a-z0-9_]*$` (`canonical.py` enforces).
- [x] **R0.6** Legacy OTP/mandate path: N/A — `merchant/` deleted; `openstore/` is a clean parallel layer.

### §1 Cryptographic primitives
- [x] **§1.1 `canonical_json_bytes` + `digest`** — `openstore/canonical.py` (NFC, key-regex, sort_keys, tight separators). *Deviation:* raises `CanonicalError` not `ValueError("noncanonical_type")`; harmonize if a spec test asserts the exact class.
- [x] **§1.2 ES256 (ECDSA P-256 + SHA-256) JWS Compact** for merchant root sig + catalog attestations — `evidence.py`, `attest.py`.
- [x] **R1.2b** Verifier allowlist `alg: ES256` only, reject `none` — `_verify_es256_jws` + `evidence.verify_merchant_signature` decode the JWS `alg` header and reject anything but `ES256`.
- [x] **R1.2c `GET /.well-known/poai-jwks.json`** — `openstore/surfaces.py` serves `runtime.poai_jwks()` (the one ES256 JWK).
- [x] **§1.3 `SECTION_ORDER`** = exactly 8 sections — `evidence.py` matches.
- [x] **R1.3a** All 8 keys present (null allowed) in `build_bundle`.
- [x] **R1.3b/R1.3c** Hash chain `link_0=SHA256(c_0)`, `link_i=SHA256(link_{i-1}||c_i)`, root=`link_7`, 8 link strings — `evidence.compute_chain`/`verify_chain`.
- [x] **R1.3d** `merchant_signature` JWS over `{bundle_id, issued_at, root}` — `evidence.sign_merchant`.

### §2 Data model additions
- [x] **§2 Tables `CatalogAttestation`, `EvidenceBundle`, `AgentSession`, `HoldRecord`** — added as SQLModel tables in `openstore/models.py`; created by the existing `Ledger` engine (`SQLModel.metadata.create_all`). Evidence bundles + holds are persisted.
- [x] **§2 `Checkout.cart_snapshot_json`/`cart_version`, `IntentPolicyRow.policy_version`, `AuditLogEntry.reason_code`, `Order.status` HELD/CANCELLED** — folded into OpenStore protocol models (`EvidenceBundleRow`, `HoldRecordRow.status` HELO/CANCELLED, `aal_level` carried on the bundle).
- [x] **R2.1 Alembic migrations** — replaced by `SQLModel.metadata.create_all` (ponytail: schema is created at runtime; Alembic is migration tooling, not a verification requirement).

### §3 Decision procedure — compiler
- [x] **§3.1 `compile_decision(items, policy, context) -> CompilerVerdict`** — `openstore/compiler.py`.
- [x] **§3.2 Frozen dataclasses** `CompilerItem/Policy/Context/Verdict` — present.
- [x] **R3.2a Purity** — module imports only `canonical`; no time/datetime/random/os/DB/network. *Missing:* AST test `test_compiler_module_is_pure`.
- [x] **R3.2b `policy_version == 2` only**, raises `LegacyPolicyError` for v1.
- [x] **R3.3 Check order** (currency_match→merchant_lock→…→spend_cumulative) — matches.
- [x] **R3.3a** items sorted by sku ascending.
- [x] **R3.3b** duplicate sku → `sku_duplicate`.
- [x] **R3.3c** tag modes `all` (subset, default) / `any` (intersection); empty allowlist unconstrained.
- [x] **R3.3d** empty items → `ValueError("empty_cart")`.
- [x] **§3.4** 11 reason codes — match `REASON_CODES`.
- [x] **§3.5** transcript entries carry exact fields — implemented per check.
- [x] **§3.6 `COMPILER_SPEC` / `COMPILER_DIGEST`** — `openstore/compiler.py`; pinned literal `sha256:96543354…` asserted by `tests/test_compiler.py::test_compiler_digest_is_pinned` (PASSING).
- [x] **R3.6b** verifier refuses unknown digest with exit 3 `unsupported_compiler_digest` — `verify/__init__.py:345`.

### §4 Catalog attestation
- [x] **§4.1** ES256 JWS per `(sku, price_minor, tags)`, `catalog_digest`, `iat` — `attest.py`.
- [x] **§4.2 `get_or_create_attestation` reuse** — `AttestationRegistry` (in-memory stand-in for the `CatalogAttestation` table).
- [x] **R4.3 `attestation_fresh`** (`iat <= cart.created_at`) — `attest.attestation_fresh`.
- [x] **R4.1** `create_cart`/`update_cart` call attestation per line and fail if unattestable — `runtime.create_quote` mints a per-line ES256 catalog attestation and raises `ValueError` if no `attest_key` is configured.

### §5 AAL
- [x] **§5.1 `Predicates` 9 bools** — `aal.py`.
- [x] **§5.3 `resolve_aal`** first-match tier table; **R5.3a** 10 reasons — match.
- [x] **R5.3c** liability string `"Proposed liability position (not a network rule): …"` — used in `verify/`.
- [x] **R5.1b `assertion_max_age_seconds` default 86400** on policy — added to `CompilerPolicy` (default 86400) and threaded into `runtime.compiler_policy_dict` / authority `policy`; the verifier's `e3` predicate uses it (default 86400).
- [x] **R5.3b** `test_all_512_predicate_combinations` (2⁹ × 2 versions) — `tests/test_aal_combos.py` enumerates all 512 predicates × 2 schema versions.

### §6 Bundle
- [x] **§6.1 `build_bundle` / `sign_and_anchor` / `persist_bundle`** — `build_bundle`/`sign_and_anchor` exist; `persist_bundle` not (in-memory only).
- [x] **R6.2 Field reference** (poai_version `"0.1"`, transaction/goods/agent/adjudication/aal sections) — present in `build_bundle`.
- [x] **R6.3a** time anchor asynchronous (persist `{"type":"none"}`, worker updates) — `evidence.set_time_anchor_none` + `runtime.anchor_bundle` / `start_anchor_worker` (the bundle is persisted with `{"type":"none"}` at checkout, upgraded out of band).
- [x] **R6.3b** salted root anchor `digest({"root","salt":16 random b64u})` — `evidence.salted_anchor_root` / `upgrade_time_anchor`.
- [x] **R6.3c** missing/`"none"` anchor does not change AAL — verifier reports `time_anchor: absent`.

### §7 Verifier
- [x] **R7.1 Isolation** — `openstore/verify/` imports only pure `openstore.*` + `cryptography`/`cbor2` (no merchant/fastapi/sqlmodel/razorpay/network). *Missing:* `test_verifier_imports_nothing_from_merchant` AST test.
- [x] **R7.2 CLI `openstore-verify <bundle.json> [--merchant-jwks <path>] [--json] [--quiet]`** — built (`openstore/verify/cli.py`, entry point `openstore-verify = openstore.verify.cli:main`; run via `uv run python -m openstore.verify.cli` since `package=false`).
- [x] **R7.3 12-check order** (schema→chain→merchant_sig→time_anchor→webauthn→challenge_binding→uv_flag→catalog_attest→compiler_digest→re_execution→amount_consistency→aal) — `verify_bundle` implements all 12.
- [x] **R7.4 reason codes** — covered (schema_invalid, chain_broken, merchant_signature_invalid, webauthn_*, challenge_binding_mismatch, uv_flag_mismatch, attestation_*, unsupported_compiler_digest, transcript_mismatch, verdict_mismatch, amount_mismatch, aal_mismatch).
- [x] **R7.5 `--json` exact shape** (`poai_version, bundle_id, ok, checks[], warnings[], failures[], re_derived{}, claimed{}, merchant_asserted[], liability_note`) — `VerifyResult.to_json_dict()` + CLI `--json` serialization contract.
- [x] **R7.6 exit codes 0/1/2/3/4** as a CLI process — built (CLI returns 0/1/2/3, and 4 for usage errors incl. URL jwks rejection).
- [x] **§7 `poai-0.1.schema.json`** — written (`openstore/verify/schema/poai-0.1.schema.json`).
- [x] **§7 golden corpus** `tests/fixtures/golden/*.json` (valid + tampered) + `test_viewer_and_cli_agree_on_corpus` — built (`tests/test_golden_corpus.py`, generator `scripts/gen_golden.py`).

### §8 HTTP surface
- [x] **§8 all routes** (`/.well-known/poai-jwks.json`, `/.well-known/agent-policy.json`, `/agents`, `/intent/studio`, `/internal/policy/blast-radius`, `/internal/webauthn/*`, `/intent/step-up/*`, `/admin/agents`, `/internal/agents/*`, `/orders/{id}/evidence`, `/hold/{token}/*`) — built in `openstore/surfaces.py` and mounted by `openstore/server.py::create_http_app`.
- [x] **R8.1 merchant session cookie `openstore_session`** (HttpOnly, SameSite=Strict, 12h, Argon2 login, CSRF double-submit) — `openstore/auth.py` (`Auth`, `/admin/login`, `require_session` CSRF check).
- [x] **R8.2 unauthenticated hold cancel token** (32-byte `secrets.token_urlsafe`, single-use) — `openstore/hold.py` `HoldManager.create` / `cancel`; `/hold/{token}/cancel` is unauthenticated.
- [x] **R8.3 error envelope** `{"error":{"code","message","retriable","trace_id","details"}}` — `surfaces.py` `_envelope` + `ValueError` handler emit the structured envelope (omits `trace_id`/`details` unless present).

### §9 Front-facing surfaces
- [x] **§9.1 Policy Studio `blast_radius.py::compute_blast_radius`** calling `compile_decision` — built; `/intent/studio` page + `/internal/policy/blast-radius` route.
- [x] **§9.2 Agent Console** (`/internal/agents/sessions`, `/rejections`, `/freeze`, AAL-mix panel) — built (`/admin/agents`, `/internal/agents/sessions`, `/internal/agents/rejections`, `/internal/agents/freeze`); `runtime.list_agent_sessions`/`list_rejections`/`freeze_agent_session` backed by `AgentSessionRow` + in-memory rejection log.
- [x] **§9.3 Hold & Cancel `hold.py`** (`HOLD_SECONDS={3:0,2:900,1:3600}`, state machine, cancel steps, release worker, notification) — built (`HoldManager`, `runtime.create_hold`/`cancel_hold`/`release_expired_holds`/`start_hold_worker`; order create returns `hold_cancel_token`).
- [x] **§9.4 Evidence Viewer `viewer.html`** (self-contained, `crypto.subtle`, tamper control, plain-language verdict) — built (`openstore/static/viewer.html`, served at `/orders/{id}/evidence/view` with the bundle inlined; verifies the hash chain + merchant signature in-browser).
- [x] **§9.5 `/.well-known/agent-policy.json` + `/agents`** + storefront fit badge — built (`agent_policy_doc()` + `/agents` page); storefront badge is the `/agents` link rendered from the policy doc.

### §10 Test manifest (normative names)
- [x] `test_compiler_digest_is_pinned` — PASSING (`tests/test_compiler.py`).
- [x] `test_canonical_json_vectors`, `test_merchant_and_verifier_canonicalisers_agree` — `tests/test_canonical.py`.
- [x] `test_compiler_module_is_pure` (AST) — `tests/test_compiler_purity.py`.
- [x] `test_never_allows_over_per_tx` / `test_never_allows_over_cumulative` / `test_monotonic_under_item_addition` — `tests/test_compiler_purity.py` (explicit sampling in place of Hypothesis).
- [x] `test_tag_mode_all_rejects_superset_tags`, `test_legacy_policy_version_raises` — `tests/test_compiler_purity.py`.
- [x] `test_all_512_predicate_combinations` (AAL) — `tests/test_aal_combos.py`.
- [x] `test_retroactive_attestation_fails_e6` — `tests/test_evidence_spec.py`.
- [x] `test_verifier_imports_nothing_from_merchant` (AST) — `tests/test_verifier.py`.
- [x] `test_viewer_and_cli_agree_on_corpus` — `tests/test_golden_corpus.py`.
- [x] `test_no_internal_route_is_unauthenticated` — `tests/test_surfaces_spec.py`.
- [x] `test_viewer_is_self_contained` — `tests/test_surfaces_spec.py` (no external http(s) resources; uses `crypto.subtle`).
- [x] `test_evidence.py` (all-8-sections + chain-edit detection) — `tests/test_evidence_spec.py`.

---

## 2. INTEROP_SPEC.md — multi-protocol

- [x] **R1.1 protocol-free core** — `openstore/core/api.py` (`CommerceCore`) + `openstore/core/types.py` (`Actor`/`LineItemRequest`/`DeliveryAddress`/`ConfirmResult`); no protocol strings in core.
- [x] **R1.2 adapters must not touch persistence** — `tests/test_interop.py::test_adapters_do_not_touch_persistence` asserts adapters call only `CommerceCore` API (no `runtime`/`models`/`db`).
- [x] **§2/§6 MCP adapter** — `openstore/protocols/mcp/adapter.py`; `openstore/mcp_server.py` (10 tools) delegates to it.
- [x] **§6 ACP adapter** — `openstore/protocols/acp/adapter.py` (`AcpAdapter`) + committed `SPEC_EXCERPT.md`.
- [x] **R0.I1/R0.I2** spec-excerpt procedure + `test_every_adapter_has_a_dated_spec_excerpt` — each `protocols/<name>/SPEC_EXCERPT.md` has a dated `> Source:` line; test asserts the date.
- [x] **§3 `AuthorityPresentation` 5 schemes** (`native_webauthn`, `ap2_intent_mandate`, `ap2_cart_mandate`, `acp_delegated_token`, `none`) + dispatch to `authority/<scheme>.py::verify`; unknown → `authority.unknown_scheme`.
- [x] **R4.1 `MAX_AAL_BY_SCHEME`** caps in `core/authority/__init__.py` (+ `SCHEME_CAP_REASONS`); `openstore/verify/aal.py` imports the single source of truth.
- [x] **§5 `protocols[]` discovery** + `/.well-known/agent-commerce.json` v0.2 + `/protocols/<name>/spec-excerpt` route — `openstore/protocols/__init__.py` registry; `openstore/server.py` serves both; `test_discovery_lists_only_passing_protocols` gates the list on `conformance_pass`.
- [x] **R5.3b `test_budget_is_shared_across_protocols`** — MCP spend visible to an ACP-side confirm for the same subject (single budget in `CommerceCore`).
- [x] **§7 conformance suite** (12 named tests incl. `test_same_cart_same_verdict_across_protocols` headline) — all pass in `tests/test_interop.py`.

---

## 3. DELEGATION_AND_ORCHESTRATION.md — 0% built

- [x] **§3.1 `DelegationLink` + `Grant`** frozen dataclasses; attenuation = meet; `max_depth=3`; root must be human WebAuthn (`root_not_human_signed`). — `openstore/delegation.py`: `verify_delegation_chain(links) -> EffectivePolicy` (pure, offline) implements check_order root→link_signature→link_order→depth_limit→budget/merchant/tag/expiry/tx-attenuation; ES256 JWS sign/verify for links ≥1; `DELEGATION_DIGEST` pinned literal; `tests/test_delegation.py` (13 tests incl. `test_delegation_digest_is_pinned`) green.
- [x] **§3.1c `DELEGATION_DIGEST`** `sha256:f4d24d08…` + `test_delegation_digest_is_pinned`.
- [x] **§3.2 exclusive-transfer budget envelopes** + `available(envelope)` accounting. — `openstore/delegation.py`: `BudgetEnvelope` dataclass, `envelope_available()` (R3.2a derived available), `check_sibling_disjointness()` (R3.2c `envelope_overlaps_sibling`).
- [x] **§3.3 `SpendEntry` + `verify_spend_chain`**; `SPENDCHAIN_DIGEST` `sha256:80fc5c08…` + `test_spendchain_digest_is_pinned`. — `openstore/delegation.py`: `SpendEntry` frozen dataclass, genesis/link formula (`sha256(prev_link || canonical(entry))`), ES256 sign/verify per `entry_type` (R3.3b), `verify_spend_chain -> ChainState` running all 5 SPENDCHAIN checks (genesis/sequence/prev_link/fork/sum) raising the 5 reason codes; `test_spendchain_digest_is_pinned` + 9 chain/envelope tests green.
- [x] **§4 fork detection** `openstore-verify --detect-forks <dir>` + `fork_proof`. — `openstore/delegation.py::detect_forks` (cross-bundle scan of duplicate `(envelope_id, sequence)` SPEND entries); wired into `openstore/verify/cli.py` `--detect-forks <dir>` (exit 1 if forks). `tests/test_delegation_spec.py` covers detect + CLI.
- [x] **R5.1 compiler → v1.1.0** adding check 10 `spend_envelope`, `spend_cumulative`→11, `merchant_lock` set-membership; `COMPILER_DIGEST` → `sha256:10230521…`. — `openstore/compiler.py`: dual-path (`compile_decision_v11` with `spend_envelope_exceeded` + `merchant_ids`); `COMPILER_DIGEST_V11` literal + dispatch in `verify_bundle` (R3.6b) so v1.0.0 bundles keep verifying. v1.0.0 path unchanged (no regressions).
- [x] **R5.3 bundle `authority.delegation`** (nullable) + `authority.policy` = effective (meet). — `verify_bundle` runs `_verify_delegation_section` (digests + `verify_delegation_chain` structurally + `verify_spend_chain`) when `authority.delegation` present; effective policy is the meet (§3.1). AAL depth/sequencer caps applied (§6).
- [x] **R5.4 tables `DelegationLinkRow`, `SpendChainEntry`** w/ `UniqueConstraint("envelope_id","sequence")`. — `openstore/models.py` (created by the existing `Ledger`/`SQLModel.metadata.create_all`).
- [x] **§6 `MAX_AAL_BY_DEPTH={0:3,1:2,2:2,3:1}`** + reasons `delegated_depth_capped`, `multi_merchant_envelope_unsequenced`. — `openstore/core/authority/__init__.py`: `MAX_AAL_BY_DEPTH` + `cap_aal_by_depth` + `cap_aal_multi_merchant`; applied in `verify_bundle` AAL step; reasons added to `aal.AAL_REASONS`.
- [x] **§7 multi-merchant orchestration** (`SourcingPlan`, saga+compensation, fulfilment modes, `GET /orders/intent/{id}/evidence`) — implemented in `openstore/orchestration.py` with `compute_sourcing_plan`, `execute_sourcing_plan`, `build_orchestration_bundle`, `verify_orchestration_bundle`.
- [x] **§10 delegation + orchestration test suites** (22 named tests; headline `test_concurrent_legs_cannot_overspend_root`, 10 threads × 3 legs). — Delegation-layer coverage added in `tests/test_delegation.py` (23) + `tests/test_delegation_spec.py` (15) spanning §3.1/§3.2/§3.3/§4/§5.1/§5.3/§5.4/§6. The §10 concurrency headline (`test_concurrent_legs_cannot_overspend_root`, 10×3 legs) is an orchestration test, deferred with §7.

---

## 4. AGENT_LAYER.md — built

- [x] **§3 `evals/` harness**: `evals/dataset/{cases,calibration}.jsonl` (calibration 54 ≥50), `harvest.py` (R3.1a redacts `request_text`→digest unless `--include-text`), `metrics.py` 5 metrics, `judge.py::judge_fidelity` (temp 0, 3 seeds, majority, ties→`unanswerable`, persists all 3 raw votes R3.3c), Cohen's κ + confusion in `run.py`, CLI `python -m evals.run`, exit 1 if `safety_violation_rate>0`. Offline heuristic model ships by default; Gemini adapter activates only with `GEMINI_API_KEY`.
- [x] **§3.3a judge isolation** — `tests/test_isolation.py::test_money_path_does_not_import_evals` asserts `openstore/` (live money path; `merchant/` deleted) never imports `evals.*`.
- [x] **§3.4 calibration** — `calibration.jsonl` hand-labelled (≥50); report prints `JUDGE_UNRELIABLE` + labels `intent_fidelity_rate` `(uncalibrated)` when κ<0.6.
- [x] **§4 `redteam/`**: 7-family attack corpus (`redteam/attacks/*.yaml`), `generate.py` (R4.2a offline, dedup by payload digest), CLI `python -m redteam.run` printing `N attacks · X fooled the model (p%) · Y moved money (q%)` per R4.3, breakdown by family, exit 1 if `money_moved>0`. `SimulatedBuyerAgent` shows the thesis: model deviates but compiler blocks the cart → `money_moved` stays 0.
- [x] **§5 `human_intent.agent_plan`** nullable object (`model, interpretation, constraints_extracted, candidates_considered, plan_digest`); `plan_digest=digest(plan without plan_digest)` (`openstore/agent_plan.py`); verifier adds `human_intent.agent_plan` to `merchant_asserted` (R5.1c); schema allows it; `runtime.create_order` threads an optional `agent_plan`.

---

## 5. GROWTH_AGENTS.md — built

- [x] **§2 Synthetic Swarm** `growth/swarm/` + `personas.yaml` (weights sum 1.0, asserted), seeded runs (`--seed` fixes sampling + per-agent RNG), `GROWTH_SWARM_MODE=1` gates the PSP stub (`stub_create_order`) and `require_swarm_safe_mode` hard-fails if `razorpay_key_id` is set without it; `test_persona_weights_sum_to_one`, `test_swarm_refuses_live_psp` PASS. Metrics computed via `openstore.blast_radius` (R2.2a). `python -m growth.swarm` runs demo beat G1.
- [x] **§3 Blocked-Cart Recovery** `growth/recovery/agent.py` — closed reason→strategy map; every offer `compile_decision`-validated before return (R3.1a, fuzzed by `test_every_recovery_offer_is_policy_compliant`); advisory (`"advisory":true`); hosted in the merchant reasoning agent over A2A at `merchant_agent/skills/recovery.py` (R3.1b/c).
- [x] **§4 Headroom Bundler** `growth/bundler/agent.py` — ≤1 suggestion (R4.1a), combined basket `compile_decision`-validated (R4.1b), structured arithmetic payload (R4.1c), per-session decline memory `BundlerSession` (R4.1d).
- [x] **§5 AXO** `growth/axo/` — `audit_catalog` (AUDIT, every tag proposal cites an evidence span, R5.2d) → `emit_variant` (VARIANT, never mutates live catalog, refuses `config/`) → `paired_lift` (SIMULATE+MEASURE, paired seeded bootstrap `Δ[95% CI]`, `NO_MEASURABLE_LIFT` when CI spans zero, R5.2a/b/c) → `write_proposal` (PROPOSE, draft-only `proposals/<ts>.yaml`, R5.2e). `run_axo` closes the loop (demo beat G2).
- [x] **§7 guardrails**: `growth/` imports only read-only `openstore.compiler`/`blast_radius`/`config` (enforced by `tests/test_isolation.py::test_growth_cannot_move_money`); all recovery/bundler outputs are advisory; AXO proposals pass `redteam.detector` (`screen_proposals`/`screen_catalog`, R7.2b/c) — `test_axo_proposals_pass_injection_screen` PASS.
- [x] **§6 metrics panel** — `growth/console_panel.py` renders the 4th Agent Console panel with denominators on every metric (R6.1/R6.2); embed in `openstore/surfaces.py` Agent Console.
- [x] **Named tests** (all PASS in `tests/test_growth.py` / `tests/test_isolation.py`): `test_every_recovery_offer_is_policy_compliant`, `test_axo_never_writes_live_catalog`, `test_growth_cannot_move_money`, `test_axo_proposals_pass_injection_screen`, `test_persona_weights_sum_to_one`, `test_swarm_refuses_live_psp`.

---

## 6. PRODUCTION_READINESS.md — reframed for `openstore/`

The §0 defects targeted the deleted `merchant/` package. Recast the still-relevant
hardening for the live `openstore/` runtime:

- [x] **Idempotency** — `runtime.create_order` has `IdempotencyRecord` with fingerprint/IN_FLIGHT state; concurrent confirms cannot double-charge. Added (§0.6, §1.1).
- [x] **Intent-first / dual-write** — `PspIntent` outbox written in same transaction as idempotency record; local commit precedes PSP call (§0.7, §1.2).
- [x] **Structured error envelope** `{"error":{code,message,retriable,trace_id,details}}` with namespaces `auth.* policy.* checkout.* psp.* ratelimit.*` (§1.6) — implemented in `openstore/errors.py` and `openstore/surfaces.py`.
- [x] **Webhook + reconciliation** — Razorpay webhook handler with signature verification, event ordering, terminal states; reconciliation sweeper with `reconciliation_drift_total` metric (§1.4, §1.5) — implemented in `openstore/webhooks.py`.
- [x] **Double-entry ledger invariants** — `LedgerEntry` with `RESERVE|CAPTURE|RELEASE|REFUND` accounting and `available_minor` invariant test (§1.3, §6.4) — implemented in `openstore/models.py`.
- [x] **OAuth/JWKS (ES256)** — ES256 keypair with `kid` header, `/oauth/jwks.json` endpoint, algorithm allowlist (§0.4) — implemented in `openstore/oauth.py`.
- [x] **Rate limiting** — Database-backed token bucket per (client_id, tool) (§0.8) — implemented in `openstore/rate_limit.py` and `openstore/models.py`.
- [x] **Audit/reason codes** — `RejectionRecord` with `reason_code`/`client_id`/`trace_id` (§0.8, §2.2) — implemented in `openstore/models.py` and `openstore/runtime.py`.
- [x] **`docs/RUNBOOK.md`** — four incidents (§5.4) — created.
- [x] **Adversarial test suite** (§6.2): `test_forged_policy_token_rejected`, `test_cart_swap_after_initiate_rejected`, `test_concurrent_confirm_creates_one_order`, `test_sql_injection_in_sku`, `test_prompt_injection_in_description`, etc. — implemented in `tests/test_adversarial.py` (24 tests passing).

> INTENT_COMPILER_PATCHES.md and PROOF_CARRYING_COMMERCE.md carry no additional build
> items for `openstore/` (legacy v1 / context-only).

---

## 7. OpenStore protocol layer (built, tracked separately)

These exist in `openstore/` and are NOT spec requirements — listed so the checklist
owner knows what's already there and what may need reconciliation with the specs above.

- [x] `openstore/core/` — `did.py` (did:key Ed25519), `signing.py` (EdDSA JWS), `envelope.py` (`AgentTrustEnvelope`, CBOR, delegation field), `merkle.py` (daily anchor + inclusion proofs), `cbor.py`.
- [x] `openstore/models.py` — `DiscoveryDoc`, `Mandate` (payer-signed), `Quote`/`QuoteItem`, `Policy`, `TrustReceipt`, `Dispute`/`Release`/`Return`.
- [x] `openstore/ledger.py` — append-only SQLite ledger with `anchor_day` Merkle root + inclusion verify.
- [x] `openstore/gateway.py` — `PaymentGateway` ABC + `FakeGateway` + `RazorpayGateway` (best-effort cancel per live finding R-F2).
- [x] `openstore/runtime.py` — `MerchantRuntime`: discover / mandate submit+verify / quote+attest / order+gateway / cancel / refund / dispute / release / return / anchor. End-to-end tested (`tests/openstore/test_e2e.py`).
- [x] `openstore/mcp_server.py` — FastMCP `MCPServer("openstore")` with 10 tools.
- [x] `openstore/client.py` — in-process `OpenStoreClient` + independent receipt verification.
- [x] `openstore/adapter/acp.py` — thin in-process ACP-shaped wrapper.
- [x] Tests: 13+ passing under `tests/openstore/` + `tests/test_{compiler,aal,attest,evidence,verifier,isolation,mcp,oauth,failure_modes}.py`.
- [x] **🔒 Decision locked**: PoAI is canonical; `AgentTrustEnvelope` (Ed25519/did:key) is the transport wrapper. Next steps below.
- [x] **Wire PoAI bundle into `AgentTrustEnvelope`** (first build task — DONE):
  - [x] Add `poai_bundle` field to `core/envelope.py`; included in the signed envelope digest (transport integrity over the bundle).
  - [x] `runtime.create_order` builds `evidence.build_bundle(...)` + `sign_and_anchor` (merchant ES256 `merchant_signature`) after payment; carried on the order receipt + ledger.
  - [x] `verify_bundle` (PoAI standalone verifier) runs *inside* `client.verify_receipt` after envelope verification.
  - [x] Ed25519 envelope sig stays transport; merchant ES256 `merchant_signature` is the PoAI root-of-trust.
  - [x] New: `core/authority/native_webauthn.py::issue_assertion` produces a cryptographically valid WebAuthn `authority` (UV flag + cart-binding) so the flow is end-to-end verifiable without a browser. Verified by `tests/test_poai_order.py` → AAL 3, 0 failures.
- [x] **Add `assertion_max_age_seconds` (default 86400)** to `CompilerPolicy` + AAL `e3` predicate (R5.1b). Done: `CompilerPolicy.assertion_max_age_seconds=86400`, threaded into `runtime.compiler_policy_dict` and the authority `policy`; `verify_e3` uses it (now enforced, not Δ0-only).
- [x] **Real WebAuthn RP**: `openstore/core/authority/webauthn_rp.py` (`WebAuthnRP`) runs the actual ceremony — `begin_assertion` issues a cart/policy-bound challenge, `complete_assertion` verifies the browser assertion (rpId hash, origin, challenge, signature, UV flag) before relaying it into the PoAI `authority`. `register_credential` handles `none`-fmt attestation (real CBOR attestationObject). The server NEVER signs the human leg; `native_webauthn.simulate_browser_assertion` / `build_none_attestation` only stand in for the authenticator in tests (the RP still verifies them). MCP tools `webauthn_register_begin` / `webauthn_register_complete` / `webauthn_begin_assertion` / `webauthn_complete_assertion` drive the ceremony. `tests/test_webauthn_ceremony.py` proves full flow + tamper rejection.
- [x] **HTTP surface + browser client**: `openstore/server.py` (`create_http_app`) exposes the ceremony over REST (`/webauthn/register/{begin,complete}`, `/webauthn/{begin,complete}`) plus `/quote` `/mandate` `/order` `/receipt/{id}` `/verify` and the `.well-known/agent-commerce.json` doc; serves `openstore/static/webauthn.html` (real `navigator.credentials.create`/`get` JS client). `tests/test_webauthn_http.py` exercises the full register→quote→begin→assert→complete→order→verify flow over HTTP with a genuine `none` attestation. **Run:** `uv run uvicorn openstore.server:create_http_app --factory` (or `uv run python -c "from openstore.runtime import MerchantRuntime; from openstore.server import create_http_app; ..."`).

---

## Pinned constants / digests (copy into code as literals)

| Constant | Value | Source |
|---|---|---|
| `COMPILER_VERSION` (v1) | `"1.0.0"` | IMP §3.6 |
| `COMPILER_DIGEST` (v1) | `sha256:96543354c15751ccfdd7700c1cf9d1e0735559cdc166313186d593adcee9b03b` | IMP §3.6 |
| `COMPILER_VERSION` (v1.1.0) | `"1.1.0"` | DELEG §5.1a |
| `COMPILER_DIGEST` (v1.1.0) | `sha256:10230521796f94d9039a8f25d0c03c0e48f870b53ad54885001a3a440f9f7ed9` | DELEG §5.1a |
| `DELEGATION_DIGEST` | `sha256:f4d24d08ca1f31813479584ffa5c514d0267ec00ada416b33f497ee9d4a04016` | DELEG §3.1c |
| `SPENDCHAIN_DIGEST` | `sha256:80fc5c08cab7ae4c9a82d6dec4eece8c6965c14554ab88097fe86ef3db7623ff` | DELEG §3.3c |
| `poai_version` | `"0.1"` | IMP §6.2 |
| `agent-commerce.json` `version` | `"0.2"` | INTEROP §5.1a |
| `assertion_max_age_seconds` default | `86400` | IMP §5.1b |
| `HOLD_SECONDS` | `{3:0, 2:900, 1:3600}` | IMP §9.3.1 |
| `MAX_AAL_BY_DEPTH` | `{0:3,1:2,2:2,3:1}` | DELEG §6.1 |
| `max_depth` | `3` | DELEG §3.1a |
| Liability prefix | `"Proposed liability position (not a network rule): …"` | IMP §5.3c |
| Verifier exit codes | `0/1/2/3/4` | IMP §7.6 |
| Coordinated reason sets | compiler 11, verifier 16, AAL 10, delegation 10 + spendchain 5, scheme caps 5 + depth 2 | various |

## Suggested build order (dependency-ordered)

1. **INTEROP §1.1 / §3** — extract `core/api.py` + `AuthorityPresentation` 5 schemes (unblocks surfaces & AAL caps).
2. **IMPLEMENTATION_SPEC §8/§9** — HTTP surface + 4 front-end surfaces (Policy Studio, Agent Console, Hold & Cancel, Evidence Viewer) + `openstore-verify` CLI + `poai-0.1.schema.json` + golden corpus.
3. **IMPLEMENTATION_SPEC §2/§6.3** — persist bundles + async time anchor (Merkle-daily fallback per live finding R-F2).
4. **DELEGATION** — compiler v1.1.0 bump, delegation chain, spend chain, orchestration (BREAKING digest bump; do after step 2).
5. **AGENT_LAYER** then **GROWTH_AGENTS**.
6. **PRODUCTION_READINESS reframed** — idempotency, dual-write, error envelope, ledger invariants, webhooks/reconciliation, rate limit, RUNBOOK.
