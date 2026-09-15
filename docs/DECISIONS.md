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

## DECISION-027 | date: 2026-09-05T00:00:00Z | stage: 14
- Two new MCP tools close a federated buyer's order status/cancel gap: `list_orders`
  (read-only, no scope — matches `get_order`/`list_campaigns`) and `cancel_order` (gated
  `checkout:initiate`, the scope every federated buyer OAuth client already has from
  `federation-register-buyer` — no re-registration needed).
- This is a mechanical fix, not a new policy question: single-merchant `BuyerBot._handle_cancel`
  already worked (it shares the merchant's own DB session in-process), but `FederatedBuyerBot`
  inherits that method verbatim and it silently no-ops — `self.config` there is `BuyerSettings`
  (buyer.db, which never holds `Checkout` rows; those live in each merchant's own DB) and has
  no `razorpay` config at all. It reported "no open orders" instead of failing loud (R0.5).
- `cancel_order` is the first checkout-lookup tool in the codebase to check ownership.
  `get_checkout`/`get_order` do not — any caller who knows a `checkout_id` can read any
  checkout's state today. Left as-is (out of scope here); `cancel_order` adds the check
  because it mutates, not because the gap was noticed and ignored elsewhere.
- `checkout_id` is a required, caller-supplied argument, not "find my own latest" — deciding
  *which* order, across however many merchants a federated buyer reaches, is
  `buyer_agent.py`'s job (it can see every merchant the buyer is enrolled with); the tool only
  ever sees one merchant's DB and has no basis to guess.
- New `core/api.py:list_checkouts_for_buyer` — the first identity-scoped checkout query
  anywhere in `core/`; `studio.py`'s admin order listing is unfiltered and operator-only.
- Registered `checkout.not_found`, `psp.invalid_state`, and `auth.insufficient_scope` alongside
  the new `checkout.not_owned`. The first three were already raised (by `get_order`,
  `cancel_checkout_by_id`, and `_require_scope` respectively — the last also used by
  create_cart/update_cart/checkout_initiate/checkout_confirm already) and absent from
  REGISTRY.json — the same class of gap DECISION-023 fixed for `campaign.*`, except
  `registry_diff.py`'s AST scan only ever checked `CampaignValidationError(...)` call sites, so
  it never caught this one. A wider sweep found ~12 more unregistered `checkout.*`/`psp.*`
  codes (`psp.cancel_failed`, `psp.refund_failed`, `psp.amount_invalid`, etc.) — flagged here,
  not fixed: out of scope for this change, which only touches the three codes it actually raises.

## DECISION-028 | date: 2026-09-05T00:00:00Z | stage: 14
- MerchantBot (Q-034 RESOLUTION): a conversational, read-only reporting agent for the
  merchant — `openstore merchant-bot <config...>`, its own Discord identity (env var
  `MERCHANT_BOT_TOKEN`, distinct from any merchant's own `bot_token`, same pattern as the
  buyer process's `BUYER_DISCORD_BOT_TOKEN`), DM-only, no bang commands.
- Closed action set of exactly three read-only reports (`campaign_status`, `exposure`,
  `recent_orders`), plus `unknown` for anything else — including any request to change,
  approve, or mutate anything, which the system prompt tells the model to refuse rather than
  pretend to do. It can never actually approve/reject/pause a campaign or touch money: not
  because of the prompt wording, but because the action set contains no mutating action at
  all, so there is nothing for the LLM to misuse even in the worst case (R0.10 by omission,
  the same safety argument `cancel_order`'s server-side ownership check makes elsewhere).
- One-shot LLM classification (a single `llm_chat` call → closed JSON action → deterministic
  Python dispatch), not a multi-turn LangGraph loop like `BuyerGraph`. The buyer's loop earns
  its complexity from genuine iterative search/negotiation; a merchant asking "how's my
  campaign doing" needs exactly one lookup, so a second tool-calling graph would be
  complexity with no matching need.
- Runs as its own process reading directly from each merchant's own database, not through
  MCP/OAuth — this is the merchant's own trusted tool, not an arm's-length buyer, so there is
  no remote-caller trust boundary to cross. Takes any number of merchant config paths so one
  bot instance can cover one store or several (both `gelateria.yaml` and `chai.yaml` for the
  demo); a single Discord bot token can only safely log in from one process, which is why
  this could not simply be a second `discord.Client` embedded in one merchant's own
  `openstore serve`.
- Deliberately does NOT use `core/database.get_engine`/`session_scope` — that module caches
  ONE engine for the whole process (`_engine: Engine | None`, keyed by nothing), which is the
  right shape for every other component here (one merchant per process, always). MerchantBot
  is the one component that genuinely needs several merchants' databases open at once; going
  through the shared singleton would have silently pointed every merchant but the first at
  whichever database initialized it first. `MerchantBot` builds and keeps its own
  `{name: Engine}` instead (mirroring `get_engine`'s SQLite/WAL setup, not its cache).
- `agents/campaign_agent.py`'s private `_policy_headroom_minor` is promoted to
  `core/campaigns.compute_merchant_headroom` — it now has a second real caller (the
  `exposure` report), so it belongs with the other merchant-wide reporting functions rather
  than staying private to the campaign-drafting flow.
- No access control: responds to any DM. An allow-list of authorized Discord user IDs was a
  real, considered option (Q-034) given the sensitivity of what this reports; the user
  explicitly chose no restriction, matching `BuyerBot`'s existing lack of gating, over adding
  one.

## DECISION-029 | date: 2026-09-05T00:00:00Z | stage: 14
- MerchantBot gains a fourth action, `suggest_campaign` (the user asked directly: "can I not
  orchestrate or get suggested campaigns/offers from the merchant bot?"). Amends
  DECISION-028's "exactly three read-only reports" — this one is not read-only, but it is not
  a new mutating capability either: it runs the identical ingest → LLM draft → deterministic
  validate → persist DRAFT → PENDING_APPROVAL pipeline `openstore campaign draft` already runs
  from the CLI (`agents/campaign_agent.py` + `core/campaigns.create_campaign`/
  `submit_for_approval`), just reachable from chat instead of a terminal.
- Still cannot activate anything: PENDING_APPROVAL is the same non-authoritative artifact
  either path produces, and only a real WebAuthn ceremony at `/campaign/studio` can move it to
  ACTIVE — no chat surface can perform that (R0.10). `core/campaigns.py` still validates
  before anything reaches the DB (R0.9) regardless of which caller triggered the draft.
- Requires exactly one matched merchant — unlike the three report actions (which happily
  aggregate across every matched store), drafting a campaign for "all of them at once" when
  the merchant didn't say which store they meant would be guessing at something consequential
  enough not to guess at; asks which store instead.

## DECISION-030 | date: 2026-09-05T00:00:00Z | stage: 14
- `/campaign/studio` (and every other `Depends(_operator)` route) now accepts the operator
  identity as a `?operator=` query param in addition to the `X-Operator-Id` header — flagged
  earlier this session as a real gap, hit in practice the moment a real campaign was drafted:
  a plain browser navigation cannot set a custom header, so this page could never actually be
  opened by clicking a link, only by a JS-driven `fetch()`. `_Operator`'s own docstring
  already establishes this identity carries no authority of its own — it only selects which
  WebAuthn credential set a session uses — so accepting it as a query param weakens nothing;
  the real gate is the WebAuthn ceremony, unchanged.

## DECISION-031 | date: 2026-09-05T00:00:00Z | stage: 14
- Campaign Studio had no registration UI at all — only `policy_studio.html` (the buyer/policy
  flow) ever called `navigator.credentials.create(...)`; Campaign Studio only ever called
  `.get(...)` (an assertion), which requires a credential to already exist. A merchant operator
  who had never signed a policy had no way to register a passkey for campaign approval at all —
  found in practice the moment a real campaign needed approving.
- Added a "Register your passkey" panel to `campaign_studio.html`, reusing the existing generic
  `/internal/webauthn/register/begin`/`/complete` endpoints unchanged (they were already
  operator-scoped, not policy-studio-specific — nothing server-side needed to change). One-time
  per operator id, same as Policy Studio's enrolment panel.

## DECISION-032 | date: 2026-09-05T00:00:00Z | stage: 14
- Fixed a real money-path bug found live: `psp/razorpay_driver.py:create_payment_link`'s
  idempotent-replay branch (`existing_idem.response_status == 200`) restored only
  `psp_order_id`/`psp_payment_link_id` from the cached response, silently dropping
  `short_url` and `cancel_token`. A checkout hitting this path came back `success` — state
  HELD, correctly charged nothing — with no payment link and no way to cancel. Confirmed
  live: a federated order for Gelateria (chocolate gelato + sprinkles, sprinkles dropped by
  negotiation) landed exactly here — `build_federated_shop_result_embed` correctly rendered
  "Order placed." with an empty line list because `short_url` genuinely was `None` in the DB,
  not a rendering bug.
- `_finalize_payment_link_create` always stores all four fields (`psp_order_id`,
  `psp_payment_link_id`, `short_url`, `cancel_token`) in `response_body` on the real success
  path — the replay branch just wasn't reading two of them back out. Fixed to restore all
  four. Root trigger (what caused `create_payment_link` to be called twice with the same
  trace_id/client_id/checkout_id in the live case) not isolated — the fix is correct
  regardless of the trigger, since any legitimate idempotent replay must restore the full
  cached response, not a subset.
- The specific stuck checkout (`chk_mcp_b3655b9643df`) was repaired manually with a fresh
  trace_id, generating a real payment link outside the buggy cached path.

## DECISION-033 | date: 2026-09-05T00:00:00Z | stage: 14
- Fixed "any offers on gelato?" returning "no special offers" despite 4 ACTIVE, in-window,
  correctly-signed campaigns covering those exact SKUs. Root cause was NOT the data pipeline —
  verified live end-to-end: `list_campaigns`, `FederatingMCPClient`'s merchant_id stamping,
  and `_run_search`'s offer-matching in `buyer_graph.py` all worked correctly; the search
  tool_result the LLM received genuinely carried `discount_bps`/`campaign_id` on the matching
  items. The bug was that `_SYSTEM_PROMPT` never told the model those fields exist or that it
  should look at or mention them — so even when explicitly asked about offers, with the data
  sitting right there in its own context, it had no instruction to surface it.
- Added explicit guidance to the `search` action's prompt text: a result item MAY carry
  `discount_bps`/`campaign_title` (a real, already-verified discount, never invented), and the
  model should name it when relevant or asked, and say plainly when an item has none.
- New regression test pins the data side specifically (`test_search_tool_result_carries_the_
  discount_before_any_selection`, tests/stage08/test_campaign_demand_loop.py) — the LLM
  behavior itself isn't unit-testable, but the plumbing it depends on now has coverage beyond
  "does the final cart line carry the discount" (which was already tested).

## DECISION-034 | date: 2026-09-05T00:00:00Z | stage: 15
- Closed a real gap identified by review: campaign orchestration was purely reactive (a human
  or a chat message had to explicitly ask for a draft) and had no feedback loop from past
  campaign outcomes back into future drafts — so it could draft compliant discounts on
  request, but could not itself claim to grow revenue. Added both halves, deterministic
  Python on both sides (R0.5/R0.9 — the LLM decides neither when to trigger nor what
  counts as success).
- **Trigger**: `core/campaigns.py:detect_stalled_skus` flags a SKU with real 30-day demand
  (>= `campaign.stall_min_units_30d`, default 3 — filters out one-off sales) but zero units in
  the last 7. `should_auto_trigger` additionally excludes SKUs already covered by a live
  (ACTIVE/PENDING_APPROVAL) campaign, and enforces a per-merchant cooldown
  (`campaign.auto_trigger_cooldown_hours`, default 24h) independent of how many SKUs stall at
  once — bounds draft frequency regardless of signal volume. `auto_draft_campaign_if_stalled`
  runs the exact same draft -> validate_campaign -> DRAFT -> PENDING_APPROVAL pipeline
  `openstore campaign draft` and MerchantBot's `suggest_campaign` already use — this only
  decides WHEN to call it. A new background loop, `server.py:_campaign_growth_loop`
  (`campaign.growth_check_interval_seconds`, default 3600s), same shape as the existing
  hold-release and campaign-expiry loops (tick on a timer, log-and-continue on failure, never
  raise past its own tick). It can NEVER activate a campaign — PENDING_APPROVAL still requires
  the real WebAuthn ceremony at /campaign/studio (R0.10: no chat surface or background loop
  holds signing authority). Auto-triggered campaigns are tagged in `source_signals` (`trigger:
  "auto_stall_detected"`, `stalled_skus: [...]`) so the audit trail can tell an autonomous
  draft apart from a manually requested one.
- **Feedback**: `compute_campaign_outcome` compares units sold across a campaign's SKUs in its
  own live window so far against an equal-length window immediately before it started — no
  new schema, derived entirely from existing `Checkout` rows. `delta_pct` is `None` (not 0)
  when nothing sold before, since a SKU with zero prior sales has an undefined, not a 0%,
  lift. `recent_campaign_outcomes` feeds a merchant's last 3 campaigns that actually ran
  (ACTIVE/PAUSED/EXPIRED — never DRAFT/PENDING_APPROVAL/REJECTED) into `CampaignAgent.
  draft_campaign`'s prompt as `past_campaign_outcomes`, with explicit instruction to weigh a
  flat/negative repeat outcome rather than blindly re-suggesting the same play. `MerchantBot`'s
  `campaign_status` report now appends the same before/after numbers for any ACTIVE/PAUSED/
  EXPIRED campaign, so a merchant asking "how's it doing" gets a real answer instead of just
  the discount terms.
- No new REGISTRY.json entries: no new reason codes (existing `campaign.*` codes cover every
  new failure path), no new MCP tools, no DB migration (outcome is computed, not stored).
  14 new tests in tests/stage15/test_growth_loop.py cover the trigger's on/off conditions
  (no stall, live coverage exclusion, cooldown block + expiry), the outcome math (including the
  None-vs-zero distinction), that the autonomous path never reaches ACTIVE, and that the
  draft prompt actually carries past outcomes.

## DECISION-035 | date: 2026-09-10T00:00:00Z | stage: 16
- Per-cart passkey ceremony from chat (Q-033 RESOLUTION). kind=CART handoff +
  cart_payload, cart_studio.html, approve/reject routes, create_cart_handoff
  MCP tool. The WebAuthn cart binding already existed (webauthn_rp
  _binding_matches + api.create_checkout); this stage adds the chat ceremony
  around it rather than new crypto. Approval drives the same
  create_checkout_from_policy + create_payment_link path as amendments, with
  assertion_verified=True and server-side cart-hash recompute (R0.8).
- Production DB layer: get_engine is URL-keyed (dispose on URL change),
  QueuePool + pool_pre_ping for Postgres, PRAGMAs SQLite-only,
  lock_policy_row() row-level INV-11 lock for PG. Alembic 0001..0009 verified
  on Postgres 16. psycopg[binary] + prometheus-client added as dependencies.
- Identifier hygiene (Q-035): CommerceError/WebhookError/RazorpayError codes
  normalised to namespaced closed sets; registry_diff scans all
  reason-code-carrying exceptions (OAuth protocol errors and WebAuthn
  audit-only failure_types excluded by prefix gate); webhook handler persists
  psp_payment_id best-effort (Q-027 primary path live-ready, fixture-safe).
- Q-032 both halves: pre-release PSP reconcile in the hold loop (bounded,
  never blocks release) + discord_message_id stamp/edit plumbing (column +
  migration 0010, set_order_message MCP tool with ownership check, best-effort
  try_edit_dm in webhook worker and hold loop; DMs remain source of truth).
- Deploy: Dockerfile + compose (PG + 2 merchants + buyer + merchant-bot
  profiles), DEPLOY.md runbook, DATABASE__URL fix (from_yaml used
  model_validate which ignores env — containers migrated SQLite while
  believing PG; now honoured with fail-loud empty check, regression-tested).
  Proven: fresh compose brings both merchants to 0010/PostgresqlImpl with
  /health/ready green.

## DECISION-036 | date: 2026-09-14T00:00:00Z | stage: 17
- MCP wire conformance, hard cutover (mirrors Q-036 RESOLUTION).
- `POST /agent/mcp` speaks JSON-RPC 2.0 only: `initialize` (version
  negotiation, pinned `2025-06-18`), `notifications/initialized` (202, no
  body), `tools/list` (all 20 names + generated `inputSchema`), `tools/call`
  (`{content:[{type:text,text}],isError}`; business failures ride `isError`
  content with their closed-set reason codes, never envelope errors).
  Legacy `{"tool","arguments"}` bodies are answered `400`/`-32600`, never
  executed. Auth mechanism unchanged (Bearer → scopes); invalid bearer is
  `401`/`-32001` carrying the OAuth error in `data`.
- `HttpMCPClient` migrates to the wire path in the same commit (lazy
  `initialize` once per client, then `tools/call`; callers keep their
  `(tool, arguments[, require_auth])` Python signatures, `buyer_agent.py`
  untouched). `InProcessMCPClient` keeps internal dispatch.
- The manifest already advertised `2025-06-18`, so no manifest change; the
  promise is now true. UCP MCP *binding names* (`search_catalog` et al.)
  remain future work — wire conformance first, vocabulary aliases later.

## DECISION-037 | date: 2026-09-14T00:00:00Z | stage: 18
- Shopify read-only catalog adapter (mirrors Q-037 RESOLUTION).
- New `surfaces/shopify_catalog.py`: runtime client-credentials token mint
  (cached to expiry-60s, never persisted), REST `products.json` pagination
  (250/page, 40-page cap), per-variant normalization to the exact YAML item
  shape (`sku`, `name`, `unit_minor` paise-exact via Decimal, sorted `tags`,
  `related_skus: []`, text `description`). Fail-loud rules: non-INR shop
  currency, missing-SKU variants skipped with a count (zero usable → hard
  error), non-exact cent prices, missing `read_products`, HTTP non-200.
- `Settings.shopify` (optional block) is the only branch signal;
  `load_catalog` is otherwise untouched. `configs/shopify.yaml` runs the
  same Gelateria merchant against Shopify instead of YAML (own SQLite file;
  `buyer_bot_enabled: false` so it never double-logins beside the demo
  merchants). Write-back (inventory-levels, order webhooks), GST/shipping/
  COD, and multi-currency stay explicitly out of scope.

## DECISION-038 | date: 2026-09-14T00:00:00Z | stage: 18
- Cross-merchant consolidated budget check (buyer agent sums per-policy
  exposures + pending carts against a user-declared total before phase two)
  is accepted as a planning-time guardrail and DEFERRED until after the
  webchat slice + hiring artifacts. Rationale: it is defense in depth with a
  bounded residual risk (overshoot ≤ in-flight holds under concurrency;
  per-merchant hard caps hold regardless), not an enforcement guarantee —
  read-then-act races, no atomic cross-merchant commit, and merchant-reported
  exposure is untrusted input (R0.8 cuts both ways). Promoting it to hard
  needs shared spend state (the unsolved DECISION-007 half). Plumbing is one
  MCP field away (`resolve_policy` + `compute_policy_exposure`,
  `core/database.py:273) when its turn comes.

## DECISION-040 | date: 2026-09-15T00:00:00Z | stage: 20
- UCP MCP catalog binding aliases (mirrors Q-040 RESOLUTION; the README-named
  next interop slice after wire conformance).
- New tools `search_catalog` / `lookup_catalog` (REGISTRY-registered, ungated
  like the reads they alias) as thin adapters over `search_catalog_items` /
  `get_catalog_item`; `get_product` accepts `{sku}` | `{id}` |
  `{catalog: {id}}` and answers a superset `{item, product, ucp}`.
- UCP shape per the verified 2026-08-25 binding: arguments
  `{meta, catalog}`, `meta` tolerated-and-ignored, `context`/`filters`/
  `signals`/`attribution` accepted-and-ignored (R0.8: enforcement at
  checkout), lookup misses are success + `messages` (never `isError`), no
  batch cap, `ucp` envelope version `2026-08-25`, SKU as the canonical
  identifier. Manifest gains `dev.ucp.shopping.catalog.search` /
  `.catalog.lookup`; tool count 20 -> 22.

## DECISION-039 | date: 2026-09-14T00:00:00Z | stage: 19
- Anonymous storefront-lite page (mirrors Q-039 RESOLUTION; flagship webchat
  slice A, not the full buyer-in-browser — browser checkout needs a
  browser-safe auth design first, deferred with its own future Q-entry).
- `GET /chat` (REGISTRY-registered, ungated) serves zero-dependency
  `surfaces/static/chat.html`: same-origin catalog browse + client-side
  search, live signed-offer feed, deterministic related-SKU chips, evidence
  viewer links, studio links. 503 from the gated feeds renders as an
  explicit not-ready message (never a silent empty page). The Stage-1
  `storefront.html` stub at `/` is left untouched.
