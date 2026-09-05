# OpenStore — Decisions Log
# Append-only. Each entry: DECISION-NNN | date: <UTC> | stage: NN

## DECISION-001 | date: 2026-08-30T11:10:00Z | stage: 00
- Sidecar over plugin: credential absence (INV-2), R0.8, R0.10 enforcement requires separate process boundary.
- The sidecar runs as a separate process on the same domain (reverse proxy) or subdomain.
- Merchant's existing site is never modified.

## DECISION-002 | date: 2026-08-30T11:10:00Z | stage: 00
- PoAI moved to Stage 4: it is the product differentiator. Evidence layer before payments.

## DECISION-003 | date: 2026-08-30T11:10:00Z | stage: 00
- Demo stores are acceptance tests, not the deliverable. The deliverable is the pip-installable package.

## DECISION-004 | date: 2026-08-30T11:10:00Z | stage: 00
- Four Discord channel names defined in REGISTRY.json: #buyer-trace, #merchant-trace, #money-trace, #alerts.

## DECISION-005 | date: 2026-08-30T11:10:00Z | stage: 00
- Campaign check appended as #12 to preserve v2.1 transcript/golden validity for checks 1–11.

## DECISION-006 | date: 2026-08-30T11:10:00Z | stage: 00
- UAP watchlisted: NPCI UAP is pilot-stage, no public spec to pin constants against — watchlist only.

## DECISION-007 | date: 2026-08-30T11:10:00Z | stage: 00
- Multi-merchant delegation chains admitted unsolved (offline double-spend) — post-freeze.

## DECISION-008 | date: 2026-08-30T11:10:00Z | stage: 00
- Synthetic buyer swarm as a feature cut from MVP; red-team suite retained.

## DECISION-009 | date: 2026-08-30T11:10:00Z | stage: 00
- AXO optimizer loop cut from MVP.

## DECISION-010 | date: 2026-08-30T11:10:00Z | stage: 00
- Frontend: lean single-file HTML per surface, no SPA framework, no npm, no build step. Vanilla JS + fetch.

## DECISION-011 | date: 2026-08-30T11:10:00Z | stage: 00
- Import firewall: src/openstore/core/ and src/openstore/verify/ MUST NOT import any LLM SDK, network LLM client, or openstore.agents.*.

## DECISION-012 | date: 2026-08-30T11:10:00Z | stage: 00
- uv as package manager; uv.lock committed; uv sync --locked in CI; uv pip forbidden.

## DECISION-013 | date: 2026-08-30T11:10:00Z | stage: 00
- Money: integer minor units (paise) only. Floats forbidden anywhere money appears.

## DECISION-014 | date: 2026-08-30T11:10:00Z | stage: 00
- Time: RFC 3339 strings everywhere EXCEPT IntentPolicy.not_before / .expires_at (integer Unix seconds, grandfathered).

## DECISION-015 | date: 2026-08-30T11:10:00Z | stage: 00
- Currency: "INR" only. Single-tenant per sidecar process.
## DECISION-016 | date: 2026-09-02T00:00:00Z | stage: 10
- Feature freeze record (Q-008/009/010 resolved): sidecar integration contract SID-1..7
  implemented and green (PRD §1.4; SPECS stage-10 S10.5). Gates at freeze:
  `ruff check src tests` clean; `mypy src` clean; `python scripts/registry_diff.py`
  prints nothing; `pytest -q` → 323 passed, 1 skipped. SID commits: 66e8878 (Q-008/Q-009
  resolution + Q-010 contract), 270cc3e (Q-009 prompt-injection), 44f9276 (OAuth kid
  mypy), acbda3c (SID-1..7 implementation), this freeze.
- Q-011/012/013 provisional (unattended-agent, operator-ratify pending): hand-rolled
  Prometheus (B1), __version__ via importlib.metadata.version (B4), headless §11 steps
  3-5 (B6). Recorded in OPEN_QUESTIONS.md; not operator-ratified. PENDING-HUMAN
  verification items listed in SPECS/stage-10-freeze.md S10.6. Never self-certified.

## DECISION-017 | date: 2026-09-04T00:00:00Z | stage: 11
- Handoff table & module (mirrors Q-014 RESOLUTION).
- `handoffs` table adopted with the exact columns option (a) named — durable single-use token parking, `kind` ∈ {"policy","amendment"} closed, R0.3 hard on any other value.
- `core/handoff.py` new module (precedent: `core/health.py` per Q-010), fail-loud on expired or already-consumed tokens per R0.5.
- Phase 2 of the `go-with-the-push-functional-hearth.md` plan; NOT Phase 0/1.

