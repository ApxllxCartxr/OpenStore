# secure-money-authority - Work Plan

> **BLOCKED (access limit):** Execution cannot proceed in this environment. Subagent dispatch
> (`task()`) is gated by an unpaid billing account ("No payment method" — confirmed on the
> `metis` gap-review call and again on the first implementation delegation, todo 1). The Atlas
> orchestrator is forbidden from editing product code directly, so with no subagent capability the
> work is stalled. All 12 checkboxes are marked `- [~]` (blocked). To unblock: add a payment method
> so subagents can run, OR explicitly authorize direct implementation, OR run this plan in an
> environment where subagent spawning is enabled. The plan itself is decision-complete and approved.

## TL;DR (For humans)
What you'll get: the money-decision verifiers in BOTH OpenStore money paths stop trusting caller-supplied
booleans and start requiring a real, verifiable cryptographic proof. Concretely: `native_webauthn.verify`
and the AP2 verifier will (a) verify the WebAuthn ECDSA signature over the cart/policy binding, (b) verify
the UV flag and freshness, and (c) fail-closed when proof is missing; the ACP adapter will decide the
step-up BEFORE calling `confirm_checkout`; and the reference merchant's `IdempotencyRecord` gets a unique
constraint so a concurrent double-submit cannot charge Razorpay twice.

Why this approach: the correct stateless verifier ALREADY EXISTS at `openstore/verify/__init__.py:129-178`
(`_verify_webauthn_assertion` / `_verify_challenge_binding` / `_verify_uv`) and is proven by
`tests/test_verifier.py`. Only the LIVE money-decision path bypasses it in favor of booleans. This plan
reuses that exact logic (extracted to a shared module) rather than inventing new crypto. The reference
merchant ships a correct verifier (`reference/merchant/webauthn.py:40 verify_assertion_policy`) that is
simply never called in `checkout_confirm` — the fix wires it in. TDD keeps every change proven.

What it will NOT do: no new dependencies, no compiler-digest / AAL-reason / policy-schema changes, no
changes to the OAuth/OIDC surface (H1), the JWT secret (C3), the bridge tag-drop (H2), the spend-ledger
timing (H4), or the duplicated caps (H5) — all deferred by scope decision Q1=(a). No `openstore/verify`
public-API churn beyond the internal shared-helper extraction.

Effort: ~4-6 focused todos + a final verification wave. Roughly a half-day of worker time including tests.

Risk: MEDIUM. Security-critical code touching both money paths; 2 existing tests encode the bug and MUST
be reworked (they are listed as explicit dependent todos, not optional). Domain: `localhost`/port 8000;
the real-signature verifier is already covered by tests, so risk is in wiring, not in the crypto.

Decisions: scope = C1+P1+P2+C4 + C2 (Q1=a); test strategy = TDD (Q2=a); include `e3` freshness in C1 (Q3=a).

## Scope
IN:
- N1 `openstore/core/authority/native_webauthn.py` — real ECDSA + challenge-binding + UV + freshness verification,
  fail-closed on `_all_false()`.
- N2 `openstore/core/authority/ap2.py` (+ `openstore/protocols/ap2/adapter.py` where needed) — fail-closed mandate
  verification, no trust in caller booleans.
- N3 `openstore/protocols/acp/adapter.py` — AAL decision before `confirm_checkout`; also resolves the AAL-0 dead-end.
- N4 `reference/merchant/models.py` `IdempotencyRecord` — add `UniqueConstraint("client_id","idempotency_key")`;
  make the INSERT collide-safe in `reference/merchant/mcp_server.py` `_persist_confirm_result`.
- C2 `reference/merchant/mcp_server.py` `checkout_confirm` Path A — call `verify_assertion_policy(...)` before
  `verify_cart_against_policy`.
- N5 dependent test rework for `tests/test_interop.py` (the two tests that pass `raw={}`).
- Shared module `openstore/core/authority/webauthn_verify.py` extracted from `openstore/verify/__init__.py`;
  both call sites use it.
