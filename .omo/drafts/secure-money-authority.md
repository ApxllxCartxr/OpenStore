# Draft: secure-money-authority

- slug: secure-money-authority
- intent: clear
- review_required: false
- classification: Standard (3-6 files, security-critical refactor across two money paths)
- status: awaiting-approval
- created: 2026-08-29 (Prometheus / ulw-plan)

## User request (verbatim)
"Plan the fixes and go ahead with it" — referring to the review findings the user approved:
- C1: `native_webauthn.verify()` grants AAL3 from caller-supplied booleans — no crypto.
- P1: AP2 mandate verifier trusts `signature_present`/`human_held_key_signature`/`cart_hash_bound` booleans — no crypto.
- P2: ACP `complete_checkout` calls `confirm_checkout` (moves money) BEFORE checking `aal < required_aal`.
- C4: reference `IdempotencyRecord` has no unique constraint → TOCTOU double charge.

## Intent verdict (announced to user)
CLEAR — user named a concrete set of fixes and said "go ahead". No high-accuracy review requested.
Per ulw-plan, this is planning only: approval authorizes WRITING THE PLAN, execution happens in a
separate worker session (`/start-work`). I will not implement now or by proxy.

## Components ledger (topology lock)
| id | outcome | status | evidence path |
|----|---------|--------|---------------|
| N1 | openstore core `native_webauthn.verify` performs real signature+challenge+UV verification | to plan | `openstore/core/authority/native_webauthn.py:81-98`; model = `openstore/verify/__init__.py:129-178` |
| N2 | AP2 verifier fail-closed instead of trusting booleans | to plan | `openstore/core/authority/ap2.py:14-44`; `openstore/protocols/ap2/adapter.py:52-78` |
| N3 | ACP adapter checks AAL before calling confirm_checkout | to plan | `openstore/protocols/acp/adapter.py:83-105`; `api.py:140 required_aal=1` |
| N4 | reference IdempotencyRecord gets a unique constraint | to plan | `reference/merchant/models.py:160-165`; TOCTOU at `mcp_server.py:156->320` |
| N5 | DEPENDENT: existing tests reworked to carry real authority (not raw={}) | to plan | `tests/test_interop.py:158-159,217-219` encode the bug; model = `tests/test_webauthn_ceremony.py` |

Decision: C2 (reference Path A never calls verify_assertion_policy) and C3 (hardcoded JWT secret) and
H1/H2/H4/H5 — surface as SCOPE-OUT / deferred; user explicitly named only C1+P1+P2+C4 as "the fixes".
See approval-brief questions Q1-Q3 (owner-decision on scope).

## Key first-hand facts (explored, cited)
1. `native_webauthn.verify` (native_webauthn.py:81-98) hardcodes e2/e7/e8 True, defaults e1/e3/e6/e9 True
   off caller dict, reads e4 from `challenge_binding.uv` default `mode=="cart"`. `confirm_checkout`
   (`openstore/core/api.py:186-189`) → `verify` → `resolve_aal` → `cap_aal`; AAL3 with empty reasons.
2. The CORRECT stateless verifier already exists: `openstore/verify/__init__.py`:
   `_verify_webauthn_assertion` (129-146, ECDSA over auth_data+SHA256(client_data) w/ enrolled pubkey),
   `_verify_challenge_binding` (149-165, challenge == SHA256(policy_hash+cart_hash) in cart mode),
   `_verify_uv` (168-178). This is the model for the C1 fix.
3. Native `AuthorityPresentation.raw` carries the full completed assertion — built from `policy_token`
   (`openstore/protocols/mcp/adapter.py:73-74`), produced by `WebAuthnRP.complete_assertion`
   (`webauthn_rp.py:143-197`) which emits `webauthn.{authenticator_data,client_data_json,signature,uv,
   challenge_binding}` + `enrolment.public_key` + `policy`. Matches what `verify/` needs.
4. AP2 `_common` (ap2.py:14-30): e2=`bool(raw.signature_present default True)`, e7=True, e8=True.
   `verify_cart_mandate` (32-44): e5=`cart_hash_bound`, e4=`human_held_key_signature`. Adapter
   (ap2/adapter.py:76) sets `signature_present=True` default. `resolve_aal` with e5→AAL3. No crypto.
   UNMAPPABLE_CONSTRAINTS fail-closed is correct; keep.
5. ACP `complete_checkout` (acp/adapter.py:93-98) calls `confirm_checkout` first (creates Order+Hold,
   records spend — `api.py:193-215`), THEN checks `aal_level < required_aal` and returns STEP_UP_REQUIRED.
   `acp.verify` (acp.py) never sets e2 → AAL always 0 → `0 < 1` always → complete_checkout always
   STEP_UP_REQUIRED (P3 dead-end, fix here too).
6. Reference `IdempotencyRecord` (models.py:160-165): `client_id`/`idempotency_key` index=True only,
   NO UniqueConstraint. `checkout_confirm` SELECT (mcp_server.py:156) then INSERT (320). Concurrent
   same-key → double `create_order_and_payment_link`. openstore's own `IdempotencyRecord`
   (openstore/models.py:297) HAS the UniqueConstraint — the template to copy.
7. Existing tests ENCODE the bug: `test_interop.py:158-159` and `:217-219` call `confirm_checkout`
   with `raw={}` / `raw={"token_present": True}` and assert spend happens. The C1/P1 fix breaks these;
   they must be reworked to carry a REAL authority (begin_assertion→simulate→complete_assertion),
   the pattern in `tests/test_webauthn_ceremony.py:54-60`.
8. Test command: `uv run pytest -q` (current: 204 pass). No lint/typecheck configured.

## Decisions / defaults (CLEAR path)
- Test strategy: **TDD** (write failing test first, then fix). Security-critical = red-green-refactor.
  DEFAULT unless user overrides (Q2).
- C1 fix approach: reuse the existing `verify/` stateless logic in `native_webauthn.verify` — refactor
  `_verify_webauthn_assertion`/`_verify_challenge_binding`/`_verify_uv` into a shared helper (or import),
  call it from native_webauthn.verify, fail-closed via `_all_false()` on any missing/unverifiable proof.
- e3 freshness (`assertion_max_age_seconds`/`signed_at`): INCLUDE in C1 (matches verify/) — low cost,
  closes the freshness gap in the live path. (Q3 optional)

## Approval gate
- status: APPROVED (user: "Approve", all answers at defaults: Q1=include C2, Q2=TDD, Q3=include freshness)
- approval grants plan creation only; execution via /start-work
- pending action: after explicit okay → write `.omo/plans/secure-money-authority.md`.
- approach (summary): apply the existing stateless verifier to the live money-decision verifiers
  (native_webauthn + AP2), fix the ACP ordering dead-end, add the idempotency unique constraint,
  rework the raw={} tests, TDD, run full suite.
- Questions to user BEFORE approval (owner-decisions): see approval brief in chat.

## Review state
- review_required: false (no modifier requested; CLEAR, Standard). Optional dual review offered at delivery.

## Not in scope (Must-NOT-Have)
- C2 (reference Path A assertion verification), C3 (JWT secret), H1, H2, H4, H5 — unless user opts in (Q1).
- No new dependencies. No schema migration beyond adding one DB constraint index.
- No changes to `openstore/verify/__init__.py`'s public API unless required by the shared-helper refactor.
- Must NOT change compiler digest / AAL reason vocabulary / policy schema.
