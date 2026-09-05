# Stage 11 — Chat-native purchase flow: the handoff bridge

Self-contained per the operator-approved plan
`go-with-the-push-functional-hearth.md`. You do not need other stage files,
but Stage 10 (freeze) is the baseline this stage builds on.

## READ FIRST
- `AGENTS.md` (repo root) — the ten rules bind this stage.
- `go-with-the-push-functional-hearth.md` (approved plan) — the full design
  rationale for the handoff bridge lives there; this file is the stage
  contract distilled from it.
- `OPEN_QUESTIONS.md` Q-014 (handoffs table), Q-015 (studio mount + concrete
  webauthn routes), Q-016 (authority.* handoff reason codes), Q-017
  (amendment approve/reject routes).
- `OPENSTORE_PRD_v3.md` Part 12 (track-bar compliance / the human-in-chat
  flow), `SPECS/stage-07-agents.md` (BuyerBot / MerchantAgent, unchanged
  contracts this stage wires up rather than redesigns).
- `REGISTRY.json` — the closed sets this stage's routes/reason codes draw
  from.

## SCOPE (closed) — files this stage may create or modify across its phases
- `OPEN_QUESTIONS.md` (append-only, per R0.4)
- `REGISTRY.json`
- `SPECS/stage-11-chat-flow.md` (this file)
- `tests/test_registry_compliance.py`, `tests/sentinel/test_route_table_snapshot.py`
- `src/openstore/server.py`
- `src/openstore/surfaces/studio.py`, `src/openstore/surfaces/templates/policy_studio.html`
- `src/openstore/surfaces/static/policy_studio.html` (deleted, Phase 1)
- `src/openstore/core/webauthn_rp.py` (note-only in Phase 1; no functional
  change expected)
- `alembic/versions/0003_handoffs.py` (NEW, Phase 2)
- `src/openstore/models.py` (`Handoff` table, Phase 2)
- `src/openstore/core/handoff.py` (NEW, Phase 2)
- `src/openstore/notifier.py` (Phase 2/3)
- `src/openstore/agents/buyer_agent.py` (Phase 2/3)
- `src/openstore/psp/router.py` (Phase 3)
- `src/openstore/agents/merchant_agent.py` (Phase 4)
- `tests/stage03/test_policy_studio.py`, `tests/stage11/**` (NEW test dir for
  Phases 2-4)

No other file may be modified.

## BUILD

### S11.0 Protocol (Phase 0)
R0.4 ambiguity protocol for four identifier gaps this stage needs ahead of
implementation: the `handoffs` table (Q-014), mounting `policy_studio_router`
plus naming the four concrete `/internal/webauthn/...` paths (Q-015), new
`authority.*` reason codes for handoff-resolution failures (Q-016), and
amendment approve/reject route names reserved for Phase 4 (Q-017, names only
— not added to REGISTRY.json until Phase 4 implements them). REGISTRY.json
gains the four concrete webauthn paths and four `authority_reason_codes`
entries; `tests/test_registry_compliance.py` and
`tests/sentinel/test_route_table_snapshot.py` are updated in lockstep.

### S11.1 Make signing real (Phase 1)
- Mount `policy_studio_router(config)` in `server.py`, replacing the dead
  `/intent/studio` stub (`surfaces/static/policy_studio.html`, deleted).
- `surfaces/studio.py`: accept an optional `?token=` query param on
  `GET /intent/studio` as the Phase-2 handoff seam — fails loud (501) rather
  than stubbing a fake identity, since no `handoffs` table exists yet. The
  existing `X-Operator-Id` header path keeps working unchanged for the
  merchant-operator flow. Fix the `merchant_id`/`user_id` conflation:
  `merchant_id` is now the configured merchant (`_merchant_id(config)`), not
  the operator header string; `_current_aggregate` and
  `/internal/policy/blast-radius` are rescoped from
  `IntentPolicy.merchant_id == user_id` to a `webauthn_credential_id` join
  against the operator's own enrolled credentials, since a policy has no
  buyer/user column of its own.
- `templates/policy_studio.html`: fix the registration path's real WebAuthn
  bug — `navigator.credentials.create()` requires `BufferSource` for
  `challenge`, `user.id`, and `excludeCredentials[].id`; the register path
  passed base64url strings directly. The assertion (sign) path already did
  this correctly via `b64d()`; the register path now mirrors it.
- `core/webauthn_rp.py`: `begin_registration` returns no `timeout` key, and
  `mcp_server.py:409` reads it defensively via `.get("timeout")` (resolves to
  `None`, no crash). No PRD/config-pinned timeout constant exists to fill it
  with — left as-is; inventing one would violate R0.3.