- Tests (TDD): new/updated tests in `tests/` for N1/N2/N3/N4/C2.

OUT (Must-NOT-Have):
- C3 (JWT secret), H1 (OAuth client validation), H2 (bridge tags), H4 (spend ledger timing), H5 (duplicate caps).
- Any change to `openstore/compiler.py` digest / AAL reason vocabulary / policy schema.
- New dependencies; schema migration beyond the one idempotency unique index.
- Renaming or removing the public `verify_bundle` API.

## Verification strategy
- TDD: each implementation todo starts by writing/updating a test that FAILS against current behavior, then the
  code change makes it pass. Agent executes both, with evidence (exact test name + paste of key assertion).
- Full suite `uv run pytest -q` must stay 204-passing (or 204± the count of new/removed tests) with zero new failures.
- Each todo records: References (file:line), Acceptance criteria, happy + failure QA scenarios with evidence path,
  and a Commit line.
- Final verification wave F1-F4 (parallel) must ALL APPROVE before completion.

## Execution strategy
Target 5-8 todos per wave. Implementation + Test = ONE todo. Order respects dependencies:
N1 and N2 share the new shared-verifier module (todo 1) and are independent afterwards; N3 is independent;
N4 and C2 are in the reference merchant; N5 (test rework) lands with N1/N2. Wave A = shared module + N1 + N2 +
their tests. Wave B = N3 + N4 + C2 + their tests. Wave C = full-suite + final verification.

## Todos

- [x] 1. Extract the three WebAuthn verifier helpers into `openstore/core/authority/webauthn_verify.py`
  - References: move `_verify_webauthn_assertion` (openstore/verify/__init__.py:129-146), `_verify_challenge_binding`
    (149-165), `_verify_uv` (168-178) verbatim into a new module `openstore/core/authority/webauthn_verify.py`.
    The module must import ONLY `openstore.canonical`, `openstore.evidence`, `cbor2`, `cryptography`, `json`,
    `hashlib`, `base64` — no `merchant`, `fastapi`, `sqlmodel`, `compiler`, `delegation` (keeps it out of any
    import cycle: `openstore/verify/__init__.py` does NOT currently import `native_webauthn`, and the shared
    module must not import either caller). Give it a single public function
    `verify_native_authority(authority: dict) -> dict[str,bool]` returning `{"e2_policy_signature_valid": ok,
    "e4_user_verified": uv_ok, "e5_cart_bound": cb_ok}` plus `{"e3_assertion_fresh": fresh}` when the authority
    carries `webauthn.signed_at` and the policy carries `assertion_max_age_seconds` (mirror the logic in
    `_compute_predicates`, openstore/verify/__init__.py:266-273). Do NOT import this module at `openstore/verify`
    top-level yet (todo 2 wires it) to keep the diff reviewable.
  - Acceptance: `openstore/core/authority/webauthn_verify.py` exists; all three checks are byte-identical in behavior
    to the originals (same failure codes: `webauthn_signature_invalid`, `challenge_binding_mismatch`, `uv_flag_mismatch`);
    module imports only the allowed set.
  - QA happy: `from openstore.core.authority import webauthn_verify; auth=...` from `tests/test_verifier.py`'s
    `_mint_authority` output → returns `e2/e4/e5` all True for a valid signed authority. Evidence: run that snippet in
    a throwaway test against a real `simulate_browser_assertion` + `WebAuthnRP.complete_assertion` authority.
  - QA failure: corrupt the signature byte in `wa["signature"]` → `e2_policy_signature_valid=False`; tamper
    `client_data_json` challenge → `challenge_binding_mismatch`. Evidence: failing assertion captured.
  - Commit: `refactor(openstore): extract webauthn_verify shared helpers from verify/__init__`