## DECISION-018 | date: 2026-09-04T00:00:00Z | stage: 11
- Policy Studio router mounting (mirrors Q-015 RESOLUTION).
- `policy_studio_router` mounted in `server.py`; dead `/intent/studio` stub replaced.
- Four concrete `/internal/webauthn/*` paths added to REGISTRY.json `routes`; the `/internal/webauthn/*` wildcard stays in the `_EXPECTED_ABSENT` sentinel set permanently since FastAPI never mounts a literal `*` path.
- `/internal/policy/blast-radius` removed from `_EXPECTED_ABSENT`.

## DECISION-019 | date: 2026-09-04T00:00:00Z | stage: 11
- Authority.* handoff reason codes (mirrors Q-016 RESOLUTION).
- Four new codes in REGISTRY.json `authority_reason_codes`: `authority.handoff_not_found`, `authority.handoff_expired`, `authority.handoff_consumed`, `authority.policy_unsigned`.
- Widens `authority_reason_codes`'s meaning from "delegated-authority scheme rejection" to "authority-and-ceremony rejection" — semantic note carried per the Q-016 RESOLUTION.
- Pre-authorized Phase-0 addition ahead of Phase-2 raising code (mirrors Q-010 SID-* precedent, differs from Q-008 sequencing — record why explicitly).

## DECISION-020 | date: 2026-09-04T00:00:00Z | stage: 11
- Chat identity on Checkout & amendment routes (mirrors Q-017 + Q-018 RESOLUTIONS).
- Route names `/intent/amendment/{amendment_id}/approve` and `/intent/amendment/{amendment_id}/reject` reserved for Phase 4; REGISTRY entries added by Phase 4 in the same commit.
- Migration `0004_checkout_chat_identity` adds `checkouts.chat_platform`, `chat_user_id`, `chat_channel_id` (all nullable). Threaded from `BuyerBot._handle_shop` → `BuyerAgent.shop()` → `checkout_initiate` MCP tool → stamped on the row before `create_payment_link`.

## DECISION-021 | date: 2026-09-04T00:00:00Z | stage: 11
- Amendment application, evidence persistence, and the `initiate_hold` re-fetch bug (mirrors Q-019 + Q-020 + Q-021 RESOLUTIONS).
- Amendment scope is one-time, per-checkout — `_apply_amendment_delta` builds an unpersisted `model_copy` snapshot; the standing signed `IntentPolicy` is never mutated.
- Migration `0005_amendment_and_evidence` adds `handoffs.amendment_draft`, `checkouts.request_text`, `checkouts.poai_bundle` (all nullable JSON/String).
- New WebAuthn binding mode `amendment` mirrors `cart` mode.
- Evidence path: `authority.webauthn` carries only what is legitimately reconstructable (`policy_version`, `credential_id`) — no fabricated `authenticator_data`. `human_intent` and `notification` are honestly populated so e8/e9 hold; e2..e7 remain future work (option (c) from Q-019).
- `swap_sku` executor = "drop the blocked SKU's line item" (Q-021); no substitution engine, explicit future work.
- Bug fix inside this scope: `core/holdcancel.initiate_hold` gains optional `max_spend_per_tx_minor`/`max_spend_total_minor` params so the amendment path's relieved caps survive the INV-11 re-check.


