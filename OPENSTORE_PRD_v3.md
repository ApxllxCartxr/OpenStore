# OpenStore — Product & Engineering Requirements Document (PRD)

**Version:** 3.0 — FINAL, normative, self-contained. Supersedes v2.1 in full.
**Track:** AI Growth & Agentic Commerce — (1) make a merchant transactable by an AI buyer
end to end; (2) make agent traffic a revenue channel, not a liability.
**Product shape:** an **installable sidecar package** — `pip install openstore[razorpay]` —
that makes ANY merchant agent-transactable in one afternoon. Demo storefronts exist only
to dogfood the package through its public install surface.
**Self-containment note:** all normative detail formerly in v2.1 is inlined here. No
external document is required to implement this. Resolutions Q-001…Q-004 (2026-08-30) are
incorporated into the body sections they touch.

---

## Part 0 — Rules for the implementer (human or model)

### 0.1 The ten rules

- **R0.1** — MUST / MUST NOT / SHOULD / MAY are used as in RFC 2119.
- **R0.2 — Never invent an identifier.** Field names, reason codes, route paths, function
  signatures, table names, column names, exit codes, CLI flags, tool names, scope strings,
  node names, channel names, and config keys are **closed sets defined in this document**.
  If a name you need is not here, it does not exist. Do not pluralise, abbreviate,
  re-case, or "improve" a name defined here.
- **R0.3 — Never invent a value.** Every enum here is exhaustive. A value outside an enum
  MUST cause a hard error — never a fallback branch, never a silent default.
- **R0.4 — When this document is silent, stop and ask.** Part 11 (DECISIONS) records every
  resolved question. If you hit a decision not answered here, append it to
  `OPEN_QUESTIONS.md` (template §0.5), commit, and STOP the stage. Do not pick a default
  and proceed. In a cryptographic evidence system, a wrong guess silently invalidates
  everything already issued; that failure is unrecoverable.
- **R0.5 — Fail loud.** No `except Exception: pass`, no silent coercion, no defaulting a
  missing required field, no retrying a non-retriable error. Every rejection carries a
  reason code from the closed sets in Part 7.
- **R0.6 — The legacy OTP path is retired, not maintained.** The Discord-DM + OTP
  per-cart approval flow is replaced in full by the WebAuthn IntentPolicy path.
- **R0.7 — Verify external claims against primary sources.** Any statement about an
  external system marked `[verify-at-build]` MUST be confirmed against the live test-mode
  API or the primary spec, captured, and pinned as a constant with a source comment. If
  the source is unreachable, that is an OPEN_QUESTION. Inventing the constant from memory
  is the one unforgivable act.
- **R0.8 — Never trust agent-supplied money data.** Totals, prices, cart hashes, and
  policy state MUST be recomputed server-side from the database of record.
- **R0.9 — LLM output is a proposal, never a command.** Every agent-produced artifact that
  touches money, catalog truth, policy, or fulfillment MUST pass through the same
  deterministic validators as any external input. Agents propose; the compiler disposes.
  There is no agent code path that bypasses `compile_decision()`.
- **R0.10 — Credential absence is the guarantee.** Reasoning agents MUST NOT hold payment
  keys, PSP credentials, or signing material. The execution server holds them; agents hold
  nothing.

### 0.2 Global encoding rules

Money: integer minor units (paise) only; money fields end in `_minor`; floats forbidden.
Time: RFC 3339 strings (UTC, `Z` suffix, second precision) everywhere EXCEPT
`IntentPolicy.not_before` / `.expires_at` and attestation `iat`, which are integer Unix
seconds (grandfathered — do not "fix"). Currency: `"INR"` only. Timezone-aware datetimes
throughout (`datetime.now(timezone.utc)`, never `utcnow()`).

### 0.3 STEP ZERO — the first action of any build (Q-001)

Before any application code exists, the implementer MUST: write `AGENTS.md` at repo root
with EXACTLY the content of §0.4; create `OPEN_QUESTIONS.md` (§0.5); run `uv init`; and
commit as `chore(agents): implementation contract`. No stage may begin until `AGENTS.md`
exists at repo root. Project structure is normative in §1.3; the dependency stack is
normative in Part 4. Q-001 is resolved by this section.

### 0.4 AGENTS.md — verbatim content to write at repo root

```markdown
# AGENTS.md — OpenStore Implementation Contract
# Read at the start of every session. Obey absolutely.

You are implementing the OpenStore sidecar package from OPENSTORE_PRD.md v3.0.

## The ten rules (PRD §0.1, verbatim)
- R0.1  MUST/MUST NOT/SHOULD/MAY per RFC 2119.
- R0.2  NEVER invent an identifier. All names are closed sets in REGISTRY.json.
        If a name is not there, it does not exist.
- R0.3  NEVER invent a value. Enums are exhaustive. Unknown value => hard error,
        never a fallback, never a silent default.
- R0.4  When the PRD is silent, STOP. Append to OPEN_QUESTIONS.md (template below),
        commit, and stop the stage. Do NOT pick a default.
- R0.5  Fail loud. No silent except, no coercion, no defaulting, no retrying
        non-retriable errors. Every rejection carries a closed-set reason code.
- R0.6  The legacy OTP path is retired. Do not resurrect it.
- R0.7  [verify-at-build] markers MUST be confirmed against live test-mode responses
        and pinned as constants with a source comment. Never invent a constant.
- R0.8  NEVER trust agent-supplied totals, prices, cart hashes, or policy state.
        Recompute server-side.
- R0.9  LLM output is a proposal, never a command. All agent artifacts pass through
        compile_decision() and the deterministic validators. No agent bypass path exists.
- R0.10 Reasoning agents hold NO payment keys, PSP credentials, or signing material.

## Hard constraints
- Money: integer minor units (paise) only. Floats forbidden anywhere money appears.
- Time: RFC 3339 everywhere EXCEPT IntentPolicy.not_before / .expires_at and
  attestation iat, which are integer Unix seconds (grandfathered — do not "fix").
- Currency: "INR" only. Single-tenant per sidecar process.
- Do not refactor, rename, or "improve" anything outside the current stage's SCOPE.

## Closed sets are executable
- REGISTRY.json is the single source of truth for every identifier and enum.
- Run `python scripts/registry_diff.py` before EVERY commit. It MUST print nothing.
- `pytest tests/test_registry_compliance.py` MUST pass. Code containing an identifier
  absent from REGISTRY.json, or a REGISTRY.json entry with no implementation, is red.
- You may NOT add an identifier to REGISTRY.json yourself. A missing identifier is an
  OPEN_QUESTION (R0.4), not a permission slip.

## Crypto is pinned, never improvised
- GOLDEN/ contains canonical-JSON vectors, hash-chain vectors, a known-good signed PoAI
  bundle, WebAuthn fixtures, ledger lifecycle vectors, and 40+ compiler decision vectors.
- Your implementation MUST reproduce these byte-for-byte. If output differs, the CODE is
  wrong, the vector is right. Do not "fix" a golden vector.

## Stage discipline
- Work exactly one stage at a time, from SPECS/stage-NN-*.md.
- A stage is DONE only when every command in its "DONE WHEN" block exits 0.
- Commit message format: `stage(NN): <slug>`.

## Ambiguity protocol (R0.4)
When you hit an unspecified decision:
1. Check DECISIONS.md (PRD Part 11) — it may already be resolved.
2. If not, append to OPEN_QUESTIONS.md:

## Q-NNN | stage: NN | date: <UTC>
- What is ambiguous:
- Options considered:
- Blocked since: <UTC>

3. Commit `docs(open-questions): Q-NNN` and STOP the stage.
4. Resume only after a human writes a `RESOLUTION:` block under Q-NNN.

## The one unforgivable act
Inventing a `[verify-at-build]` constant from memory. It silently invalidates every
piece of evidence already issued. When in doubt: OPEN_QUESTION and stop.
```

### 0.5 OPEN_QUESTIONS.md template

```markdown
# OpenStore — Open Questions
# Append-only. One entry per ambiguity. Never edit a resolved entry.

## Q-001 | stage: 00 | date: <UTC>
- What is ambiguous:
- Options considered:
- Blocked since: <UTC>
- RESOLUTION: <human writes here; then remove the block and proceed>
```

---

## Part 1 — Product definition

### 1.1 What OpenStore is