- [x] 2. Rework `native_webauthn.verify` (N1) to require real proof
  - References: `openstore/core/authority/native_webauthn.py:81-98`. Replace the boolean-trust body with:
    build `authority = {"webauthn": raw.get("webauthn"), "enrolment": raw.get("enrolment"), "policy": presentation.policy_json}`
    and call `webauthn_verify.verify_native_authority(authority)`; assign the returned `e2/e3/e4/e5` into `p`.
    Keep `e7_compiler_allow`/`e8_intent_recorded` as the caller-claimed flags ONLY IF they remain false when the
    proof set fails (they gate on the same failure via `resolve_aal`); if any of `webauthn`/`enrolment`/`policy` is
    absent, set `e2=e4=e5=False` (fail-closed). Preserve `e1_agent_authenticated`/`e6_catalog_attested`/`e9_notified`
    as caller-supplied claims (they are not the human-auth leg). Import the shared module.
  - Acceptance: with `raw={}` (no webauthn/enrolment), `verify(...)` returns `e2_e4_e5` all False (→ resolve_aal
    gives 0). With a real `simulate_browser_assertion`+`complete_assertion` output in `raw`, returns AAL3.
  - QA happy: reuse `tests/test_webauthn_ceremony.py:54-60` pattern; assert `verify(...)` predicates reflect a real
    signature and `resolve_aal` → 3.
  - QA failure: `raw={}` → `verify` predicates `e2/e4/e5` False; tampered signature → same. Evidence: assertion.
  - Commit: `fix(openstore): native_webauthn verifies real assertion, fail-closed instead of trusting booleans`

- [x] 3. Rework AP2 verifier to fail closed (N2)
  - References: `openstore/core/authority/ap2.py:14-44`, `openstore/protocols/ap2/adapter.py:52-78`. In `_common`,
    do NOT default `signature_present` to True — require an actual verifiable signature artifact; set
    `e2_policy_signature_valid` False unless the mandate carries a proof we can verify (out of scope to verify
    SD-JWT; so treat "no verifiable signature artifact" as False). In `verify_cart_mandate`, set `e4_user_verified`
    False unless a human-held-key signature is *present and verifiable* (`human_held_key_signature` must not be a
    caller boolean — derive from the actual signature structure, or leave False). Keep UNMAPPABLE_CONSTRAINTS
    fail-closed (correct). Ensure `verify_intent_mandate` (policy-level) yields at most AAL2 and never AAL3.
  - Acceptance: a mandate with `"signature": {}` or no signature → `verify_cart_mandate` yields `e2=False` and thus
    AAL 0 (never AAL3). A mandate with `human_held_key_signature` absent → `e4=False`.
  - QA happy: mandate carrying a structural signature that maps to a verifiable human key → allowed (AAL2 if intent,
    AAL3 if cart-bound AND verifiable). Evidence: unit test on `verify_cart_mandate`/`verify_intent_mandate`.
  - QA failure: `signature={}` → AAL 0; unknown constraint still raises `AP2UnmappableConstraint`. Evidence: test.
  - Commit: `fix(openstore): AP2 mandate verifier fail-closed, never trust caller signature booleans`