## DECISION-022 | date: 2026-09-05T00:00:00Z | stage: 12
- Federated multi-merchant shopping: one buyer agent searches N merchant origins, builds one unified cart tagged per line with `merchant_id`, and creates SEPARATE per-merchant checkouts. Scoped as a superset of DECISION-007's cut, not a reversal of it.
- DECISION-007 cut multi-merchant *delegation chains* because offline double-spend is unsolved. That blocker is shared budget arithmetic across merchants that cannot see each other's spend. This work shares no budget: each merchant holds its own `IntentPolicy` row in its own DB, and `compute_policy_exposure(session, policy_id)` is per-policy/per-DB. Nothing is shared, so nothing can be double-spent. Delegation chains remain out of scope.
- `IntentPolicy.merchant_id` stays SINGULAR and the compiler's `merchant_lock` strict equality (`core/compiler.py`) is unchanged. PRD §307's `merchant_ids: list[str]` ("plural for multi-merchant roots") is deliberately NOT implemented: one policy row with one `max_spend_total_minor` spanning merchants that cannot see each other's spend is unenforceable — a ₹5000 total cap becomes ₹5000 *per merchant* in practice, which is exactly the DECISION-007 failure mode.
- Authorization is per-merchant enrollment, cached forever: the buyer registers a passkey and signs a policy once per merchant, on first contact there. No signing hub, no cross-origin policy distribution, no new trust root. Demo merchants share `rp_id`, so one physical authenticator yields one `WebAuthnCredential` row per merchant DB.
- Buyer agent moves OUT of the merchant process into its own role. `discord.buyer_bot_enabled` (default `true`, preserving single-process installs) gates only `BuyerBot.register`; the shared `discord.Client` still serves the notifier's four trace channels. Motivation was a live collision: `gelateria.yaml` and `chai.yaml` share `${DISCORD_BOT_TOKEN}`, so both processes logged in and answered the same DM.
- Catalog SKUs are namespaced `merchant_id::sku` throughout the buyer loop. Bare-sku keying let two merchants selling the same sku string collide, validating a selection against the wrong store's price. `cart_signature` is `(merchant_id, sku, qty)`; cart items carry `merchant_id`; a selection missing it fails loud (`malformed_selection`), an unknown one as `hallucinated_sku`.
- Checkout is two-phase: `create_cart` against every merchant first (so the compiler validates caps/tags/policy everywhere), then `checkout_initiate` only once all pass. A PSP failure mid-commit reports what succeeded and what did not — there is no cross-DB transaction to roll back and inventing one would be a false claim.
- `POST /oauth/token` (client_credentials) mounted. `/.well-known/oauth-authorization-server` had advertised `token_endpoint` since S6.4 while no route existed, so no out-of-process client could obtain a bearer token.
- SECURITY fix landed with it: `core/oauth.validate_client` gated secret verification on the secret being supplied (`if client_secret and client.client_secret_hash`), so omitting it skipped verification entirely — knowing a `client_id` was enough to mint a token carrying `cart:write`/`checkout:initiate`/`checkout:confirm`. Latent while the only caller was in-process; a live auth bypass the moment `/oauth/token` became reachable. A registered secret now makes the client confidential and the secret mandatory.
- New MCP tools `resolve_policy` and `create_policy_handoff` (14 -> 16), both thin adapters over `core/handoff.py`, both `catalog:read`. Needed because `BuyerBot._handle_shop` called `require_active_policy`/`create_handoff` against the local DB, which a remote buyer process cannot do.

## DECISION-023 | date: 2026-09-05T00:00:00Z | stage: 12
- Campaign reason codes enter the closed set (mirrors Q-028 RESOLUTION).
- `campaign.*` added to `error_namespaces`. Eleven codes added to `reason_codes`: the nine
  already raised by `core/campaigns.py` (`campaign.not_found`, `campaign.sku_not_found`,
  `campaign.discount_out_of_bounds`, `campaign.invalid_window`, `campaign.sku_on_blocked_list`,
  `campaign.empty_content`, `campaign.injection_content`, `campaign.no_webauthn_approval`,
  `campaign.max_active_exceeded`) plus `campaign.webauthn_verification_failed` and
  `campaign.invalid_state_transition` required by the lifecycle repair (DECISION-024/025).
- Root cause recorded: `scripts/registry_diff.py` was a shape validator only (required keys,
  `enums_are_exhaustive`, list duplicates). It never scanned source for raised identifiers, so
  a nine-code R0.2 violation sat in the tree while the differ printed nothing and exited 0. The
  differ is extended in the same commit to scan `CampaignValidationError(` sites and diff the
  first argument against `reason_codes`. Fixing the codes without fixing the differ would leave
  the detection gap that produced them.
- `orchestration.*` stays registered and unused. Removing a namespace is out of this scope.

## DECISION-024 | date: 2026-09-05T00:00:00Z | stage: 12
- WebAuthn binding mode `campaign` (mirrors Q-029 RESOLUTION).
- SECURITY: `core/campaigns.activate_campaign` accepted any truthy `webauthn_assertion` dict and
  never invoked the RP — `{"x": 1}` was sufficient to move a campaign to ACTIVE and publish a
  signed, agent-discoverable offer. PRD §9.7 ("cannot publish without a WebAuthn approval") and
  INV-13 were both unenforced. Compounding it, `POST /campaign/{id}/approve` and `/reject` in
  `server.py` carried no authentication at all — no operator dependency, no OAuth scope — so the
  transition was reachable anonymously by anyone who could route to the sidecar.
- Fix: `activate_campaign` calls `complete_assertion` (`core/webauthn_rp.py`) with
  `binding={"mode": "campaign", "campaign_id": <id>}` and raises
  `campaign.webauthn_verification_failed` on rejection. Because the `campaign_id` is inside the
  binding, an assertion approving campaign A cannot be replayed onto campaign B.