An **installable Python sidecar** that sits beside a merchant's existing store and makes
them transactable by AI buyers end to end, while turning agent traffic into a governed
revenue channel. The merchant's existing site is **never modified** — the sidecar runs as
a separate process on the same domain (reverse proxy) or a subdomain.

### 1.2 The three-step install contract

```bash
pip install openstore[razorpay]
openstore init --merchant "Gelateria Milano" --currency INR
openstore serve gelateria.yaml
```

The merchant then: (1) fills `gelateria.yaml` with SKUs in integer paise + tags; (2)
pastes Razorpay **test-mode** keys into `.env`; (3) opens Policy Studio and signs an
IntentPolicy with a passkey. The sidecar then auto-serves: catalog feed, well-known
manifests, MCP endpoint, ACP/AP2 endpoints, hold/cancel surfaces, the evidence viewer, and
the campaign feed.

### 1.3 Package layout

```
openstore/
├── pyproject.toml            # uv-managed; PEP 621; [project.optional-dependencies] razorpay
├── AGENTS.md                 # §0.4 content, verbatim (STEP ZERO)
├── REGISTRY.json             # Part 7 — every closed set, machine-readable
├── src/openstore/
│   ├── __init__.py
│   ├── config.py             # YAML+env loader; fails loud on unknown keys (R0.3)
│   ├── core/                 # deterministic spine — NO LLM imports permitted in this tree
│   │   ├── compiler.py       # compile_decision() — check 0 + checks 1–12, normative order
│   │   ├── aal.py            # AAL ladder, e1–e9 predicates, first-match-wins
│   │   ├── ledger.py         # double-entry append-only ledger (INV-5, INV-5a)
│   │   ├── idempotency.py    # INV-3
│   │   ├── poai.py           # bundle assembly, 9 sections, hash chain
│   │   ├── webauthn_rp.py    # relying party (INV-2, INV-10)
│   │   ├── holdcancel.py     # hold/release/cancel state machine
│   │   ├── health.py         # SID-2 readiness + gate; SID-7 metrics (hand-rolled Prometheus)
│   │   └── api.py            # Commerce Core API — single entry for all adapters
│   ├── psp/
│   │   └── razorpay_driver.py# payment links, webhooks, sweeper (INV-4,6,7)
│   ├── surfaces/
│   │   ├── mcp_server.py     # 14 MCP tools (closed set)
│   │   ├── wellknown.py      # 6 well-known manifests
│   │   ├── catalog.py        # agent-readable catalog + campaign feed
│   │   ├── studio.py         # Policy Studio + Campaign Studio (lean HTML)
│   │   └── storefront.py     # demo storefront (lean HTML)
│   ├── agents/               # reasoning agents — keyless by construction (R0.10)
│   │   ├── llm.py            # single LLM provider interface; model = config key llm.model
│   │   ├── buyer_agent.py    # discord.py bot, MCP client, planning loop
│   │   ├── merchant_agent.py # A2A: negotiation, recovery, bundler, narrator
│   │   └── campaign_agent.py # Campaign Orchestrator (Part 9)
│   ├── verify/
│   │   ├── cli.py            # `openstore-verify` — offline verifier, exit codes 0–4
│   │   └── checks.py         # the 14 verification checks
│   └── notifier.py           # Discord trace emitter — 4 channels (§7.6)
├── scripts/
│   ├── registry_diff.py      # prints any code/registry identifier mismatch
│   └── capture_constants.py  # [verify-at-build] helper — pins live API responses
├── tests/
│   ├── test_registry_compliance.py
│   └── sentinel/             # drift sentinel: route/enum/schema snapshots + import firewall
├── GOLDEN/
│   ├── canonical/  hashchain/  poai/  webauthn/  compiler/  ledger/
├── SPECS/                    # stage-01 … stage-10 contracts
├── DECISIONS.md              # Part 11
└── OPEN_QUESTIONS.md
```

**Import firewall (binding):** `src/openstore/core/` and `src/openstore/verify/` MUST NOT
import any LLM SDK, network LLM client, or `openstore.agents.*`.
`tests/sentinel/test_import_firewall.py` asserts this statically.

### 1.4 Sidecar integration contract (SID-1 … SID-7)

Authorized by OPEN_QUESTIONS Q-010 (option a). Normative for deploying the sidecar
beside a merchant's existing store in either of the two supported topologies. Each SID
is a MUST (RFC 2119). Tests: `tests/stage10/test_sid_integration.py` and
`tests/stage10/test_sid4_kill_restart.py`.

**Topologies** (SID-1). OpenStore binds to a public origin derived as follows:
- `same-origin` (default): sidecar sits behind the merchant's reverse proxy; its public
  URLs (`storefront`, `url`, OAuth `issuer`) derive from the incoming request.
- `subdomain`: merchant sets `public_base_url` (e.g. `https://agent.gelateria.example`).
  When set, every public URL MUST use it.
New config key: `public_base_url` (`str | None`, default `null`).
CLI: `openstore init --deployment same-origin|subdomain`; `subdomain` REQUIRES
`--public-base-url` else the process exits non-zero (`fail fast`, R0.5).

**Health & readiness** (SID-2). `GET /health/live` → 200 always (liveness).
`GET /health/ready` → 200 `{"status":"ready"}` only when all of: config loaded, DB
reachable, schema present (migrations or test `create_all`), Razorpay test-mode keys
(`key_id` MUST be prefixed `rzp_test_`), and PoAI signing keys derivable. Otherwise 503
with `reason_codes`. While not ready, any **gated** agent/money route
(`/agent/*`, `/campaign/{id}/approve|reject`, `/hold/{cancel_token}/cancel`,
`/webhooks/razorpay`) MUST return 503 `service_unavailable`. Infra/discovery routes stay
open. Reason codes: `health.<check>.<status>`.

**Startup ordering** (SID-3). Boot order on `openstore serve`: load config → run
Alembic migrations → derive signing keys → start workers → bind HTTP. Migrations run
before the server accepts traffic; `apply_migrations()`, guarded by a schema-ready flag
so double-migration is impossible on a single process.

**Failure semantics** (SID-4). Crash/kill between PSP `create` and `create` returning
MUST NOT double-pay or orphan a held checkout. Recovery keys off the persisted
`psp_payment_link_id`; a dedupe by `reference_id` rejects a second PSP link for the same
checkout and re-attaches the recovered id. See `test_sid4_kill_restart.py`.

**Origin security boundary** (SID-5). When `public_base_url` is set, its host MUST equal
`webauthn.rp_id` (RP ID); a mismatch fails at app build (500/startup), never silently.
CORS `allow_origins` is pinned to the merchant origin only — a foreign Origin is blocked.

**Versioning & rollback** (SID-6). `openstore.__version__` MUST equal
`pyproject.toml [project] version` (PEP 621 canonical), read at runtime via
`importlib.metadata.version("openstore")`. The agent-card manifest and the FastAPI app
SHOULD expose `version`. A rollback to a prior pip release lines up with the prior
manifest/DB migration set.

**Observability** (SID-7). `GET /internal/metrics` MUST return Prometheus text
exposition (verified hand-rolled form, no new dependency, Q-011): at least
`openstore_health_ready`, `openstore_checkout_hold_state{label=...}`,
`openstore_ledger_balance_minor{label=...}`, `openstore_reconciliation_drift_total`.
Drift is recomputed from the latest reconciliation sweeper audit entry (server-side
truth, R0.8) — never a fabricated constant.

---

## Part 2 — Architecture (three isolated layers)

1. **Buyer Agent** — consumer-facing LLM (discord.py bot). MCP *client* with a real
   planning loop: goal → search → policy-aware cart assembly → checkout → hold monitoring.
2. **Merchant Reasoning Agents** — LLMs that recommend, negotiate, draft campaigns, and
   narrate evidence. **Physically hold no keys** (R0.10, import firewall).
3. **Merchant Execution Server** — the deterministic FastAPI core. Holds keys, runs the
   compiler, moves money via Razorpay payment links.

The WebAuthn Intent Compiler gates every purchase against a pre-signed human policy. AAL
0–3 prices reversibility. Every transaction produces a PoAI evidence bundle verifiable
offline 90+ days later in a dispute.

---

## Part 3 — Normative schemas

### 3.1 IntentPolicy (policy_version = 2) — exact fields