- [~] 4. Fix ACP step-up ordering + AAL-0 dead-end (N3)
  - References: `openstore/protocols/acp/adapter.py:83-105`, `openstore/core/authority/acp.py:14-29`,
    `openstore/core/api.py:140` (required_aal=1). Before calling `core.confirm_checkout`, resolve the AAL
    (via `verify(authority)` + `resolve_aal` + `cap_aal`) and compare against `required_aal`; if `aal_level <
    required_aal`, return `{"status":"STEP_UP_REQUIRED", ...}` WITHOUT calling `confirm_checkout`. Also fix the
    dead-end: the ACP scheme must yield the intended AAL1 (set `e2_policy_signature_valid=True` for the delegated-
    credential scheme so `resolve_aal` doesn't short-circuit to 0 — R6.2c caps it at 1 via `cap_aal`), and
    `required_aal` for a normal cart should be 1 (already is), so low-tx ACP completes at AAL1.
  - Acceptance: `complete_checkout` with a delegated token below `required_aal` returns `STEP_UP_REQUIRED` and
    `core._orders` has NO new order / `core._spend` unchanged (confirm money did NOT move). When eligible, it
    completes at AAL1.
  - QA happy: delegated token on a cart with `required_aal=1` → status COMPLETED, order created, AAL1.
  - QA failure: assert step-up path does not create an order nor consume spend budget. Evidence: assert
    `len(core._orders)` and `remaining_budget` unchanged after a step-up.
  - Commit: `fix(openstore): ACP decides step-up before confirm_checkout; AAL1 not 0`

- [~] 5. Add idempotency unique constraint (N4)
  - References: `reference/merchant/models.py:160-165` (IdempotencyRecord), `reference/merchant/mcp_server.py:320`
    (`_persist_confirm_result` INSERT), template at `openstore/models.py:297-311` (UniqueConstraint). Add
    `__table_args__ = (UniqueConstraint("client_id", "idempotency_key"),)` to the reference IdempotencyRecord
    (import `UniqueConstraint`). In `_persist_confirm_result`, handle the INSERT: on `IntegrityError`, roll back and
    return the previously persisted result (fetch by client_id+key) instead of creating a duplicate order — or, if
    the SELECT-then-INSERT remains, treat the constraint as the authoritative guard (the second writer's INSERT
    fails and it returns the existing response). This makes idempotency race-safe under concurrency.
  - Acceptance: two concurrent `checkout_confirm` with the same `idempotency_key` produce ONE Razorpay order and ONE
    `IdempotencyRecord`; the second call returns the first's stored `response_json`.
  - QA happy: sequential retry still returns the existing response (existing test `test_idempotent_retry_no_duplicate_charge`
    must still pass).
  - QA failure: add a concurrency test that fires two same-key confirms (e.g. two threads on the same TestClient/session)
    and asserts `create_order_and_payment_link` was effectively called once (or the DB holds exactly one record for that
    key) and total `SpendLedgerEntry` for that checkout_id is one. Evidence: the test + assertion.
  - Commit: `fix(merchant): unique idempotency constraint closes concurrent double-charge race`

- [~] 6. Wire reference `checkout_confirm` Path A to `verify_assertion_policy` (C2)
  - References: `reference/merchant/mcp_server.py:165-235` (Path A), `reference/merchant/webauthn.py:40-105`
    (`verify_assertion_policy(credential_id_b64, public_key_b64, assertion_b64, policy_json, nonce_to_policy_hash)`),
    `reference/merchant/intent_routes.py` (nonce→policy_hash issuance, AssertionRow), `reference/merchant/models.py`.
    Before `verify_cart_against_policy` (mcp_server.py:199), call `verify_assertion_policy(...)` using the stored
    `policy_row.public_key` + `policy_row.credential_id` + `policy_token` + `policy_json` + the nonce→policy-hash map
    maintained when the assertion was signed. On `ValueError`, reject the checkout (403) like the other guards and
    set `checkout.status="REJECTED"`. The nonce map must bind each nonce to the policy hash that was actually signed
    (reuse/extend what `intent_routes.py` stores so it is available at confirm time).
  - Acceptance: a `policy_token` with a bad/forged signature is rejected with a 403 and the checkout is REJECTED;
    a validly-signed `policy_token` passes. The existing "Policy mismatch" path (hash compare) remains.
  - QA happy: valid signed assertion → Path A proceeds to Razorpay (mirrored in test with a stubbed
    `create_order_and_payment_link`).
  - QA failure: tamper `policy_token` signature → 403 "Invalid assertion signature"; checkout REJECTED. Evidence: test.
  - Commit: `fix(merchant): checkout_confirm verifies the WebAuthn assertion signature before confirming`

- [~] 7. Rework the two `tests/test_interop.py` tests that encode the bug (N5)
  - References: `tests/test_interop.py:148-170` (`test_budget_is_shared_across_protocols`) and `:203-226`
    (`test_same_cart_same_verdict_across_protocols`). Replace `AuthorityPresentation(scheme="native_webauthn", raw={},
    policy_json=None)` and `raw={"token_present": True}` with a REAL authority produced by
    `WebAuthnRP.begin_assertion` + `simulate_browser_assertion` + `WebAuthnRP.complete_assertion` (the pattern in
    `tests/test_webauthn_ceremony.py:54-60`), keeping the budget-sharing and cross-protocol verdict assertions intact.
    For the ACP leg, keep the delegated-token semantics but ensure the authority now yields the corrected AAL (1) so
    the test reflects real behavior.
  - Acceptance: both tests run with real authorities and still assert shared budget / identical transcript; they
    FAIL against the pre-fix code (proving they were encoding the bug) and PASS after todos 2-4.
  - QA happy/failure: run the two tests after todos 2-4 → pass; revert todo 2 briefly → fail (red-green proof).
    Evidence: pytest output.
  - Commit: `test(interop): use real WeAuthn authorities instead of raw={}`

- [~] 8. Full-suite regression + a concurrency regression test for N4
  - References: run `uv run pytest -q` (baseline 204 pass). Add `tests/test_custom_idempotency.py` (or extend the
    existing idempotency test file) with the concurrent same-key test from todo 5. Confirm zero new failures and no
    test-order flakiness (run twice).
  - Acceptance: `uv run pytest -q` green; the new concurrency test is in the suite and passes; no regressions.
  - QA: run suite twice, both green; the N4 concurrency test alone re-run 3x green. Evidence: full CI output.
  - Commit: `chore(tests): idempotency concurrency regression + full-suite green`

## Final verification wave
- [~] F1. Plan-compliance audit — every todo's acceptance criteria meets its stated evidence path; no todo left
  without a failing-then-passing test; grep the diff to confirm NO `openstore/verify/__init__.py` public-API change
  and NO out-of-scope file edited (only the 8 listed paths + tests). Exit_ok only if all pass.
- [~] F2. Code-quality review — the shared `webauthn_verify.py` has no duplicate logic vs the originals; fail-closed
  semantics are explicit; no new dependency; the ACP ordering, AP2 fail-closed, and C2 wiring are each reviewed for
  correctness by reading the final source. Exit_ok only if all pass.
- [~] F3. Real manual QA — boot `uv run uvicorn merchant.app:app --reload --port 8000`, run the MCP `checkout_confirm`
  happy path with a REAL signed assertion (register → sign via `/intent/*` → confirm) and a FORGED-signature negative
  path; verify 403 + REJECTED; verify idempotent re-confirm returns the stored response. Evidence: console transcript.
- [~] F4. Scope fidelity — diff against the Must-NOT-Have list: nothing in C3/H1/H2/H4/H5 changed; no compiler-digest /
  AAL-reason / policy-schema edits; no new dependencies. Exit_ok only if clean.

## Commit strategy
One atomic commit per todo (8 commits), each prefixed per the Commit line in its todo, only touching the files in
that todo's References. The test-rework (todo 7) and the fix it enables (todos 2-4) should land in the same review
unit conceptually but remain separate commits so the red-green transition is visible in history. No commit until its
own tests pass.

## Success criteria
1. `native_webauthn.verify` and both AP2 verifiers return fail-closed predicates for unverifiable/missing proof and
   AAL3 only for a genuinely verified human-held-key signature over the cart.
2. ACP `complete_checkout` never creates an order when below `required_aal`, and completes at AAL1 when eligible.
3. Reference `checkout_confirm` rejects a forged assertion signature; a valid one proceeds.
4. Concurrent same-key `checkout_confirm` produces exactly one Razorpay order.
5. `uv run pytest -q` green (204 baseline preserved or net-new per added tests), including the reworked
   `test_interop.py` and the new concurrency test.
6. No changes to C3/H1/H2/H4/H5, no new dependencies, no compiler/digest/schema changes.