- Both routes move from `server.py` into `policy_studio_router` (`surfaces/studio.py`) and gain
  `Depends(_operator)`, the same session gate `/internal/policy/blast-radius` already uses. The
  operator header is the session gate; the WebAuthn assertion remains the authority.
- Precedent: DECISION-021 introduced the `amendment` binding mode by the same route. Binding
  modes are not a REGISTRY closed set; they are recorded here.

## DECISION-025 | date: 2026-09-05T00:00:00Z | stage: 12
- Campaign lifecycle repair, orchestration trigger, and four new routes (mirrors Q-030 RESOLUTION).
- PRD §9.4 specifies `DRAFT -> PENDING_APPROVAL -> ACTIVE -> (PAUSED | EXPIRED)`. The tree
  implemented `DRAFT -> ACTIVE` only, leaving `PENDING_APPROVAL`, `PAUSED`, and `EXPIRED`
  unreachable. Because `static/campaign_studio.html` rendered its Approve/Reject controls only
  for `PENDING_APPROVAL`, the Studio could never display an actionable campaign — its three
  handlers were `alert('... not yet implemented - Stage 8')`.
- Compounding it, the pipeline had no producer: `CampaignAgent.draft_campaign()`,
  `create_campaign()`, and `validate_campaign()` had zero callers in `src/`, so R0.9's
  deterministic validator never ran on the only live write path. `create_campaign` now calls
  `validate_campaign` before `session.add`.
- Routes added: `/campaign/<campaign_id>/pause`, `/admin/campaigns`, `/admin/orders`,
  `/.well-known/ucp`. `/admin/*` is retained alongside the two concrete admin paths per the
  DECISION-018 precedent.
- Drafting deliberately gets NO route. PRD §9.2 makes the merchant a reviewer, not an author, so
  the trigger is the `openstore campaign draft` CLI command and the Studio is a review surface.
  The hand-entry create form in the old Studio stub is removed as off-spec.
- `EXPIRED` is set by `_campaign_expiry_loop` in `server.py`, modelled on the existing
  `_hold_release_loop` (same `while True` / `asyncio.sleep` / cancel-propagating shape). No
  scheduler dependency is added.
- `config.campaign.max_active` is now read (it was defined, written into every scaffolded YAML,
  and never used — `core/campaigns.py` hardcoded `5`), and the active count is scoped by
  `merchant_id`.
- `agents/campaign_agent.py` loses its `except Exception:` swallow (the only R0.5 violation among
  the agents) and its invented `200000` headroom constant, which now derives from active
  `IntentPolicy` caps.

## DECISION-026 | date: 2026-09-05T00:00:00Z | stage: 12
- Protocol manifest tells the truth; UCP discovery added (mirrors Q-031 RESOLUTION).
- REMOVED from `surfaces/wellknown.py` `protocols[]`: the `acp` entry advertising version
  `2024-11-01` (a release ACP never published) at `/agent/acp` (which returns
  `not implemented`) with `authority_schemes: ["acp_delegated_token", ...]` (no code accepts
  one). The route stays mounted and registered but now answers `not_implemented` with a
  pointer to `/agent/mcp` and `/.well-known/ucp`.
- ADDED `/.well-known/ucp` (`build_ucp_manifest`). UCP is Google/Shopify's whole-journey
  discovery standard (2026-01-11) and explicitly supports MCP as a transport, so the sidecar
  can declare its real surfaces without a new protocol implementation. Exactly two
  capabilities are declared — `dev.ucp.shopping.checkout` and `dev.ucp.shopping.discount` —
  because those are the two that work. Fulfilment and order management are omitted.
- `payment_handlers[0].flow` is `hosted_payment_link`, not a delegated payment token: the
  buyer pays on the PSP's hosted page and no agent holds a credential (R0.10, INV-2). Stated
  in the manifest so an agent never waits for a token it will not receive.
- A sentinel asserts every operation named in the UCP manifest exists in
  `REGISTRY.mcp_tools`, so this manifest cannot drift into promising a missing tool (R0.2).
- Positioning note (no code): the signed `IntentPolicy` is structurally AP2's Intent Mandate
  and the per-cart assertion is its Cart Mandate, arrived at independently and with
  deterministic compiler enforcement AP2 does not specify. An AP2 adapter remains future
  work. x402 is explicitly NOT pursued: it is a stablecoin micropayment protocol for
  machine-to-machine API calls, and DECISION-015 fixes this sidecar to INR/UPI.