```python
class IntentPolicy(BaseModel):
    policy_version: int = 2
    merchant_ids: list[str]              # sorted; the merchant lock, plural for multi-merchant roots
    currency: str                        # "INR"
    max_spend_per_tx_minor: int          # per-transaction cap
    max_spend_total_minor: int           # cumulative budget over the policy's life (PER-POLICY, §3.2c)
    max_transactions: int                # count cap
    allowed_tags: list[str]              # tag allowlist (empty = unconstrained)
    tag_mode: str = "all"                # "all" | "any"
    blocked_skus: list[str] = []
    not_before: int                      # Unix seconds (int by exception, §0.2)
    expires_at: int                      # Unix seconds
    assertion_max_age_seconds: int = 86400   # signed; feeds AAL predicate e3
    fulfilment_mode: str = "all_or_nothing"  # signed, never agent-chosen
    required_skus: list[str] = []            # for fulfilment_mode "required_subset"
```

`policy_version` MUST be exactly `2`; a v1 policy is rejected with `LegacyPolicyError`,
never mapped. Any `currency != "INR"` is rejected with `policy.currency_mismatch`.

### 3.2 The Intent Compiler — check 0 + checks 1–12, normative order, stop at first failure

**Check 0 — `human_authority_present` (Q-004).** Runs BEFORE check 1, only when
`policy.no_human_authority == False`: if no valid WebAuthn assertion accompanies the cart,
return `CompilerResult(allowed=False, reason_code="assertion_required")`. If an assertion
IS present, flow continues into checks 1–12 and `compute_aal_level()`. This is a
precondition gate, not a policy-content check — it is numbered 0 so checks 1–12 keep
their numbers and golden-vector validity. `compile_decision()` MUST return this result,
NEVER raise, on the missing-assertion path (R0.5 — every business denial is a returned,
reason-coded result so PoAI §3.3.6 `adjudication.transcript` records it uniformly). The
`ValueError` formerly on the missing-assertion path is DELETED.

| # | Check | Reason code on failure | Pass condition |
|---|---|---|---|
| 1 | `currency_match` | `policy.currency_mismatch` | `policy.currency == context.currency == "INR"` |
| 2 | `merchant_lock` | `policy.merchant_mismatch` | `context.merchant_id in policy.merchant_ids` |
| 3 | `policy_not_before` | `policy.policy_not_yet_valid` | `evaluated_at_unix >= not_before` |
| 4 | `policy_expiry` | `policy.policy_expired` | `evaluated_at_unix < expires_at` |
| 5 | `transaction_count` | `policy.tx_count_exceeded` | `transactions_count + 1 <= max_transactions` |
| 6 | `item_qty` | `policy.qty_invalid` (or `policy.sku_duplicate`) | each item: `isinstance(qty,int) and qty>=1`; no duplicate SKU |
| 7 | `item_blocked_sku` | `policy.sku_blocked` | each `sku not in blocked_skus` |
| 8 | `item_tag_allowlist` | `policy.tag_violation` | per `tag_mode` semantics |
| 9 | `spend_per_tx` | `policy.spend_per_tx_exceeded` | `total_minor <= max_spend_per_tx_minor` |
| 10 | `spend_envelope` *(delegated only)* | `policy.spend_envelope_exceeded` | `chain_state.spent_minor + total_minor <= envelope_budget_minor` |
| 11 | `spend_cumulative` *(root budget)* | `policy.spend_cumulative_exceeded` | `spent_minor + total_minor <= max_spend_total_minor` |
| 12 | `campaign_validity` *(v3.0, appended)* | `policy.campaign_inactive` \| `policy.campaign_outside_window` | referenced campaign exists, is `ACTIVE`, and now ∈ `[starts_at, ends_at)` |

**§3.2a — Aggregate cap (from DECISIONS §11.1.12).** Merchant config
`per_user_aggregate_cap_minor` (demo default 500000) is enforced at policy signing and
re-checked at `checkout_confirm`; keyed on enrolled `user_id` so one human with several
credentials or policies shares one ceiling per merchant. Failure: `policy.aggregate_cap_exceeded`.

**§3.2b — Check 12 (v3.0).** Runs only when a cart line references a `campaign_id`.
Appended (not inserted) so v2.1 transcript format and golden vectors for checks 1–11
remain valid. Discount application is computed **server-side** (R0.8); the discounted
total is what checks 9–11 evaluate.

**§3.2c — Check 11 scoping (Q-003).** `spend_cumulative` computes `spent_minor` as the sum
of `CAPTURE` legs (minus `REFUND`/`RELEASE`) joined via `LedgerEntry.reference_id →
Checkout.policy_id`, restricted to Checkout rows **whose `policy_id` equals the policy
under evaluation**. `max_spend_total_minor` is PER-POLICY — never per-merchant, never
process-wide. Do NOT add `policy_id`/`merchant_id` columns to `LedgerEntry` (the ledger is
a pure value-movement record; authorization metadata lives on `Checkout`). Check 10 is
already correctly scoped to `envelope_id` and is unchanged. Rationale: a policy is a
discrete human-signed authorization envelope; cross-policy cumulative sums would let one
grant silently erode another's signed cap, breaking PoAI's per-policy offline
verifiability.

### 3.3 PoAI bundle — sections, fixed order, hash chain

**§3.3.0 Section order (v3.0: nine sections — `campaign` appended after `aal`).**

```python
SECTION_ORDER = ("transaction", "human_intent", "authority", "goods",
                 "agent", "adjudication", "notification", "aal", "campaign")
```

A section that does not apply is represented as JSON `null`, never absent. `campaign` is
`null` unless a `campaign_id` applied.

**§3.3.1 transaction** — `merchant_id`, `checkout_id`, `cart_created_at`, `amount_minor`,
`currency`, `psp{provider, order_id, payment_link_id}`.

**§3.3.2 human_intent** — `request_digest`, `request_text` (verbatim retained —
DECISIONS §11.1.7; digest-only selective disclosure is designed-for v0.2, not built),
`captured_at`, `channel`, `channel_message_id`, `agent_plan{model, interpretation,
constraints_extracted, candidates_considered, plan_digest}` (optional).

**§3.3.3 authority** — `scheme`, `policy`, `policy_hash`, `webauthn{credential_id,
client_data_json, authenticator_data, signature, uv, sign_count, signed_at,
challenge_binding}`, `enrolment{public_key, aaguid, attestation_format, enrolled_at}`,
`presentation`, `delegation{chain, spend_chain, envelope_id, envelope_budget_minor,
digests, sequencer}`.

**§3.3.4 goods** — `cart_hash`, `cart_version`, `items[{sku, qty, unit_minor, tags,
catalog_attestation}]`.

**§3.3.5 agent** — `client_id`, `display_name`, `token_jti`, `scopes`,
`consent_granted_at`, `edge_identity`.

**§3.3.6 adjudication** — `compiler_version`, `compiler_digest`, `policy_schema_version`,
`evaluated_at`, `context` (the exact `CompilerContext`), `verdict`, `reason_code`,
`transcript` (list, recorded verbatim; AAL predicate e7 requires re-execution to be
byte-identical to it).

**§3.3.7 notification** — `sent_at`, `channel`, `receipt_digest`.

**§3.3.8 aal** — `level`, `predicates{e1..e9}`, `reasons`.

**§3.3.9 campaign** *(v3.0)* — `campaign_id`, `campaign_version`, `draft_digest`,
`approval{approver_credential_id, approved_at, amendment_assertion}`,
`offer_terms{discount_bps, applies_to_skus, window}`.

**§3.3.10 Top-level (outside the chain):** `poai_version`, `bundle_id`, `issued_at`,
`chain{links, root, merchant_signature, time_anchor}`.

**§3.3.11 Hash chain construction (verbatim algorithm).**
- `c_i = canonical_json_bytes(bundle[SECTION_ORDER[i]])` (`null` → `b"null"`)
- `link_0 = SHA256(c_0)`; `link_i = SHA256(link_{i-1} || c_i)` for `i = 1..8`
- `root = link_8`
- `chain.links`: exactly 9 strings `"sha256:" + hex(link_i)`, in `SECTION_ORDER`.
- `chain.root` MUST equal `links[8]`.
- `chain.merchant_signature`: ES256 JWS Compact over exactly `{bundle_id, issued_at, root}`.
- `chain.time_anchor`: the merchant anchors the **salted root** —
  `digest({"root": root, "salt": <16 random bytes b64u>})`; the salt is stored within the
  anchor object. Rekor primary, `merkle_daily` fallback (DECISIONS §11.1.1); anchoring is
  asynchronous and never blocks `checkout_confirm`. If missing, verifier reports
  `time_anchor: absent`.