### S11.2 The handoff table + chat loop (Phase 2 — built; see Q-014, Q-018)
`alembic/versions/0003_handoffs.py` (explicit `op.create_table`, real
`upgrade()`/`downgrade()`, safe on fresh and existing DBs — `0001` uses
`create_all`, `0003` must not); `models.Handoff` (token PK, `kind` closed set
`{policy, amendment}`, merchant/chat/request-text/expiry columns per Q-014);
`core/handoff.py` (create/resolve/consume/expire, fail loud on
expired/consumed per R0.5, raising the Q-016 `authority.handoff_*` codes);
`notifier.py` gets a live Discord client and DM send; `server.py` lifespan
starts/cancels it; `agents/buyer_agent.py` gets DM-first command handling,
checks for an active policy before shopping, creates a handoff + DM's the
link on none, and auto-resumes `request_text` on signing success (wired
through `studio.py`'s success path once Phase 2 lands).

### S11.3 Money reaches chat (Phase 3 — built; see Q-019)
Deliver `short_url`/amount/hold-deadline in the DM; deliver `cancel_token` as
a bot `cancel` command (POST, not a link, per PRD §3.7); schedule
`hold_release_worker_tick` on a 30s lifespan loop; pass a real `customer`
object to Razorpay; fix `callback_url` to prefer `public_base_url`; fix
`shop()["state"]` and `confirm()`'s missing WebAuthn assertion.

### S11.4 Negotiation, amendment, evidence (Phase 4 — built; see Q-020, Q-021)
Wire `MerchantAgent.negotiate` into the DENY branch of `shop()`; build the
missing `cart_delta` executor (today `negotiate` returns intent flags, not
carts); amendment flow: `draft_amendment` → handoff(`kind=amendment`) →
one-tap approval at the Q-017 routes → apply delta, increment version,
recompile → ALLOW; implement `/orders/{checkout_id}/evidence`, call
`create_poai_bundle` on a completed purchase with `human_intent` (chat
request text) and `notification` (DM `sent_at`/`receipt_digest`) so bundles
reach AAL2; mount `evidence_viewer.html`; DM the receipt link.

## MUST NOT
- No bespoke one-off "policy signed" callback outside the single `handoffs`
  abstraction (Phase 2) — one table, one link builder, one push path serves
  all four ceremonies (policy signing, amendment approval, payment, cancel).
- `core/` may not import `agents/` (import firewall,
  `tests/sentinel/test_import_firewall.py`); the chat push egress belongs in
  `notifier.py`, not in `core/`.
- Do not weaken or delete a red-team, sentinel, or registry-compliance test
  to make it pass — a legitimate semantic change gets the test updated
  deliberately, with the reason stated.
- Do not add an identifier to REGISTRY.json without a prior OPEN_QUESTIONS
  RESOLUTION (R0.2).
- Phase 1 does not touch `core/api.py`'s known `user_handle=merchant_id`
  checkout-time bug (checkout authorizes with the merchant's own credential,
  not the buyer's) — that is part of the Phase 2 identity-hole fix, not
  Phase 1's `studio.py` scoping fix.

## DONE WHEN (all exit 0) — full-stage exit criteria
- `python scripts/registry_diff.py` → prints nothing
- `pytest -q` (full suite) → 0 failures
- `pytest tests/sentinel/ -q` → pass (route table, schema snapshot, import
  firewall)
- `ruff check src tests` and `mypy src` → clean
- Live-server check: `/internal/webauthn/*` (concrete paths) and
  `/orders/{checkout_id}/evidence` appear in `openapi.json` paths
- Human-verified (report to operator, never self-certified): a real passkey
  registration + policy signature at `/intent/studio`; a DM'd `shop` request
  with no policy produces a signing link that, once signed, auto-resumes the
  order; a Razorpay test-link payment produces a "paid / held" DM and a
  working `cancel` command; a non-compliant request produces a visible
  negotiation, an amendment link, and an ALLOW after approval;
  `openstore-verify orders/<checkout_id>/evidence` reports AAL2.

STATUS: all four phases (S11.0-S11.4) are built and merged (see commits
`8442c8e`, `5844ac1`). The DONE WHEN block's automated checks
(`registry_diff.py`, `pytest -q` — 394/394 passing, sentinel suite, ruff,
mypy) are green as of this stage's completion; the "Human-verified" line
items (live Discord DM, live Razorpay test-link payment, live
`openstore-verify` AAL2 verdict) still require an operator with real
Razorpay/Discord credentials to confirm.

## COMMIT GATE
`stage(11): chat flow`