### 3.4 The money-path invariants

- **INV-1 — The authorised object, the verified object, and the charged object are the
  same immutable object, identified by hash.** The compiler runs over
  `Checkout.cart_snapshot_json` (frozen at `checkout_initiate`), never over the mutable
  `Cart`. At confirm, recompute `compute_cart_hash(cart_snapshot_json)` and require
  equality with `checkout.cart_hash`, and assert `sum(unit_minor*qty) == total_minor`.
  A foreign key to a mutable row is not an immutable object.
- **INV-2 — The WebAuthn assertion is verified on the money path.** `checkout_confirm`
  calls the assertion verifier before the compiler runs — challenge lookup, signature
  verification, UV flag, sign-count. A policy token is never treated as an opaque bag from
  which a credential ID is read.
- **INV-3 — Idempotency is a contract, not a cache.** Unique per `(client_id,
  idempotency_key)`; **fingerprinted** so key reuse with a different payload is
  `422 idempotency_key_reuse_with_different_payload`; **in-flight-aware** so a concurrent
  retry gets `409 request_in_progress` (retriable, `Retry-After: 1`) instead of a second
  charge; **expiring** at 24h. Scoped to `client_id`.
- **INV-4 — Intent-first, then outbox (the dual-write rule).** Never call Razorpay before
  local effects are durable. Order: `BEGIN` write `IdempotencyRecord(IN_FLIGHT)` +
  `PspIntent(PENDING)` → `COMMIT` → call Razorpay with `reference_id = checkout_id`
  (deterministic) → `BEGIN` write `PspIntent→SUCCEEDED` + `Order` + ledger `CAPTURE` +
  `IdempotencyRecord→COMPLETED` → `COMMIT`. A crash after the first commit leaves a
  recoverable `PENDING` intent; a recovery worker adopts the existing Razorpay object by
  `reference_id` rather than creating a second. Razorpay enforces `reference_id`
  uniqueness on payment links (max 40 chars; a UUID4 `checkout_id` is 36). The exact
  duplicate-create error code string is `[verify-at-build]`: capture it once from
  test-mode, pin it as a constant, catch that code specifically, never bare `Exception`.
- **INV-5 — The spend ledger is double-entry and reverses.** `RESERVE` at confirm,
  `CAPTURE` on `payment_link.paid`, `RELEASE` on link expiry / `payment.failed` /
  hold-cancel, `REFUND` on refund. Append-only; never `UPDATE` a ledger row. Derived
  balance `available_minor` over a 24h window; invariant test asserts it never goes
  negative under any interleaving.
- **INV-5a — Ledger balance invariant (Q-002).** At every terminal order state
  (`RELEASED`, `CANCELLED`, `REFUNDED`), the escrow accounts — `customer_hold` and
  `merchant_pending` — MUST net to zero per `reference_id`. Economic accounts —
  `merchant_revenue` and `platform` — are excluded from the zero-invariant and MUST be
  non-negative. `verify_ledger_balances(reference_id)` returns `True` iff both conditions
  hold. Golden vectors `GOLDEN/ledger/{reserve_capture,reserve_release,
  reserve_capture_refund}.json` pin each lifecycle; `verify_ledger_balances` MUST
  reproduce them byte-for-byte. The naive "all accounts == 0" assertion is WRONG and MUST
  NOT be used — economic accounts are legitimately positive after CAPTURE.
- **INV-6 — Webhooks assume at-least-once, out-of-order, occasionally never.** Signature
  verify (raw body, HMAC-SHA256, `hmac.compare_digest`) → persist raw event → return 200 →
  process in a worker. Dedupe on `X-Razorpay-Event-Id` header (fallback:
  `sha256(raw_body)`). Guard transitions with an explicit allowed-transition table;
  terminal states `{PAID, REFUNDED, FAILED}` are absorbing. Return non-2xx only when you
  want a retry; after N attempts move to a dead-letter table and alert. `[verify-at-build]`
  the real webhook body shape by capturing one live — build against bytes, not docs.
- **INV-7 — Reconciliation, because webhooks get lost.** A sweeper polls Razorpay for
  orders stuck in non-terminal states (age 10m–7d) and applies the PSP's answer — the PSP
  is the source of truth for money. `reconciliation_drift_total` is the most important
  metric; a healthy system reports zero; non-zero is an alarm, not a log line.
- **INV-8 — `checkout.expires_at` is enforced.** A checkout initiated Monday does not
  confirm Friday.
- **INV-9 — Asymmetric OAuth tokens.** ES256 keypair loaded from env/file, `kid` header,
  real `GET /oauth/jwks.json` serving the public JWK, algorithm allowlist on verify
  (reject `none` and HS256). The resource server never holds signing material.
- **INV-10 — The enrolment ceremony is authenticated.** `/internal/webauthn/*` and
  `/admin/*` are behind the merchant operator session. `user_id` comes from the session,
  never the request body. A test asserts no `/internal/` or `/admin/` route resolves
  anonymously.
- **INV-11 — Spend-cap TOCTOU is closed.** `BEGIN IMMEDIATE` on the confirm transaction
  (SQLite) so concurrent reads of the budget serialise; the budget row is locked for the
  read-modify-write.
- **INV-12 — Audit attributes every call.** `AuditLogEntry.client_id` and `trace_id` are
  real on every row. One `trace_id` is generated at ingress and threaded through
  `contextvars` so the audit row, the Discord trace, the app log, and the Razorpay `notes`
  field all correlate. PII is redacted by allowlist — **excluded from logs/traces:
  delivery address; full WebAuthn assertion.**
- **INV-13 — Campaign offers are signed and time-bound (v3.0).** An offer in the feed
  carries the merchant's ES256 signature and a `[starts_at, ends_at)` window. Buyer agents
  MUST treat unsigned or out-of-window offers as non-existent. The compiler enforces
  validity (check 12) — offer text is never trusted at face value (R0.8 applied to
  marketing).
- **INV-14 — The campaign agent reads only derived analytics, never raw PII (v3.0).** Its
  input is the aggregated sales view (§9.6). The PII-redaction allowlist (INV-12) applies
  to every analytics row it consumes.

### 3.5 AAL ladder — predicates e1–e9, first-match-wins

| Predicate | True iff |
|---|---|
| **e1** | `agent.client_id`, `.scopes`, `.token_jti` present and non-empty, and `"checkout:confirm" in scopes` |
| **e2** | the WebAuthn assertion verifies (ES256) against `authority.enrolment.public_key`, and the challenge matches `challenge_binding` |
| **e3** | `adjudication.evaluated_at − webauthn.signed_at <= policy.assertion_max_age_seconds` |
| **e4** | UV bit (0x04) set in byte 32 of decoded `authenticator_data` |
| **e5** | `challenge_binding.mode == "cart"` and its `cart_hash == goods.cart_hash` |
| **e6** | every item's `catalog_attestation` verifies, matches sku/price/tags, and `iat <= cart_created_at` |
| **e7** | re-execution of `compile_decision` returns ALLOW and its transcript is byte-identical to `adjudication.transcript` |
| **e8** | `human_intent` non-null and `digest({"text":request_text}) == request_digest` |
| **e9** | `notification` non-null, has `sent_at`, well-formed `receipt_digest` |

First-match-wins resolution, evaluated in this exact order:

| Order | Condition | Level | Reason |
|---|---|---|---|
| 1 | `not e2` | **0** | `policy_signature_invalid` |
| 2 | `not e7` | **0** | `compiler_denied_or_transcript_mismatch` |
| 3 | `not e1` | **0** | `agent_unauthenticated` |
| 4 | `policy_version != 2` | **1** | `policy_schema_legacy` |
| 5 | `not e3` | **1** | `assertion_stale` |
| 6 | `not e6` | **1** | `catalog_unattested` |
| 7 | `not e4` | **1** | `user_not_verified` |
| 8 | `not (e8 and e9)` | **1** | subset of `intent_unrecorded`, `notification_missing` |
| 9 | `e5` | **3** | `()` |
| 10 | otherwise | **2** | `no_per_transaction_binding` |

AAL0: no verifiable human authority exists; **no order is created** (`policy.no_human_authority`).

**HOLD_SECONDS (server constant, human-visible — DECISIONS §11.1.5):** AAL3 = 0s,
AAL2 = 900s, AAL1 = 3600s. Hold windows MUST be displayed in Policy Studio and published
in `/.well-known/agent-policy.json` before the human signs.

**Liability strings (fixed verbatim — DECISIONS §11.1.6).** Verifier and Evidence Viewer
MUST use these, each prefixed `"Proposed liability position (not a network rule): "`:
- **AAL3:** "...the human's authenticator signed this exact cart with user verification;
  this is the strongest merchant-side evidence of authorized intent available."
- **AAL2:** "...the human authorized a standing policy with a fresh, user-verified
  signature, and this cart compiled clean against it; this is evidence of authorized
  intent, with final allocation resting with the network and issuer."
- **AAL1:** "...authority was presented but one or more freshness, attestation, or
  verification predicates failed; treat the transaction as contested."
- **AAL0:** "...no verifiable human authority exists; no order is created at this level."

### 3.6 Verifier CLI (`openstore-verify`) — exit codes and the 14 checks

**Exit codes:** `0` all pass · `1` a check failed · `2` bundle malformed/schema-invalid ·
`3` `unsupported_compiler_digest` · `4` usage error. `--json` emits `merchant_asserted`
(`spent_minor`, `transactions_count`, `agent_plan`). Omitting `--merchant-jwks` reports
merchant signature as `unverified_no_jwks`. `--merchant-jwks` accepts a **directory** of
per-merchant JWKS files and selects the key by `kid` (DECISIONS §11.1.10).
`--detect-forks <dir>` emits `fork_proof`. The verifier MUST run fully offline — no
network access during verification.

**The 14 checks:** (1) `schema`; (2) `chain_integrity`; (3) `merchant_signature` — ES256
JWS Compact over exactly `{bundle_id, issued_at, root}`; (4) `time_anchor`; (5)
`webauthn_assertion` — verifies (ES256) against `authority.enrolment.public_key`; (6)
`challenge_binding` — challenge matches; (7) `uv_flag` — UV bit (0x04) set in byte 32 of
decoded `authenticator_data`; (8) `catalog_attestations` — every item's attestation
verifies, matches sku/price/tags, `iat <= cart_created_at`; (9) `compiler_digest`; (10)
`re_execution` — re-run `compile_decision` against `adjudication.context`, returns ALLOW,
transcript byte-identical to `adjudication.transcript`; (11) `amount_consistency` —
`transaction.amount_minor == sum(unit_minor*qty)` over `goods.items`; (12) `aal` — tier
resolution over e1–e9; (13) `delegation_chain` — link 0 is the human's WebAuthn-signed
root, each later link ES256-signed by the previous delegate, attenuation is a meet
(greatest lower bound) of constraints; (14) `spend_chain` — one hash chain per envelope,
every budget-moving event appends a link (sequence contiguous from 0, no gaps), `SPEND`
countersigned by the merchant, `DELEGATE`/`RELEASE` by the envelope holder.

### 3.7 Checkout object and state enums

```python
class Checkout(SQLModel, table=True):
    checkout_id: str          # unique, indexed UUID4 string (36 chars; fits reference_id max 40)
    cart_id: int
    client_id: str            # indexed
    status: str               # indexed; governed by the checkout state machine
    cart_hash: str            # digest over the frozen snapshot
    cart_snapshot_json: list[dict]  # JSON column; never re-reads the mutable Cart
    cart_version: int
    total_minor: int
    delivery_address: str
    required_aal: int
    expires_at: datetime
    created_at: datetime
```

**Checkout states (closed):** `PENDING`, `POLICY_VERIFIED`, `ORDER_CREATED`, `REJECTED`.
**Order states (closed):** `CREATED`, `HELD`, `RELEASED`, `CANCELLED`, `PAID`, `FAILED`,
`REFUNDED`.

**Hold & Cancel state machine.** States `ORDER_CREATED`, `HELD`, `CANCELLED`, `RELEASED`.
Allowed transitions exactly `{(HELD,CANCELLED), (HELD,RELEASED)}`, plus
`ORDER_CREATED ──aal∈{1,2}──► HELD` and `aal==3 ──► RELEASED` (hold of 0s). Fulfilment
keys off `RELEASED`, never `ORDER_CREATED`. Hold release worker runs every 30s.
**Cancel token:** a 32-byte `secrets.token_urlsafe` capability, delivered only in the
customer's notification, single-use, expiring with the hold. `POST
/hold/{cancel_token}/cancel` is unauthenticated by design — the token IS the capability;
do not "improve" it with a login. Cancellation (DECISIONS §11.1.2): attempt
`POST /payment_links/{id}/cancel` → on the already-paid HTTP 400, issue an idempotent
refund and write a `REFUND` ledger entry instead of `RELEASE`.

### 3.8 Catalog item and attestation

**Catalog item:** `sku: str` · `name: str` · `unit_minor: int` · `tags: list[str]` ·
`related_skus: list[str]` · `description: str`. For canonicalization and compiler checks,
`tags` are sorted ascending; all money uses `unit_minor`.

**Catalog attestation** — ES256 JWS Compact, signed by the merchant. Payload:
```json
{
  "sku": "GEL-VAN-500",
  "price_minor": 21000,
  "tags": ["dairy-free", "vegan"],
  "catalog_digest": "sha256:...",
  "merchant_id": "gelateria-roma",
  "iat": 1787000000
}
```
`tags` sorted ascending; `catalog_digest` is a `sha256:` digest of the full normalized
catalog; `iat` is integer Unix seconds. **Freshness rule:** an attestation is valid only
if `attestation.iat <= cart.created_at`. Stored in `CatalogAttestation` (`jws_compact`,
`issued_at`).

### 3.9 Well-known manifest — `/.well-known/agent-commerce.json` (version 0.2)

```json
{
  "version": "0.2",
  "merchant": { "name": "Gelateria Roma", "id": "gelateria-roma" },
  "storefront": "https://<host>/",
  "catalog_endpoint": "https://<host>/agent/catalog",
  "mcp_endpoint": "https://<host>/agent/mcp",
  "a2a_agent_card": "https://<host>/.well-known/agent-card.json",
  "auth": {
    "type": "oauth2",
    "authorization_server": "https://<host>/.well-known/oauth-authorization-server",
    "scopes_supported": ["catalog:read","cart:write","checkout:initiate","checkout:confirm"]
  },
  "policy": {
    "currency": "INR",
    "max_unconfirmed_spend_minor": 0,
    "requires_human_approval": true,
    "default_per_tx_cap_minor": 50000
  },
  "protocols": [
    {"name":"mcp","version":"2025-06-18","endpoint":"/agent/mcp",
     "auth":["oauth2_bearer"],"authority_schemes":["native_webauthn"],
     "spec_excerpt":"/protocols/mcp/spec-excerpt"},
    {"name":"acp","version":"[verify-at-build]","endpoint":"/agent/acp",
     "auth":["oauth2_bearer","http_message_signature"],
     "authority_schemes":["acp_delegated_token","native_webauthn"],
     "spec_excerpt":"/protocols/acp/spec-excerpt"}
  ],
  "evidence": {
    "poai_version": "0.1",
    "bundle_endpoint": "/orders/{checkout_id}/evidence",
    "jwks": "/.well-known/poai-jwks.json"
  },
  "campaigns": {
    "feed_endpoint": "/agent/campaigns",
    "signed_feed": "/.well-known/agent-campaigns.json"
  }
}
```

The `protocols[]` array is generated from the adapter registry at startup and gated on a
`conformance_pass` — never hand-written. Flat endpoint fields are retained for backward
compatibility; `protocols[]` supersedes them. The `campaigns` block is v3.0-new.

### 3.10 OAuth 2.1 closed sets

**Scopes (exhaustive):** `catalog:read`, `cart:write`, `checkout:initiate`,
`checkout:confirm`.
**Token claims:** `access`, `refresh`, `jti`, `scope`, `exp`, `revoked`, `kid` (header,
per INV-9). OAuth 2.1 + PKCE; asymmetric tokens (INV-9).

---

## Part 4 — Technology stack (pinned)

| Concern | Choice | Pin rule |
|---|---|---|
| Language | Python 3.12 | `requires-python = ">=3.12,<3.13"` |
| Package/venv manager | **uv** | `uv.lock` committed; `uv sync --locked` in CI; `uv pip` forbidden |
| Web framework | FastAPI + SQLModel + Alembic | versions pinned in `uv.lock`; Alembic adopted at Stage 0 — `create_all` permitted in tests only (DECISIONS §11.1.13) |
| DB (demo) | SQLite (`BEGIN IMMEDIATE`, INV-11) | production exit = Postgres (documented, not built) |
| Payments | Razorpay **test mode**, `razorpay-python` SDK | error strings `[verify-at-build]` |
| Discord bot | **discord.py** | pinned; bot token in `.env` |
| WebAuthn | `py_webauthn` | accept ES256 (−7) and RS256 (−257) only; EdDSA (−8) out of scope (DECISIONS §11.1.4); rejection: `webauthn_unsupported_alg` |
| Signing | ES256 (merchant bundle key) | per-merchant keypairs, `kid = "{merchant_id}-key-{n}"`; each instance's `/.well-known/poai-jwks.json` serves only that merchant's keys (DECISIONS §11.1.10) |
| Time anchor | Sigstore Rekor (99.5% SLO), `merkle_daily` fallback | asynchronous; never blocks `checkout_confirm` (DECISIONS §11.1.1) |
| OAuth | 2.1 + PKCE, asymmetric tokens | INV-9 |
| Operator auth | single shared operator password (env var, Argon2-hashed session) | session MUST carry `user_id` internally from day one (DECISIONS §11.1.8) |
| LLM (agents) | one provider behind `openstore/agents/llm.py` | model name = config key `llm.model` (registry-pinned default) |
| Frontend | **lean, single-file HTML** per surface (storefront, Policy Studio, Campaign Studio, Evidence Viewer) | no SPA framework, no npm, no build step; vanilla JS + fetch; all money rendering server-side; Evidence Viewer is zero-dependency and self-contained — WebCrypto verifies ECDSA P-256 and RSASSA-PKCS1-v1_5 natively |
| Local tunneling (demo) | `cloudflared` | for webhook receipt during development |
| Evidence retention | config key `evidence_retention_days` (default 540) | nightly worker deletes older `EvidenceBundle` rows, writes `RETENTION_PURGE` audit entry per deletion; retention advertised in `/agents` MUST equal the configured value (DECISIONS §11.1.9) |
| Envelope TTL | `envelope_ttl_seconds = 14400` (4h), merchant-configurable | DECISIONS §11.1.14 |
| Observability | hand-rolled Prometheus text exposition on `/internal/metrics` | no new dependency (Q-011); gauges: health_ready, checkout_hold_state, ledger_balance_minor, reconciliation_drift_total |
| Readiness/migrations | `core/health.py` + Alembic `apply_migrations()` | SID-2/SID-3; schema-ready flag prevents double-migration |

**Razorpay pinned constants:** `reference_id = checkout_id` (deterministic; max 40 chars,
UUID4 is 36). Webhook events handled: `payment_link.paid`, `payment_link.cancelled`,
`payment_link.partially_paid`; `payment.failed` triggers ledger RELEASE. Reconciliation
sweeper: non-terminal orders aged 10m–7d. Hold release worker: every 30s. Cancel path per
DECISIONS §11.1.2. Refunds are idempotent requests. All webhook body shapes and error
strings are `[verify-at-build]` — capture live via `scripts/capture_constants.py`, pin as
constants with source comments.

**Frontend constraint (binding):** each surface is ONE self-contained `.html` file served
by FastAPI. The demo's credibility rests on the protocol, not the chrome.

---

## Part 5 — The agentic surface

All agents obey R0.9 and R0.10 and live under `openstore/agents/` behind the import
firewall. Five agent capabilities are normative.

### 5.1 Buyer agent — planning loop
A discord.py bot that is an MCP client. Loop: parse goal → `search_products` → assemble a
policy-aware cart (`create_cart`/`update_cart`) → `checkout_initiate` → `checkout_confirm`
→ monitor hold window → report. Every money action goes through the compiler. It NEVER
sees Razorpay credentials; payment links return out-of-band to the human.

### 5.2 Negotiation loop (buyer ↔ merchant, over A2A)
When a cart is DENIED with a recoverable reason code (`policy.tag_violation`,
`policy.sku_blocked`, `policy.spend_per_tx_exceeded` with in-policy headroom), the merchant
agent and buyer agent exchange structured counter-offers (§7.5) until a cart compiles or
both emit `no_compliant_path`. The transcript is stored and embedded in
`human_intent.agent_plan` of any resulting bundle.

### 5.3 Policy amendment ceremony (agentic centerpiece)
When negotiation reaches `no_compliant_path`, the merchant agent MAY draft a **signed
policy amendment** (§7.4): e.g. "allow SKU `pistachio`, one-time, ≤₹300". Routed to the
human for one-tap WebAuthn approval. On approval the policy's version increments, the cart
recompiles → ALLOW. The amendment assertion is recorded in PoAI §authority. A denied
amendment is terminal for that cart: fail loud, no retry (R0.5).

### 5.4 Evidence narrator
At dispute time, an agent writes the human-readable cover note for the PoAI bundle. The
LLM writes prose ONLY; the bundle remains signed machine output. The narrator's text is
stored alongside, never inside, the signed bundle.

### 5.5 Campaign / Offer Orchestrator
See Part 9 (normative).

---

## Part 6 — MCP tools and routes (closed sets)

**14 MCP tools:** `search_products`, `get_product`, `create_cart`, `update_cart`,
`checkout_initiate`, `checkout_confirm`, `get_order`, `get_audit_log`,
`webauthn_register_begin`, `webauthn_register_complete`, `webauthn_begin_assertion`,
`webauthn_complete_assertion`, `list_campaigns`, `get_campaign`.

**Routes:** `/.well-known/agent-commerce.json`, `/.well-known/agent-policy.json`,
`/.well-known/agent-card.json`, `/.well-known/oauth-authorization-server`,
`/.well-known/poai-jwks.json`, `/.well-known/agent-campaigns.json`, `/agent/catalog`,
`/agent/mcp`, `/agent/acp`, `/agent/campaigns`, `/protocols/<name>/spec-excerpt`,
`/intent/studio`, `/campaign/studio`, `/campaign/{campaign_id}/approve`,
`/campaign/{campaign_id}/reject`, `/admin/agents`, `/hold/{cancel_token}/cancel`,
`/orders/{checkout_id}/evidence`, `/orders/intent/{intent_id}/evidence`, `/oauth/jwks.json`, `/internal/webauthn/*`,
`/internal/policy/blast-radius`, `/admin/*`.
**SID routes (Q-010):** `/health/live`, `/health/ready`, `/internal/metrics` —
liveness, readiness (SID-2), and Prometheus text metrics (SID-7). Registered in
REGISTRY.json; `/health/live` is also served as `/healthz` for legacy probes.

---

## Part 7 — REGISTRY.json (executable closed sets)

Stage 1 generates `REGISTRY.json`; Stage 2 wires `test_registry_compliance.py` to enforce
it in both directions. Content:

```json
{
  "reason_codes": ["assertion_required","policy.currency_mismatch","policy.merchant_mismatch",
    "policy.policy_not_yet_valid","policy.policy_expired","policy.tx_count_exceeded",
    "policy.qty_invalid","policy.sku_duplicate","policy.sku_blocked","policy.tag_violation",
    "policy.spend_per_tx_exceeded","policy.spend_envelope_exceeded",
    "policy.spend_cumulative_exceeded","policy.no_human_authority",
    "policy.aggregate_cap_exceeded","policy.campaign_inactive","policy.campaign_outside_window",
    "idempotency_key_reuse_with_different_payload","webauthn_unsupported_alg",
    "unsupported_compiler_digest","request_in_progress"],
  "error_namespaces": ["auth.*","policy.*","checkout.*","psp.*","ratelimit.*","agent.*",
    "hold.*","authority.*","orchestration.*"],
  "authority_reason_codes": ["authority.unknown_scheme","authority.scheme_capped_native_webauthn",
    "authority.scheme_capped_ap2_intent_mandate","authority.scheme_capped_ap2_cart_mandate",
    "authority.scheme_capped_acp_delegated_token","authority.scheme_capped_none"],
  "mcp_tools": ["search_products","get_product","create_cart","update_cart",
    "checkout_initiate","checkout_confirm","get_order","get_audit_log",
    "webauthn_register_begin","webauthn_register_complete","webauthn_begin_assertion",
    "webauthn_complete_assertion","list_campaigns","get_campaign"],
  "oauth_scopes": ["catalog:read","cart:write","checkout:initiate","checkout:confirm"],
  "aal_levels": [0,1,2,3],
  "checkout_states": ["PENDING","POLICY_VERIFIED","ORDER_CREATED","REJECTED"],
  "order_states": ["CREATED","HELD","RELEASED","CANCELLED","PAID","FAILED","REFUNDED"],
  "campaign_states": ["DRAFT","PENDING_APPROVAL","ACTIVE","PAUSED","EXPIRED","REJECTED"],
  "ledger_entries": ["RESERVE","CAPTURE","RELEASE","REFUND"],
  "ledger_accounts": {
    "escrow": ["customer_hold","merchant_pending"],
    "economic": ["merchant_revenue","platform"]
  },
  "verifier_exit_codes": [0,1,2,3,4],
  "discord_channels": ["#buyer-trace","#merchant-trace","#money-trace","#alerts"],
  "negotiation_states": ["PROPOSED","COUNTERED","ACCEPTED","NO_COMPLIANT_PATH","AMENDMENT_REQUESTED"],
  "enums_are_exhaustive": true
}
```

*(The full route list from Part 6 is also encoded under `"routes"`.)*

**§7.4 PolicyAmendment schema:** `amendment_id`, `base_policy_hash`,
`delta{add_allowed_skus?, add_allowed_tags?, bump_max_spend_per_tx_minor?, one_time:bool,
max_amount_minor?}`, `reason_code_triggered`, `drafted_by:"merchant_agent"`,
`draft_digest`, `approval{approver_credential_id, approved_at, webauthn_assertion}`.

**§7.5 Negotiation message schema:** `negotiation_id`, `round:int`,
`from:"buyer_agent"|"merchant_agent"`, `state` (negotiation_states), `cart_delta`,
`reason_code`, `trace_id`.

**§7.6 Discord channels:** exactly four — `#buyer-trace`, `#merchant-trace`,
`#money-trace`, `#alerts`. `#money-trace` is append-only and receives every
RESERVE/CAPTURE/RELEASE/REFUND with `trace_id`. Every rejection emits a trace with its
reason code; the `checkout_confirm` ladder and hold state machine transitions are traced.

---

## Part 8 — The 14 invariants

See §3.4 — INV-1…INV-12 plus INV-5a (Q-002) and INV-13/INV-14 (v3.0). They are normative
there and not repeated here.

---

## Part 9 — Campaign / Offer Orchestrator (normative)

### 9.1 Purpose
Grow merchant revenue by drafting data-driven campaigns from prior sales, calendar dates,
and catalog state; route each draft to the merchant for one-tap signed approval; publish
approved campaigns to the agent-readable feed so buyer agents discover offers and re-plan
around them.

### 9.2 Pipeline (five stages, all gated)
1. **Ingest** — the derived sales-analytics view (§9.6), the calendar (holidays, paydays,
   seasonality), current catalog + tags, current policy headroom.
2. **Draft** — the LLM proposes a `Campaign` (§9.3). LLM output is a DRAFT only (R0.9).
3. **Validate** — deterministic validator (no LLM): every SKU exists; `discount_bps`
   within `[campaign_min_bps, campaign_max_bps]` (config, registry-pinned); window
   well-formed; no SKU is on any active policy's `blocked_skus` unless the campaign is
   explicitly excluded from those policies; projected price stays in integer paise.
4. **Approve** — merchant reviews in Campaign Studio; one-tap WebAuthn approval signs the
   campaign → `ACTIVE`. Rejection → `REJECTED`, fail loud, logged to `#merchant-trace`.
5. **Publish** — signed campaign appended to `/.well-known/agent-campaigns.json` and
   `/agent/campaigns`, and to catalog item `offers[]`. Buyer agents discover via
   `list_campaigns` / `get_campaign`.

### 9.3 Campaign schema (closed)
`campaign_id`, `campaign_version`, `merchant_id`, `title`, `rationale`,
`offer_terms{discount_bps:int, applies_to_skus:list[str], starts_at, ends_at}`,
`source_signals{top_skus, slow_skus, calendar_event?, headroom_minor}`, `draft_digest`,
`approval{approver_credential_id, approved_at, webauthn_assertion}`, `state`
(campaign_states), `merchant_signature`.

### 9.4 Lifecycle
`DRAFT → PENDING_APPROVAL → ACTIVE → (PAUSED | EXPIRED)`; `PENDING_APPROVAL → REJECTED`.
`EXPIRED` is set by the sweeper when `now >= ends_at`. Only `ACTIVE` campaigns are in the
feed. All transitions logged with `trace_id` to `#merchant-trace`.

### 9.5 Compiler integration
A cart line referencing `campaign_id` triggers check 12 (`campaign_validity`, §3.2b). The
applied campaign is recorded in PoAI §3.3.9 (`campaign`).

### 9.6 Analytics source (privacy boundary, INV-14)
The agent reads a **derived, aggregated** view only: `sku`, `units_sold_7d`,
`units_sold_30d`, `gross_minor_30d`, `attach_rate`, `last_sold_at`. It NEVER reads raw
orders, buyer identities, or payment data. The view is produced by the core (deterministic
SQL), not by the agent.

### 9.7 Guardrails
`campaign_min_bps`/`campaign_max_bps` cap the discount. Max `campaign_max_active`
concurrent ACTIVE campaigns (registry-pinned). The orchestrator CANNOT publish without a
WebAuthn approval. No prompt-injection copy: `title`/`rationale` pass the same content
rules as catalog strings. Fail loud on any validation breach.

---

## Part 10 — Build plan (10 stages; each ends in an executable gate)

Each stage contract lives in `SPECS/stage-NN-*.md`, is ≤ ~4k tokens, self-contained ("you
do not need the other stage files"), lists exhaustively the files it may create/modify
(SCOPE), names its out-of-scope temptations (MUST NOT), and ends in a **DONE WHEN** block
of copy-pasteable commands that must all exit 0.

| # | Stage | Commit gate |
|---|---|---|
| 0 | **STEP ZERO** — AGENTS.md (§0.4), OPEN_QUESTIONS.md, repo skeleton, `uv init`, Alembic adopted | `chore(agents): implementation contract` |
| 1 | Package skeleton: config loader, `openstore init/serve` CLI, REGISTRY.json | `stage(01): skeleton` |
| 2 | Money-path core: INV-1…12 + INV-5a as library code; `test_registry_compliance.py` live; ledger golden vectors | `stage(02): money-core` |
| 3 | Intent Compiler (check 0 + 1–12) + WebAuthn RP + Policy Studio; compiler golden vectors incl. `assertion_required` and two-policy cumulative-scoping vector | `stage(03): compiler-webauthn` |
| 4 | PoAI evidence layer: 9-section bundle, AAL, `openstore-verify` + golden corpus | `stage(04): poai` |
| 5 | Razorpay driver: payment links, webhooks, sweeper, hold/cancel; `capture_constants.py` run | `stage(05): razorpay` |
| 6 | Agent surfaces: MCP (14 tools), well-knowns (6), catalog + campaign feed | `stage(06): surfaces` |
| 7 | **Agents**: buyer agent, negotiation loop, amendment ceremony, narrator | `stage(07): agents` |
| 8 | **Campaign Orchestrator** (Part 9) + Campaign Studio | `stage(08): campaigns` |
| 9 | Demo store #1 (gelateria) + end-to-end run through the public install surface | `stage(09): demo-1` |
| 10 | Demo store #2 (chai), red-team, sentinel suite, feature freeze | `stage(10): freeze` |

**Cut from MVP (documented decisions, not omissions):** multi-merchant delegation chains
(admitted unsolved offline double-spend — post-freeze); synthetic buyer swarm as a feature
(red-team suite retained); AXO optimizer loop; UAP adapter (NPCI UAP is pilot-stage with
no public spec to pin constants against — watchlist only).

---

## Part 11 — DECISIONS.md

### 11.1 The fourteen v2.1 resolutions (carried verbatim, verified 2026-08-30)

1. **Rekor anchoring — Rekor primary, `merkle_daily` fallback.** Public Rekor is GA (1.0),
   99.5% availability SLO, on-call monitored. Anchoring stays asynchronous, so the SLO
   never touches the money path: anchor `{"type":"rekor"}` when reachable, degrade to
   `merkle_daily` on failure, never block `checkout_confirm`.
2. **Payment-link cancellation — cancel-if-unpaid, refund-if-paid.** Razorpay exposes
   `POST /payment_links/{id}/cancel`; cancelling a paid/partially-paid link returns HTTP
   400. So: attempt cancel → on the already-paid 400, issue a normal idempotent refund and
   write `REFUND` instead of `RELEASE`.
3. **`reference_id` uniqueness — enforced; deterministic idempotency is available.**
   `reference_id` must be unique per Payment Link (max 40 chars; UUID4 `checkout_id` is
   36). On duplicate-create, recover via fetch-all filtered by `reference_id` and adopt
   the existing link. Exact duplicate-create error code string is `[verify-at-build]`.
4. **WebAuthn algorithms — accept ES256 (−7) and RS256 (−257).** ES256 covers CTAP1/CTAP2
   platform authenticators; RS256 covers Windows Hello TPM-class hardware. Both verify
   natively in browser WebCrypto, preserving the zero-dependency Evidence Viewer. EdDSA
   (−8) remains out of scope.
5. **`hold_seconds` — server constant, human-visible.** Hold windows stay a module
   constant but MUST be displayed in Policy Studio and published in
   `/.well-known/agent-policy.json` before the human signs.
6. **AAL liability wording — exact strings.** Fixed verbatim in §3.5, each under the
   prefix `"Proposed liability position (not a network rule): "`.
7. **`human_intent.request_text` — verbatim stays.** The demo's bundles are self-owned
   data, and offline re-verification of predicate e8 requires the text. Digest-only
   selective disclosure remains designed-for v0.2, not built.
8. **Operator authentication — single shared operator password** (env var, Argon2-hashed
   session). Session layer MUST carry a `user_id` internally from day one so the upgrade
   to per-user auth is additive.
9. **Evidence retention — enforced by a nightly worker.** Config key
   `evidence_retention_days` (default 540). Worker deletes older `EvidenceBundle` rows and
   writes a `RETENTION_PURGE` audit entry per deletion. The retention advertised in
   `/agents` MUST equal the configured value.
10. **Second merchant keying — per-merchant keypairs, namespaced `kid`s.** Each merchant
    instance generates its own ES256 PoAI keypair; `kid = "{merchant_id}-key-{n}"`. Each
    instance's `/.well-known/poai-jwks.json` serves only that merchant's keys. The
    verifier's `--merchant-jwks` accepts a directory of per-merchant JWKS files and
    selects by `kid`.
11. **AP2 sub-delegation — not in AP2's vocabulary; delegation stays OpenStore-native.**
    AP2 defines three mandate types (Intent, Cart, Payment); there is no first-class
    sub-delegation primitive with budget arithmetic. Delegation links, envelopes, and
    spend chains remain native inside `authority.delegation`; the AP2 adapter maps only
    the root intent onto an Intent Mandate.
12. **Cross-root netting — closed with a per-user aggregate cap.** Merchant config
    `per_user_aggregate_cap_minor` (demo default 500000), enforced at policy signing and
    re-checked at `checkout_confirm` (§3.2a). Keyed on enrolled `user_id`.
13. **Alembic — adopted at Stage 0.** `create_all` permitted in tests only.
14. **Envelope TTL default — `envelope_ttl_seconds = 14400` (4 hours),**
    merchant-configurable.

### 11.2 v3.0 decisions
Sidecar-over-plugin (credential absence, INV-2, R0.8) · PoAI moved to stage 4 (it is the
product differentiator) · demo stores are acceptance tests, not the deliverable · the four
Discord channel names (§7.6) newly defined · campaign check appended as #12 to preserve
transcript/golden validity · UAP watchlisted pending a public NPCI spec.

### 11.3 Q-001…Q-004 resolutions (2026-08-30)
- **Q-001 (structure/stack):** closed — structure normative in §1.3, stack in Part 4,
  first action STEP ZERO (§0.3). No artifact change.
- **Q-002 (ledger balance invariant):** option (c) + golden vectors — escrow accounts
  (`customer_hold`, `merchant_pending`) net to zero at every terminal state; economic
  accounts (`merchant_revenue`, `platform`) non-negative, excluded from the zero-invariant.
  Normative as INV-5a (§3.4); taxonomy in REGISTRY.json `ledger_accounts`; three vectors
  in `GOLDEN/ledger/`.
- **Q-003 (cumulative spend scope):** option (b) — `max_spend_total_minor` is PER-POLICY;
  scope via `reference_id → Checkout.policy_id` join; no new `LedgerEntry` columns.
  Normative as §3.2c; two-policy golden compiler vector pinned at Stage 3.
- **Q-004 (assertion-required code):** option (a) — reason code `assertion_required` added
  to REGISTRY.json; new precondition check 0 `human_authority_present` (§3.2);
  `compile_decision()` returns (never raises); the `ValueError` on the missing-assertion
  path is deleted. Golden vector pinned at Stage 3: authority-required policy + no
  assertion → `assertion_required`.

---

## Part 12 — MVP demo script (the proof)

"Agent buys a gelato. Agents negotiate. Merchant wins the dispute. Nobody wrote store code."

0. **Install (30s live):** `pip install openstore[razorpay] && openstore init && openstore
   serve gelateria.yaml` → manifests + catalog + campaign feed live.
1. **Authorize once:** human signs IntentPolicy (₹2,000/mo, ≤₹500/txn, Gelateria, vegan).
2. **Agent shops:** Discord bot → `search_products` → cart → `checkout_initiate` →
   checks pass → ALLOW, AAL2.
3. **Bounded money:** `checkout_confirm` → dual-write → Razorpay test link → pay →
   webhook → `HELD` 15-min → cancel link → `RELEASED` → ledger RESERVE→CAPTURE.
4. **The agentic failure:** bot requests non-vegan pistachio → DENY `policy.tag_violation`
   → A2A **negotiation** (visible counter-offers) → `no_compliant_path` → merchant agent
   drafts a **signed policy amendment** → human one-tap WebAuthn → policy v2 → cart
   recompiles → **ALLOW** → sale closes. Then ₹600 over-cap → DENY
   `policy.spend_per_tx_exceeded`, no negotiation possible, fail loud.
5. **Campaign beat:** orchestrator reads analytics, drafts "weekend vegan bundle −10%",
   merchant approves in Campaign Studio, signed offer lands in the feed, the buyer agent's
   planner discovers it via `list_campaigns` and re-plans the next cart around it.
6. **The dispute:** export PoAI bundle → `openstore-verify` on a wifi-off laptop → 14
   checks → VERDICT AAL2 → tamper one digit in `amount_minor` → verifier names the exact
   broken hash-chain link and section. Evidence narrator generates the arbitrator cover
   note live.
7. **Second merchant:** `openstore serve chai.yaml` on a second port — multi-tenancy
   proven by install, not assertion.

### Track-bar compliance
Explainable → closed-enum reason codes + offline-verifiable PoAI. Bounded → human-signed
caps, deterministic compiler, TOCTOU-locked. Gated → AAL ladder, fulfillment keys off
RELEASED. Audit trail → hash-chained, Rekor-anchored, PII-redacted, `#money-trace`.
Failure handled gracefully → recoverable (`policy.tag_violation`→negotiate/amend→converted
sale) and non-recoverable (`policy.spend_per_tx_exceeded`→clean refusal) both shown.
Growth → recovery, headroom bundler, **and** the Campaign Orchestrator — all policy-gated,
never dark-pattern.

---

## Appendix A — Merge notes (v2.1 + v3.0-draft + Q-001…Q-004 → this document)

1. **Fully self-contained.** All v2.1 normative detail is inlined: the full INV-1–12 text,
   AAL e1–e9 + first-match-wins table + liability strings, the hash-chain algorithm
   (updated to **9 links** — `root = link_8`, since `campaign` was appended), the
   verifier's 14 checks, IntentPolicy/Checkout/catalog/manifest schemas verbatim, OAuth
   closed sets, hold/cancel machine, Razorpay constants, and all fourteen §11.1 decisions.
2. **Q-resolutions are normative, not footnotes.** `assertion_required` is in the
   REGISTRY.json reason codes; check 0 is in §3.2; INV-5a is in §3.4; check-11 per-policy
   scoping is §3.2c; Q-001 is closed by §0.3; REGISTRY.json gained `ledger_accounts`.
   DECISIONS §11.3 records them for traceability.
3. **Consequence of the 9th PoAI section:** any v2.1 hash-chain vectors are invalid — but
   golden vectors are generated fresh at Stages 2–4, so nothing is lost. Reason codes in
   REGISTRY.json carry their `policy.*` namespace prefixes to match the §7 error-envelope
   convention exactly.
4. **Demo script reason codes are namespaced** to match (e.g. `policy.tag_violation`).

This document replaces both prior files. The first build action remains STEP ZERO (§0.3).
