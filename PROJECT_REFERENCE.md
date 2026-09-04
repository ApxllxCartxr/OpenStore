# OpenStore — Project Reference

**Purpose of this document:** a single source of truth for what the OpenStore codebase *currently* contains — every module, class, and function, with behavior verified by directly reading the source (not by reading existing docs, READMEs, or specs). Every claim below is traceable to code that was actually read. Where the code disagrees with a comment/docstring, or where two pieces of code disagree with each other, that is called out explicitly rather than papered over.

Generated 2026-09-04 against the `main` branch working tree (commit `cf7ca0a`). Line counts and behavior reflect the state of the tree at that time — re-verify against `git log`/the source before relying on specifics that may have since changed.

## Contents

1. [Architecture overview](#1-architecture-overview)
2. [Config & data model](#2-config--data-model) — `config.py`, `models.py`, `core/database.py`, `core/idempotency.py`, `core/audit.py`, `core/health.py`, `core/__init__.py`
3. [Money & policy engine](#3-money--policy-engine) — `core/ledger.py`, `core/holdcancel.py`, `core/compiler.py`, `core/policy_signing.py`, `core/api.py`
4. [Identity & proof](#4-identity--proof) — `core/webauthn_rp.py`, `core/poai.py`, `core/oauth.py`, `devtools/virtual_authenticator.py`
5. [Payments, webhooks, campaigns, notifications](#5-payments-webhooks-campaigns-notifications) — `psp/router.py`, `psp/razorpay_driver.py`, `psp/__init__.py`, `core/webhooks.py`, `core/campaigns.py`, `notifier.py`
6. [Surfaces & offline verifier](#6-surfaces--offline-verifier) — `surfaces/catalog.py`, `surfaces/evidence.py`, `surfaces/mcp_server.py`, `surfaces/wellknown.py`, `surfaces/studio.py`, `verify/checks.py`, `verify/cli.py`
7. [Agents, CLI, server](#7-agents-cli-server) — `agents/*`, `cli.py`, `server.py`
8. [Cross-cutting findings](#8-cross-cutting-findings) — inconsistencies, duplicated logic, and gaps that span multiple modules

---

## 1. Architecture overview

OpenStore is a per-merchant "sidecar" FastAPI service (`openstore serve <config.yaml>`) that lets AI shopping agents browse a catalog, build a cart, get it adjudicated against a merchant-signed spend policy, and pay via Razorpay — with WebAuthn passkeys standing in for human authorization and a Proof-of-AI-Intent (PoAI) evidence bundle produced for each completed order.

Package layout (`src/openstore/`):

- **`core/`** — the "deterministic spine" (`core/__init__.py`'s own docstring: "NO LLM imports permitted"). Owns the DB engine, the double-entry ledger, the hold/cancel/AAL state machine, the 12-check intent compiler, policy signing, WebAuthn RP logic, PoAI bundle construction/verification, OAuth 2.1, webhook dedup, campaign validation, audit logging, and readiness/health.
- **`psp/`** — Razorpay integration: payment links, webhook receipt/dispatch, refunds, reconciliation sweeper, hold-release worker.
- **`surfaces/`** — the HTTP-facing adapters: product catalog feed, MCP tool server (14 tools), the "Policy Studio" operator UI, `.well-known` manifests, and the evidence-bundle viewer.
- **`agents/`** — the buyer-side and merchant-side reasoning: a Discord bot (`BuyerBot`/`BuyerAgent`), a rule-based `MerchantAgent` (negotiation, amendment drafting, evidence narration), an LLM-backed `CampaignAgent`, and an in-process MCP client.
- **`verify/`** — the standalone `openstore-verify` CLI: 14 fully-offline checks against a PoAI evidence bundle JSON file.
- **`devtools/`** — a deterministic virtual WebAuthn authenticator for tests/fixtures.
- **`server.py`** — the FastAPI app factory: lifespan (DB, Discord client, background workers), CORS/origin checks, readiness-gating middleware, `.well-known` routes, `/agent/*` routes, campaign approve/reject, and mounts the `psp`, `studio`, and `evidence` routers.
- **`cli.py`** — Typer CLI: `openstore init` (scaffold a merchant config) and `openstore serve` (run it).

Money flow in one line: **catalog search → cart built → `create_checkout` (compiler evaluates against signed `IntentPolicy`, AAL computed, funds RESERVEd) → Razorpay payment link created → webhook/reconciliation moves the checkout to RELEASED (CAPTURE ledger entry) → PoAI evidence bundle built for chat-originated orders.**

---

## 2. Config & data model

### `src/openstore/config.py`

Loads and validates YAML+env configuration into a typed, `extra="forbid"` pydantic-settings `Settings` object.

**Module constant:** `_ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")` — matches a whole string of the exact form `${VAR_NAME}`.

**Functions**
- `_interpolate_env(value)` — recursively resolves `${VAR}` strings in dicts/lists/strings via `os.environ`; raises `ValueError` if the env var is undefined (no silent blank).
- `merchant_id(config) -> str` — `config.merchant.name.lower().replace(" ", "-").replace("'", "")`. The single canonical merchant slug, used both when Studio signs an `IntentPolicy` and when a buyer agent calls `create_cart`. Comment: a prior bug had four separately-duplicated implementations, one of which forgot to strip the apostrophe, causing `policy_not_found` for merchants like "Joe's Gelato". Used across ~48 files.
- `load_config(config_path) -> Settings` — `Settings.from_yaml(config_path)`, then **overwrites** `settings.catalog_path` to `<config dir>/catalog.yaml` regardless of what the YAML says (catalog.yaml must always sit next to the config file).

**Classes:** `MerchantConfig`, `RazorpayConfig`, `DiscordConfig`, `WebAuthnConfig`, `DatabaseConfig` (default `sqlite:///openstore.db`), `LLMSettings` (default model `gpt-4o-mini`), `CampaignSettings` (`min_bps=500, max_bps=3000, max_active=5`), and `Settings(BaseSettings)` — `extra="forbid"`, `env_nested_delimiter="__"`; `evidence_retention_days: int = 540`; `public_base_url: str | None = None` (None ⇒ same-origin reverse proxy; subdomain deployments must set it). `Settings.from_yaml` calls `load_dotenv()`, `yaml.safe_load`, `_interpolate_env`, then `model_validate`.

**Gotcha:** `catalog_path` in the YAML is effectively ignored — `load_config` always derives it from the config file's directory. `Settings.from_yaml` can be called directly (bypassing that fixup) — used by `tests/stage10/test_multi_tenancy.py`.

---

### `src/openstore/models.py`

SQLModel table definitions for the entire money path, idempotency, audit, OAuth, WebAuthn, intent policies, campaigns, and chat handoffs.

**Helper:** `_utcnow()` returns naive UTC (`datetime.now(UTC).replace(tzinfo=None)`) — used as `default_factory` on virtually every timestamp column, to avoid aware/naive comparison bugs since SQLite DateTime columns are naive.

**Enums (`str, enum.Enum`):** `LedgerEntryType{RESERVE, CAPTURE, RELEASE, REFUND}`, `OrderState{CREATED, HELD, RELEASED, CANCELLED, PAID, FAILED, REFUNDED}`, `CampaignState{DRAFT, PENDING_APPROVAL, ACTIVE, PAUSED, EXPIRED, REJECTED}`, `WebhookStatus{RECEIVED, PROCESSING, COMPLETED, FAILED}`, `HandoffKind{POLICY, AMENDMENT}` (not tracked by the enum-exhaustiveness sentinel test — closed only via the DB column).

**Tables** (`SQLModel, table=True`):

- **`LedgerEntry`** (`ledger_entries`) — double-entry row: `entry_type`, `amount_minor: int ≥ 0` (paise), `reference_id` (= checkout_id), `account`/`counterparty_account`, unique `idempotency_key`. Indexes: `(trace_id, client_id)`, `(reference_id, entry_type)`.
- **`Checkout`** (`checkouts`) — the central order row: `id` (UUID checkout_id, PK), `merchant_id`, `cart_hash`, `cart_version`, `amount_minor`, `state: OrderState`, `policy_id`/`policy_hash`, `aal_level: int (0-3)`, `expires_at`, unique `idempotency_key`, PSP fields (`psp_provider`, `psp_order_id`, `psp_payment_link_id`, `short_url`, unique `cancel_token`), timestamps (`created_at, updated_at, paid_at, released_at, cancelled_at` — note: no dedicated `refunded_at`, that state reuses `cancelled_at`), chat identity fields (`chat_platform`, `chat_user_id` indexed, `chat_channel_id` — all nullable, only set for chat-originated checkouts), `cart_snapshot: dict` (JSON, required, INV-1), `agent_plan: dict | None` (PoAI), `request_text: str | None` (max 4096, becomes `human_intent` in evidence), `poai_bundle: dict | None` (null until the order reaches RELEASED and evidence is built). Indexes: `(trace_id, client_id)`, `(merchant_id, state)`.
- **`IdempotencyKey`** (`idempotency_keys`) — `key` PK, `request_hash`, `response_status`, `response_body: dict`, `expires_at` (indexed).
- **`WebhookEvent`** (`webhook_events`) — `psp_event_id` (unique+indexed), `event_type`, `payload: dict`, `status: WebhookStatus`, `retry_count: int ≥ 0`, `last_error`. Indexes: `(status, created_at)`.
- **`AuditLog`** (`audit_logs`) — `action`, `resource_type`, `resource_id`, request metadata, `response_status`, `audit_metadata: dict | None` (note: parameter name in `audit_log()` is `metadata`, column name is `audit_metadata`). Indexes: `(action, created_at)`.
- **`OAuthClient`**, **`OAuthAuthorizationCode`** (PKCE fields), **`OAuthToken`** (`jti` PK; `access_token_hash` column reused for both access- and refresh-token hashes).
- **`WebAuthnCredential`** (`webauthn_credentials`) — `credential_id` (unique+indexed), `user_handle` (indexed), `public_key: bytes` (COSE), `sign_count: int ≥ 0`, `is_active: bool`.
- **`IntentPolicy`** (`intent_policies`) — the signed spend policy: `policy_version=2`, `policy_hash`, `max_spend_per_tx_minor`, `max_spend_total_minor`, `max_transactions`, `allowed_tags`/`tag_mode`, `blocked_skus`, `not_before`/`expires_at` (**both `int` Unix-seconds — unlike almost every other `expires_at` in this file, which is a naive `datetime`**), `assertion_max_age_seconds=86400`, `no_human_authority: bool = False` (AAL0 gate), `fulfilment_mode`, `required_skus`, `webauthn_credential_id`/`webauthn_sign_count`, `is_active: bool`. Index `(merchant_id, is_active)`.
- **`Campaign`** (`campaigns`) — `discount_bps: int (0-10000)`, `applies_to_skus`, `starts_at`/`ends_at`, `source_signals: dict`, `draft_digest`, `state: CampaignState`, `merchant_signature`, `approver_credential_id`, `webauthn_assertion: dict | None`. Indexes: `(merchant_id, state)`, `(starts_at, ends_at)`.
- **`Handoff`** (`handoffs`) — durable single-use token (`secrets.token_urlsafe(32)` PK) parking a chat conversation during an out-of-band ceremony (policy signing or amendment approval), one table for both `HandoffKind`s. `request_text` (becomes `human_intent`), `expires_at`, `consumed_at`, `result_policy_id`, `amendment_draft: dict | None` (only for `AMENDMENT` handoffs — holds `MerchantAgent.draft_amendment`'s output plus the cart, since the draft function itself has no persistence hook). Index `(chat_platform, chat_user_id)`.

---

### `src/openstore/core/database.py`

Engine/session lifecycle, SQLite TOCTOU-safe transaction helpers (INV-11), spend-cap/exposure computation.

**Module state:** `_engine: Engine | None` — process-wide singleton (later `get_engine(config)` calls with a *different* config silently keep using the first engine). `_schema_ready: bool` — global flag, set by `mark_schema_ready()`.

**Functions**
- `get_engine(config) -> Engine` — SQLite URLs get `StaticPool`/`check_same_thread=False` plus `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (these PRAGMAs run unconditionally, even against a non-SQLite URL).
- `apply_migrations(config)` — SID-3: runs Alembic to `head`, locating `alembic.ini` four `dirname()`s up from this file. Sets `os.environ["OPENSTORE_DB_URL"]` as a side effect. Any failure is re-raised as `RuntimeError` (fail loud). Calls `mark_schema_ready()`.
- `session_scope(config)` / `immediate_session(config)` (`@contextmanager`) — the latter is documented as required for any spend-cap check-then-act sequence (INV-11); begins `BEGIN IMMEDIATE` for SQLite.
- `check_spend_cap(session, merchant_id, policy_id, amount_minor, max_spend_per_tx_minor, max_spend_total_minor) -> (bool, reason)` — must run inside an IMMEDIATE transaction. Checks per-tx cap, then `compute_policy_exposure(...) + amount_minor` against the total cap. **`merchant_id` parameter is accepted but unused.**
- `compute_policy_spend(session, policy_id) -> int` — settled spend: sums CAPTURE minus REFUND ledger entries for the policy's checkouts, `account == "merchant_revenue"`, hardcoded `currency == "INR"`.
- `compute_policy_exposure(session, policy_id) -> int` — settled spend **plus** outstanding RESERVE legs for checkouts still `CREATED`/`HELD` — in-flight exposure, so concurrent checkouts can't jointly exceed the cap.
- `get_or_create_checkout(...) -> (Checkout, created: bool)` — pure idempotent lookup-by-PK; does **not** validate that an existing row's other fields match the new call's arguments.
- `update_checkout_state(...)` — `HELD` deliberately does not stamp `paid_at`; `RELEASED` stamps `released_at`; `CANCELLED`/`REFUNDED` both stamp `cancelled_at` (no separate `refunded_at` column exists).

**Gotchas:** `get_engine`/`schema_ready` are both process-global singletons that ignore later/differing `config` arguments. `apply_migrations` mutates process env as a side effect.

---

### `src/openstore/core/idempotency.py`

INV-3: identical idempotency key + identical request body ⇒ identical response, both on read and write.

- `IDEMPOTENCY_TTL_SECONDS = 86400 * 7` (7 days).
- `compute_request_hash(body) -> str` — SHA-256 hex of canonical JSON (`[:64]` slice is a no-op since SHA-256 hex is already 64 chars).
- `check_idempotency(session, key, trace_id, client_id, body) -> (status, body) | None` — raises `IdempotencyError` for cross-tenant reuse, request-body mismatch, or expiry; returns cached response otherwise (or `None` if no row/no key).
- `store_idempotency_result(...)` — raises `idempotency_key_required` if key missing; raises `idempotency_response_mismatch` if a caller tries to overwrite a differing response under the same key; otherwise idempotent no-op or insert.
- `generate_idempotency_key(operation, trace_id, client_id, reference) -> str` — deterministic `f"idem:{operation}:{trace_id}:{client_id}:{reference}"`.
- `cleanup_expired_idempotency_keys(session) -> int`.

---

### `src/openstore/core/audit.py`

INV-12: every money/policy/identity call must be audit-logged with client_id + trace_id (enforced by convention, not by this module).

- `audit_log(session, ..., metadata=None) -> AuditLog` — writes an `AuditLog` row (note the `metadata` kwarg maps to the `audit_metadata` column — a naming mismatch other modules work around, see §6/§8).
- `AuditContext` — context manager: `set_response_status`, `add_metadata`; `__exit__` forces `status=500` if an exception occurred, writes the audit row, and **does not suppress the exception** (it propagates after the row is written). `start_time` is captured but never used (no duration is computed/stored).
- `get_audit_trail(session, ...) -> list[AuditLog]` — filtered, paginated read.

**Gotcha:** `AuditContext.__exit__`'s audit write happens in the *same* session/transaction as the caller — if the caller's outer transaction later rolls back, the audit row recording the failure is rolled back with it, unless a separate session is used.

---

### `src/openstore/core/health.py`

SID-2 readiness gating and SID-7 hand-rolled Prometheus-text metrics (no `prometheus-client` dependency, per Q-011).

- `GATED_PATHS: set[str]` — the agent/money routes gated until `is_ready()`: `/agent/catalog`, `/agent/mcp`, `/agent/acp`, `/agent/campaigns`, `/campaign/{campaign_id}/approve`, `/campaign/{campaign_id}/reject`, `/hold/{cancel_token}/cancel`, `/webhooks/razorpay`. Discovery/oauth/health routes are deliberately excluded. Rekor is explicitly *not* a readiness requirement.
- `compute_readiness(config) -> ReadinessState` — checks: `config` (hardcoded True), `database` (`SELECT 1`), `schema` (`database.schema_ready`), `razorpay_test_keys` (key starts with `rzp_test`), `signing_keys` (`_signing_keys_ready` — **side effect**: this generates PoAI signing keys if absent, meaning a readiness probe can trigger key generation).
- `is_gated(path) -> bool` — **exact string equality** against `GATED_PATHS`, which contains path *templates* (`{campaign_id}`) — the caller must pass the route template, not the resolved URL, for this to work.
- `metrics_text(config) -> str` — emits readiness, per-state checkout hold counts, per-account ledger balances (unsigned sum — RESERVE/CAPTURE/RELEASE/REFUND are all summed together with no netting convention visible in this function), and reconciliation drift.
- `_reconciliation_drift(config) -> int` — reads the first `AuditLog` row with `resource_type == "reconciliation_drift"` (`.first()`, **no `ORDER BY created_at`**, so "latest" is not actually guaranteed despite the comment saying so).

---

### `src/openstore/core/__init__.py`

Pure re-export module — the package's public API surface. Docstring: "deterministic spine (NO LLM imports permitted)" (not enforced in this file itself). Re-exports from `.api`, `.audit`, `.compiler`, `.database`, `.holdcancel`, `.idempotency`, `.ledger`, `.oauth`, `.poai`, `.webauthn_rp`, `.webhooks`.

**Notably NOT re-exported:** anything from `health.py` (server.py imports it directly), and nothing from `handoff.py`, `campaigns.py`, `policy_signing.py` (all exist in `core/` but are excluded from this public surface).

**Note:** there is no `src/openstore/core/aal.py` — confirmed against `git log`, it was deleted in commit `cf7ca0a` (the AAL logic was consolidated into `core/holdcancel.py`, which now owns it). Only a stale `__pycache__/aal.cpython-312.pyc` remains on disk in some checkouts. See §3.

---

## 3. Money & policy engine

### `src/openstore/core/ledger.py`

Double-entry ledger for reserve/capture/release/refund (INV-5).

- `LedgerError(Exception)` — `reason_code` + `message`; raised only for non-positive `amount_minor`.
- `create_reserve_entry(...)` — idempotent (keyed `reserve:{trace_id}:{client_id}:{checkout_id}`); writes a RESERVE pair: `customer_hold ↔ merchant_pending`.
- `create_capture_entry(...)` — idempotent (`capture:...`); writes **four** entries: two RELEASE-type reversals undoing the RESERVE, plus two CAPTURE entries (`merchant_revenue` and a `platform` counterpart). **The platform-fee leg is an explicit stub — comment: "for now 0, just balancing entry" — no fee calculation exists.**
- `create_release_entry(...)` — idempotent (`release:...`); two RELEASE entries reversing the RESERVE, no CAPTURE.
- `create_refund_entry(...)` — idempotent (`refund:...`); two REFUND entries reversing a CAPTURE (`merchant_revenue` ↔ `customer_refund`).
- `get_ledger_balance(session, account, ...) -> int` — signed sum keyed by *account name*.
- `verify_ledger_balances(session, reference_id) -> bool` — INV-5a: escrow accounts (`customer_hold`, `merchant_pending`) must each net to exactly 0; economic accounts (`merchant_revenue`, `platform`) must each be ≥ 0. **Uses a different sign convention (by entry_type only) than `get_ledger_balance` (by account name)** — they happen to agree only because of how this module always pairs account+entry_type; no shared helper enforces that pairing.

---

### `src/openstore/core/holdcancel.py`

State machine for hold/release/cancel/refund, and the AAL (Authenticator Assurance Level) ladder that drives hold duration (INV-4/5/8/11). **This is where the AAL logic actually lives** (there is no separate `aal.py` — it was consolidated here).

- `AALLevel(IntEnum)`: `AAL0=0` (no human authority / `policy.no_human_authority`), `AAL1=1` (1h hold), `AAL2=2` (15min hold), `AAL3=3` (immediate release).
- `AAL_HOLD_SECONDS = {AAL0: None, AAL1: 3600, AAL2: 900, AAL3: 0}`. `AAL_LIABILITY` — human-readable liability sentences, each explicitly labeled "Proposed liability position (not a network rule)".
- `compute_aal_level(has_webauthn_assertion, policy_allows_no_human_authority, amount_minor, threshold_aal3=10000, threshold_aal2=50000) -> AALLevel` — `policy_allows_no_human_authority` ⇒ `AAL0` unconditionally; else by amount thresholds (₹100/₹500 defaults) → AAL3/AAL2/AAL1. `has_webauthn_assertion` is accepted but unused in the body (assumed already gated by compiler check 0).
- `calculate_expires_at(aal_level, created_at=None) -> datetime` — AAL0 → `+365 days` (despite `AAL_HOLD_SECONDS[AAL0]=None` being commented "no hold — no order created", the code still transitions the checkout to `HELD` with a real 365-day expiry — the comment doesn't match the actual code path); AAL3 → immediate (`created_at` unchanged); AAL1/AAL2 → `+hold_seconds+60s buffer`.
- `initiate_hold(session, config, checkout_id, ..., max_spend_per_tx_minor=None, max_spend_total_minor=None) -> Checkout` — requires `state == CREATED`. If `policy_id` given, resolves caps: uses the passed override caps only if **both** are given (an in-memory, unpersisted, amendment-relieved `IntentPolicy` snapshot — S11 Phase 4/Q-020 — bypasses a stale DB re-fetch); otherwise re-fetches `IntentPolicy` from DB. Re-checks `check_spend_cap` (INV-11). Writes a RESERVE ledger entry, sets `state=HELD`.
- `release_hold` — requires `HELD`; writes CAPTURE; sets `RELEASED`, stamps `released_at`.
- `cancel_hold` — requires `HELD`; writes RELEASE; sets `CANCELLED`, stamps `cancelled_at`.
- `refund_checkout` — requires `RELEASED` (a CREATED/HELD checkout must go through `release_hold`/`cancel_hold` instead — refunding an unreleased checkout would create an unmatched REFUND with no prior CAPTURE, breaking INV-5). Writes REFUND; sets `REFUNDED`, reuses `cancelled_at`.
- `check_and_expire_checkouts(session) -> int` — INV-8 sweeper: `CREATED` past expiry → force-`CANCELLED` (no ledger entry, nothing was ever reserved); `HELD` past expiry with `AAL3` → auto-`release_hold` (auto-capture); `HELD` with `AAL1`/`AAL2` → auto-`cancel_hold` ("Hold period expired").

---

### `src/openstore/core/compiler.py`

The Intent Compiler — evaluates a cart against a signed `IntentPolicy` via **12 ordered checks**, returning an allow/deny decision plus a full transcript (PoAI evidence).

- `CompilerContext` (frozen dataclass) — input bundle: cart, policy, merchant_id, currency, checkout_count, cumulative_spend, assertion state, `now_unix`, `campaign_lookup`.
- `CompilerResult` (frozen dataclass) — `allowed`, `reason_code`, `transcript`, `aal_level`, spend figures, `checkout_id`.
- `REASON_CODES: set[str]` — the closed set of every reason code the compiler can emit; defined but never actually asserted against in this file.
- `compile_decision(ctx) -> CompilerResult` — pure function, no I/O, never raises (all failures come back via the result). Checks in order, first failure short-circuits:
  0. `human_authority_present` — fails `assertion_required` unless `policy.no_human_authority` or a WebAuthn assertion is present.
  1. `currency_match`
  2. `merchant_lock`
  3. `policy_not_before`
  4. `policy_expiry`
  5. `transaction_count` — `checkout_count >= max_transactions`
  6. `item_qty` (+ `sku_duplicate`) — `qty <= 0` or a duplicate SKU fails
  7. `item_blocked_sku`
  8. `item_tag_allowlist` — `tag_mode="all"` requires every item's tags ⊆ `allowed_tags`; any other value (docstring implies `"any"`) requires intersection — **no validation that `tag_mode` is actually one of the two expected literals; anything else silently behaves as "any"**.
  - Campaign validity (`_campaign_validity`) is checked here, between checks 8 and 9, so an invalid campaign can never discount the spend-cap math — but on the *success* path a `campaign_validity` transcript entry is re-recorded as check 12 at the very end, so **the transcript's `campaign_validity` entry sits in a different position depending on pass vs. fail** (documented as intentional).
  9. `spend_per_tx` — computes per-campaign discount via floored integer division (`subtotal * discount_bps // 10000`, grouped by campaign, not per-line, to avoid per-line rounding leakage) then compares the discounted total to `max_spend_per_tx_minor`.
  10. `spend_envelope` — **always passes; not implemented for MVP** (delegated/sub-policy budgets).
  11. `spend_cumulative`
  12. `campaign_validity` (trailing, recorded-as-passed marker; the real validation already ran above).
  - On success: computes `aal_level` via `compute_aal_level` (imported locally from `holdcancel` to avoid a circular import).
- `get_compiler_version() -> "1.0.0"` (hardcoded).
- `get_compiler_digest() -> str` — **real, dynamic** SHA-256 of `compiler.py`'s own source bytes at call time (changes whenever this file changes) — used by the verifier to cross-check the compiler identity claimed in a PoAI bundle.

**Gotcha:** `total_qty` is accumulated in check 6 but never used (no max-quantity check exists despite it).

---

### `src/openstore/core/policy_signing.py`

Turns a draft `IntentPolicy` into a signed, persisted-by-caller policy: version validation, per-user aggregate cap enforcement, deterministic hash.

- `PER_USER_AGGREGATE_CAP_MINOR = 500_000` — demo default (PRD §3.2a); lives here rather than `config.py` because config was "out of scope for this stage" per comment.
- `POLICY_HASH_FIELDS` — whitelist of exactly 15 field names the hash is computed over; server-derived fields (id, policy_hash, webauthn_*, signed_at, is_active) are excluded.
- `LegacyPolicyError` — raised by `validate_policy_version` when `policy_version != 2` ("never mapped, never defaulted" per docstring — a hard failure).
- `aggregate_cap_exceeds(...)` / `compute_user_aggregate(...)` / `blast_radius(...)` — pure aggregate-cap math.
- `compute_policy_hash(fields) -> str` — SHA-256 of canonical JSON over the whitelist; fields outside the whitelist are silently dropped, whitelist fields missing from `fields` are silently omitted (not defaulted) — two payloads differing only outside the whitelist can hash identically.
- `complete_policy_signing(*, fields, merchant_id, user_id, credential_id, webauthn_sign_count, aggregate_spent_minor, ...) -> SigningOutcome` — this is the **only** place `LegacyPolicyError` is caught and mapped to `policy.policy_version_unsupported` (calling `validate_for_signing` directly does not catch it — it propagates as an exception). `policy_id = "pol_" + sha256(merchant_id + ":" + policy_hash)[:24]` — deterministic, not random. **`user_id` parameter is accepted but unused in the function body.** Does **not** persist to the DB — no `session` parameter at all; the caller (`studio.py`) commits.

---

### `src/openstore/core/api.py`

The single top-level Commerce Core API — orchestrates policy lookup, WebAuthn verification, compiler evaluation, checkout persistence, and hold lifecycle. Consumed by the MCP server, Studio, and the PSP driver.

- `CommerceError(Exception)` — `reason_code`, `message`, `status_code` (default 400).
- `create_checkout(...) -> CompilerResult` — loads the active `IntentPolicy` (404 `policy_not_found` if none). If a `webauthn_assertion` was supplied, calls `complete_assertion` bound to `{"mode":"cart","cart_hash":...}` inside a **bare `except Exception: assertion_verified = False`** — any bug in `complete_assertion` itself would silently degrade to "not verified" rather than surfacing. `assertion_age` is taken directly from the client-supplied `webauthn_assertion["age_seconds"]`, **not independently computed server-side** in this function.
- `create_checkout_from_policy(...)` — writes an audit log unconditionally; resolves campaign snapshots per cart item (missing campaigns are silently omitted, later causing the compiler's `_campaign_validity` to fail); computes `cumulative_spend` via `compute_policy_exposure` and `checkout_count` (counts **all** checkouts ever created against the policy, not just open ones — this is what check 5 compares against `max_transactions`); calls `compile_decision`. If denied, audits and returns as-is (no checkout row, no ledger entries). If allowed: `get_or_create_checkout` (idempotent upsert with a placeholder `expires_at=now()`), and **only if the row was newly created** calls `initiate_hold` (an idempotent re-invocation skips this, since re-running it on an already-past-CREATED checkout would raise).
- `confirm_checkout(...) -> Checkout` — terminal states (`RELEASED`/`PAID`/`REFUNDED`) short-circuit unchanged (idempotent). Requires `HELD` otherwise. `allow_autonomous = policy.no_human_authority` skips WebAuthn re-verification entirely for AAL0. Otherwise requires and re-verifies a WebAuthn assertion (again wrapped in a bare `except Exception: verified=False`) — **note: called without the `binding` argument used at `create_checkout` time**, so the two call sites verify different binding contexts. Re-runs `check_spend_cap` server-side (explicit anti-TOCTOU re-check). Transitions `HELD → RELEASED` via `update_checkout_state` — **no ledger entry is created directly in this function.**
- `cancel_hold_flow(session, trace_id, client_id, cancel_token) -> Checkout` — 404 on unknown token, 400 if not `HELD`; delegates to `cancel_hold`.
- `verify_checkout_evidence(session, checkout_id) -> dict` — returns `{"error": "checkout_not_found"}` as a plain dict rather than raising `CommerceError`, **inconsistent with every other function in this file**. Otherwise returns the full ledger + audit trail + `verify_ledger_balances` result — the source data for the PoAI evidence bundle.
- `run_sweepers(config, session) -> dict[str,int]` — runs `check_and_expire_checkouts` (INV-8) and `process_webhook_retry_queue` (INV-7).

---

## 4. Identity & proof

### `src/openstore/core/webauthn_rp.py`

WebAuthn Relying Party — registration/assertion ceremonies, a single-use challenge store, COSE-alg/UV/sign-count enforcement.

- `WebAuthnError(Exception)` — `reason_code` must be one of the closed REGISTRY set (`webauthn_unsupported_alg`, `assertion_required`); an optional `failure_type` (audit-only, not a REGISTRY code) must be a member of a fixed closed set or the constructor itself raises `ValueError`.
- `ChallengeStore` — in-memory, thread-safe (`threading.Lock`), single-tenant, TTL-bounded (`CHALLENGE_TTL_SECONDS = 120`). `consume()` marks-used rather than deletes, so a replay is reported as "reused" not "unknown". A module singleton `_DEFAULT_STORE` backs calls that don't pass an explicit store.
- `_enforce_supported_alg` — only `ES256 (-7)` and `RS256 (-257)` accepted (`_SUPPORTED_ALGS`); EdDSA (-8) is explicitly out of scope.
- `begin_registration(...)` — `AuthenticatorSelectionCriteria(resident_key=REQUIRED, user_verification=REQUIRED)` with **no** `authenticator_attachment` restriction (comment: a prior PLATFORM restriction broke cross-device/QR passkey flows). `attestation=NONE` (comment: DIRECT breaks with real passkey providers).
- `complete_registration(...)` — consumes the challenge; pre-enforces the alg allowlist *before* calling `verify_registration_response`, so a bad alg always surfaces as `webauthn_unsupported_alg`, never masked by py_webauthn's own error.
- `complete_assertion(...) -> (bool, int)` — full pipeline in order: consume challenge → binding match (`mode` must agree; `cart` binding requires matching `cart_hash`; `amendment` requires matching `amendment_id`; `policy` mode always matches once modes agree) → active-credential lookup → alg check → decode `authenticator_data` and require the UV flag → **sign-count monotonicity** (`received <= stored` fails unless both are zero, i.e. counter-less authenticators are tolerated) → `verify_authentication_response` → update `sign_count`/`last_used_at`.
- Every RP rejection except a bad COSE alg collapses to the single REGISTRY reason code `assertion_required` — the finer-grained `failure_type` is explicitly local/audit-only, not a REGISTRY code.

---

### `src/openstore/core/poai.py`

Constructs/verifies the 9-section Proof-of-AI-Intent (PoAI) evidence bundle — canonical JSON hashing, SHA-256 hash chain, ES256 merchant JWS, merkle-daily time anchor, AAL0–AAL3 predicate ladder.

- `SECTION_ORDER = ("transaction", "human_intent", "authority", "goods", "agent", "adjudication", "notification", "aal", "campaign")` — a non-applicable section serializes as `null` but is still chained.
- `build_hash_chain(sections_data) -> {"links": [...9 "sha256:"-prefixed], "root": <bare hex, no prefix>}` — note the asymmetry between prefixed `links` and unprefixed `root`.
- `sign_merchant_jws_compact(...)` — ES256 JWS Compact over exactly `{bundle_id, issued_at, root}`; **`kid` is hardcoded `"{merchant_id}-key-1"`**, not parameterized by a real rotation index despite the docstring's `{merchant_id}-key-{n}` notation.
- `evaluate_aal_predicates(bundle) -> {e1..e9}` — `e3` (freshness) does **not** guard against `adjudication.evaluated_at` being timestamped *before* `authority.webauthn.signed_at`; a negative diff still passes as long as `|diff| <= assertion_max_age_seconds`.
- `compute_aal_level_from_bundle(bundle) -> int` — first-match decision table over the predicates (not e2/not e7/not e1 → 0; policy_version≠2/not e3/not e6/not e4/not(e8 and e9) → 1; e5 → 3; else → 2).
- `create_poai_bundle(...) -> dict` — **silently swallows any signing exception** (`except Exception: merchant_signature = None`), no error surfaced.
- `verify_poai_bundle(bundle) -> (bool, list[str])` — **only checks the 9-section hash chain integrity; does NOT verify `merchant_signature` (JWS) or `time_anchor` at all.** A bundle with a forged/absent signature but a self-consistent hash chain still verifies `True`.
- `AALLevel(int)` — a plain `int` subclass (**not** an `IntEnum`), distinct from `holdcancel.AALLevel` (an `IntEnum`) — see §8 for the name-collision consequences. There is likewise a second, separately-implemented `get_aal_liability_sentence` in this module, distinct from (and not exported in place of) `holdcancel`'s.

---

### `src/openstore/core/oauth.py`

OAuth 2.1 client/token management (INV-9) with PKCE authorization codes and asymmetric ES256 JWT access tokens verified against merchant JWKS.

- `ACCESS_TOKEN_TTL_SECONDS = 3600`, `REFRESH_TOKEN_TTL_SECONDS = 86400*30`, `AUTH_CODE_TTL_SECONDS = 600`. `_JWS_ALG_ALLOWLIST = {"ES256"}`.
- `hash_client_secret` — plain unsalted SHA-256 (acceptable only because the secret itself is `secrets.token_urlsafe(32)`, already high-entropy).
- `validate_authorization_code(...)` — PKCE: only `S256` supported; if `code_challenge` was set at creation, a missing `code_verifier` is a hard failure (PKCE cannot be silently bypassed).
- `create_token_pair(...) -> (access_token, refresh_token)` — reuses the same `OAuthToken.access_token_hash` column for both token kinds.
- `_token_private_key(merchant_jwks)` — falls back to `wellknown._load_or_generate_poai_keys("merchant")`'s key when no explicit key is given — i.e. by default the **same keypair** signs OAuth tokens and PoAI/catalog attestations, and is served at `/.well-known/poai-jwks.json`.
- `_resolve_token_kid(merchant_jwks)` — **ignores its `merchant_jwks` argument entirely**; always resolves the kid from the default merchant key, even though `_create_jwt_token`/`_token_private_key` *do* honor an explicit custom key. A caller passing custom `merchant_jwks` with a different key would sign with that key but stamp the wrong (default) kid — a latent verification mismatch.
- `validate_access_token(...) -> OAuthToken` — verification order is signature-before-trust: split → decode header → check `alg` allowlist → require `kid` → resolve public key → verify signature → **only then** decode claims, check `jti`/`exp` → look up the `OAuthToken` DB row (source of truth for scopes/subject; JWT claims like `scope`/`sub`/`aud` are not otherwise trusted) → check revocation → check required scopes. A token with no `exp` claim at all never expires via this check (though `_create_jwt_token` always sets one). All parse errors funnel into one generic `invalid_token`/"Invalid token format".
- `get_jwks(config) -> dict` — delegates to `wellknown.get_poai_jwks` — the OAuth JWKS endpoint and the PoAI well-known JWKS endpoint serve identical key material.

---

### `src/openstore/devtools/virtual_authenticator.py`

Deterministic in-memory FIDO2/WebAuthn authenticator for tests/fixtures — produces real py_webauthn-verifiable responses (`fmt: "none"` attestation) matching pinned GOLDEN fixtures.

- `VirtualAuthenticator` (dataclass: `credential_id`, `key`, `rp_id`, `origin`, `sign_count=1`) — `register()` builds a CBOR `{"fmt":"none","attStmt":{},"authData":...}` attestation object; `assert_credential()` builds and signs an assertion. Both accept an explicit `sign_count` override, enabling deliberate replay/regression fixtures for `webauthn_rp.py`'s monotonicity checks.
- Supports EC (P-256/ES256), RSA (RS256), and Ed25519 key types for COSE encoding/signing — Ed25519 is generatable here even though `webauthn_rp.py`'s RP-side allowlist rejects it (useful for negative tests).
- `aaguid` is always 16 zero bytes; attestation format is always `"none"`.
- Used only by tests (`tests/test_virtual_authenticator.py`, `tests/test_checkout_flow.py`) and fixture-generation scripts — no `src/` module imports it.

---

## 5. Payments, webhooks, campaigns, notifications

### `src/openstore/psp/router.py`

FastAPI router: the Razorpay webhook receiver and hold-cancel endpoint, plus the chat-notification/PoAI-evidence glue triggered by webhook-driven state transitions.

- **`POST /webhooks/razorpay`** — requires `X-Razorpay-Signature` (401 if absent); verifies via `driver.verify_webhook_signature` (`cfg.razorpay.webhook_secret` falls back to the literal `"test_secret"` if unconfigured — **the endpoint never hard-fails for a missing secret**); ignores event types outside `driver.HANDLED_WEBHOOK_EVENTS` without persisting; persists the raw event and schedules processing via FastAPI `BackgroundTasks`; **always returns 200** immediately — actual processing success/failure only shows up in `WebhookEvent.status`/`last_error`, never in the HTTP response.
- **`POST /hold/{cancel_token}/cancel`** — looks up the checkout by `cancel_token` (404 if not found); delegates to `driver.cancel_checkout_by_id`.
- `_push_chat_notification(...)` — no-op unless the event type is in `_CHAT_PUSH_EVENTS = {"payment_link.paid", "payment_link.partially_paid", "payment_link.cancelled"}` **and** the checkout has a `chat_user_id` (only chat-originated orders get DMs). For paid events, also builds and stores a PoAI evidence bundle (`_build_and_store_evidence`) and DMs a link to `/orders/{id}/evidence/view`.
- `_build_and_store_evidence(...)` — builds the bundle via `core.poai.create_poai_bundle`; `authority.webauthn` is populated only with `credential_id`/`policy_version` (**no raw WebAuthn assertion is persisted per-checkout, so predicate e2 is honestly False for bundles built this way**); the `human_intent` digest here is **deliberately unprefixed** (no `sha256:`), unlike other digests in the codebase — an explicit in-code comment warns against "fixing" this inconsistency.

---

### `src/openstore/psp/razorpay_driver.py`

The Razorpay payment-gateway integration proper (Stage 5): payment links, refunds, webhook verification/dispatch, reconciliation sweeper (INV-7), hold-release worker (INV-8). Test-mode-key only — every function that talks to Razorpay calls `assert_test_mode_key` first, which hard-fails on an `rzp_live_` key.

**Classes**
- `RazorpayError(Exception)` — `error_code`, `message`, `http_status`; alias `PSPError = RazorpayError`. Comment: "Catches ONLY pinned error codes" — this module re-wraps failures into known codes rather than letting raw exceptions escape.
- `PspIntent` (dataclass) — modeled as the pre-network-call persisted intent record, but **appears to be dead code**: never instantiated anywhere in this file or the rest of `src`/`tests`. The actual INV-4 dual-write is done directly against `Checkout`/`IdempotencyKey`.

**Key functions**
- `create_payment_link(...)` — requires `checkout.state == HELD`; INV-4 dual-write via an `IdempotencyKey` row written *before* the network call. `callback_url` (the browser GET redirect after payment) is deliberately distinct from the webhook POST endpoint — comment references a fixed prior bug (commit `cf7ca0a`) where pointing it at `/webhooks/razorpay` caused every real payment redirect to 405. On a duplicate-reference-id error from Razorpay, attempts recovery via `_fetch_existing_payment_link_by_reference_id` before giving up with `psp.duplicate_unrecoverable`.
- `cancel_payment_link(...)` — if Razorpay rejects the cancel because the link is already paid (HTTP 400), falls back to issuing a `refund_checkout` instead (DECISIONS §11.1.2: an already-paid link cannot be cancelled).
- `cancel_checkout_by_id(...)` — the shared entry point used by both the `/hold/{token}/cancel` route and the bot's `cancel` command.
- `refund_checkout(...)` — calls `client.payment.refund(payment_id, {...})`. **`payment_id` passed is actually `checkout.psp_payment_link_id`** — a payment-*link* id, not a Razorpay payment id, which Razorpay's refund API expects. No payment_link→payment mapping exists anywhere in this file — this looks like it would fail against the live API outside of mocked tests. See §8.
- `verify_webhook_signature` / `process_incoming_webhook` / `_dispatch_webhook_event` — HMAC-SHA256 verification, then dispatch by event name: `payment_link.paid`/`partially_paid` → `_apply_payment_link_paid` (writes a CAPTURE entry, sets `RELEASED` directly — **not** `PAID` — comment: `payment_link.paid` is treated as the final fulfilment transition, `PAID` is skipped); `payment_link.cancelled` → `_apply_payment_link_cancelled` (RELEASE entry only if currently `HELD`); `payment.failed` → `_apply_payment_failed`. All three handlers are terminal-state-absorbing (silently no-op on an already-terminal checkout, for idempotent replay).
- `reconciliation_sweep(...)` — polls Razorpay for the true status of every `CREATED`/`HELD` checkout aged 10 minutes–7 days, reconciles drift, and emits an audit row (`reconciliation_drift`) if any drift is found (feeds the `reconciliation_drift_total` metric in `health.py`).
- `ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]]` — the full state machine: `CREATED→{HELD,CANCELLED,FAILED}`, `HELD→{PAID,RELEASED,CANCELLED,FAILED}`, `PAID→{RELEASED,REFUNDED}`, `RELEASED→{REFUNDED}`, `CANCELLED→{REFUNDED}`, `FAILED`/`REFUNDED` terminal.
- `hold_release_worker_tick_with_notifications(...)` — snapshots chat-originated candidate checkouts before running the expiry tick, then reports which ones actually transitioned, so `server.py`'s 30s loop knows who to DM.

**Gotchas**
- `PspIntent` is dead code (never instantiated).
- `RAZORPAY_CANCEL_ALREADY_PAID_HTTP_STATUS == 400` is compared to the literal `400` in the same module — a tautological check; the real discriminator is a string match on the error text.
- `cancel_checkout_by_id`'s pre-fetch of a real Razorpay client silently swallows construction failure to `None`, but this has no functional effect since `cancel_payment_link` would just construct its own client anyway when `mock_razorpay is None`.
- `reconciliation_sweep`'s age window is checked twice via two mathematically-equivalent comparisons — redundant, not incorrect.
- No amount/currency cross-check between a webhook payload and the local ledger beyond the reconciliation sweeper's drift metric — `_apply_payment_link_paid` trusts `link.get("amount", checkout.amount_minor)` directly (protected only by the HMAC signature check upstream).

---

### `src/openstore/psp/__init__.py`

Pure re-export of the driver's public surface: `PSPError`, `create_payment_link`, `cancel_payment_link`, `verify_webhook_signature`, `persist_raw_webhook_event`, `process_webhook_in_worker`, `run_reconciliation_sweeper`, `hold_release_worker`.

---

### `src/openstore/core/webhooks.py`

A **second, independent** webhook dedup/idempotency/retry framework (INV-6/INV-7), structurally provider-agnostic but currently Razorpay-specific in its processors. **This is a distinct pipeline from `psp/razorpay_driver.py`'s own dispatch path** — used by `core/api.py` and the redteam tests, not by `psp/router.py`. See §8 for the consequences of two parallel implementations.

- `MAX_RETRIES = 5`; `RETRY_DELAYS = [60, 300, 900, 3600, 21600]` (indexed by retry_count, clamped to the last element).
- `process_webhook_event(session, ..., processor: Callable) -> WebhookEvent` — dedupes by `(psp_provider, psp_event_id)`; on processor exception, marks `FAILED`, stores the error, and **re-raises** (unlike the driver's own `_process_persisted_webhook`, which catches and returns a dict).
- `handle_razorpay_payment_captured` / `handle_razorpay_payment_failed` — dispatch on `payment.captured`/`payment.failed` (note: a **different event-type vocabulary** than the driver's `payment_link.paid`/`payment_link.cancelled`). `handle_razorpay_payment_captured`'s comment says "log but don't fail (may be race condition)" for a missing checkout, but the code actually **raises** `WebhookError` — contradicting the comment.
- `process_webhook_retry_queue(session) -> int` — INV-7 sweeper; for any provider/event-type combination outside the two explicitly handled, silently "completes" the retry with no actual reprocessing work.

---

### `src/openstore/core/campaigns.py`

Campaign store and deterministic (non-LLM) validator (INV-14: the campaign agent's *only* input is an aggregated analytics view — never raw orders/buyer identities/payment data).

- `_PROMPT_INJECTION_PATTERNS`/`_PROMPT_INJECTION_PHRASES` — a hardcoded substring/phrase blocklist (`"ignore previous instructions"`, `"[INST"`, `"you are now a"`, etc.) applied to campaign `title`/`rationale`.
- `validate_campaign(session, campaign, config) -> None` — raises `CampaignValidationError` on: unknown SKU, `discount_bps` outside `[config.campaign.min_bps, max_bps]`, invalid time window, a SKU also present in any active policy's `blocked_skus`, empty content, or a prompt-injection match. **`create_campaign` does not call this itself** — a `DRAFT` campaign can be persisted without validation; the caller must invoke it separately before activation.
- `get_analytics_view(session, merchant_id) -> list[dict]` — the sole campaign-agent input surface; iterates **all** checkouts for the merchant regardless of state (no filter for paid/cancelled), aggregating per-SKU 7d/30d units, 30d gross, attach rate.
- `activate_campaign(...)` — raises `campaign.no_webauthn_approval` if no `webauthn_assertion` dict is supplied, but **does not itself cryptographically verify** that assertion — only checks that one was passed. Enforces a **hardcoded** max of 5 concurrently-active campaigns (comment claims this comes from config; no `config.campaign.max_active` field is actually read here).

---

### `src/openstore/notifier.py`

Discord notification layer — four trace channels (buyer/merchant/money/alerts) plus DMs. R0.10: holds no payment keys/PSP credentials/signing material. Offline-safe — logs instead of sending when no bot token is configured.

- `run_from_worker_thread(coro)` — bridges a sync `BackgroundTasks` thread into the live Discord client's asyncio loop via `asyncio.run_coroutine_threadsafe(...).result()` (falls back to a fresh `asyncio.run(coro)` if no main loop is registered, e.g. offline/tests) — avoids breaking discord.py's loop-bound aiohttp session.
- `_init_discord(config)` — returns the already-registered live client if set; if no token (or the literal placeholder string `"token"`) is configured, returns `None` (offline mode, "no silent network attempt in tests"); otherwise constructs a fresh **unstarted** `discord.Client` purely for shape consistency — this client is never logged in via this path.
- `DiscordNotifier._send(...)` — re-resolves the client fresh on every call (deliberately not cached, so a client registering later isn't permanently blackholed); on no client, logs at INFO instead of sending; **all Discord send exceptions are caught and only logged as warnings — never raised**, so a failed DM/trace push is always silent to the caller (by design, per R0.10/offline-safety).
- `send_dm(config, user_id, message)` — same offline/exception-swallowing pattern; a non-numeric `user_id` (→ `int()` `ValueError`) is caught the same way as a real send failure.
- Sync fallbacks (`sync_money_trace`, `sync_buyer_trace`, `sync_merchant_trace`, `sync_alert`) — log-only, no Discord I/O; used by `core/campaigns.py`, `surfaces/studio.py`, `agents/*` where a synchronous call site can't await the async path.

**What triggers a DM/trace (from `psp/router.py`):** `payment_link.paid`/`partially_paid` → "Paid! ₹X — order released." + money_trace + (chat-originated only) an evidence-bundle build + evidence link DM. `payment_link.cancelled` → "Hold cancelled." + money_trace. (Hold-expiry warning/release DMs are driven from `server.py`'s 30s loop — see §7.)

---

## 6. Surfaces & offline verifier

### `src/openstore/surfaces/catalog.py`

Loads/searches/serves the merchant catalog feed, with per-item ES256 catalog attestations (PRD §3.8).

- `CATALOG_CACHE: list | None` — process-global cache, **no invalidation/TTL/mtime check** — once populated, it never reflects catalog file edits for the life of the process (tests reset it directly).
- `load_catalog(config)` — normalizes each item: `unit_minor` falls back to a legacy `price_minor` key, then `0`.
- `build_catalog_attestation(...)` — ES256 JWS over `{sku, price_minor, tags, catalog_digest, merchant_id, iat}`; `kid = "openstore-key-" + first 8 hex chars of the public key's x-coordinate`.
- `serve_catalog_feed(config, merchant_id, private_key_pem=None)` — **swallows any attestation-build exception with a bare `except Exception: pass`**, leaving `attestation=None` with no logging. Currency is hardcoded `"INR"`, not read from config.

---

### `src/openstore/surfaces/evidence.py`

Serves the persisted PoAI evidence bundle (S11 Phase 4) — read-only; the bundle itself is produced in `psp/router.py`.

- `GET /orders/{checkout_id}/evidence` — 404 if the checkout doesn't exist, 404 (`checkout.evidence_not_found`) if `poai_bundle is None`; else returns the raw bundle JSON.
- `GET /orders/{id}/evidence/view` — 404 only if the checkout doesn't exist (**does not** check for a missing bundle the way the JSON route does); serves an HTML viewer whose client-side JS fetches `/evidence` and silently no-ops on failure, leaving the manual file-upload UI as a fallback.

---

### `src/openstore/surfaces/mcp_server.py`

The OpenStore MCP tool surface — **14 tools**, a closed set (PRD §6), thin adapters over `core/api.py` and the WebAuthn RP.

`search_products`, `get_product`, `create_cart`, `update_cart`, `checkout_initiate`, `checkout_confirm`, `get_order`, `get_audit_log`, `webauthn_register_begin`, `webauthn_register_complete`, `webauthn_begin_assertion`, `webauthn_complete_assertion`, `list_campaigns`, `get_campaign`. Each returns `MCPToolResult{success, data, error}`, catching `CommerceError` → `{reason_code, message}` and any other exception → `internal_error`.

- **Only 6 of 14 tools enforce a scope check** via `_require_scope` (`create_cart`, `update_cart`, `checkout_initiate`, `checkout_confirm` — the other five, `get_order`/`get_audit_log`/`webauthn_*`/`list_campaigns`/`get_campaign`, are open to any caller regardless of `token_scopes`).
- `checkout_initiate` — if `chat_user_id` is present, stamps chat identity fields onto the `Checkout` row *before* creating the payment link, so downstream webhook/worker/evidence code can find the Discord identity. Builds only a minimal `{"name": "Discord user {id}"}` customer object — no fabricated email/phone.
- `checkout_initiate` catches `RazorpayError` separately from `CommerceError` (an in-code comment flags this as a previously-fixed bug: `RazorpayError` does not subclass `CommerceError`, so without the separate catch it would flatten to `internal_error`).
- `handle_mcp_request(...)` dispatches by `tool_name` against a closed `TOOL_NAMES: frozenset[str]`; unknown tool → `{"success": False, "error": {"reason_code": "auth.unknown_tool", ...}}` (no exception raised).

---

### `src/openstore/surfaces/wellknown.py`

Builds the `.well-known` manifest documents and manages per-merchant ES256 signing keys for PoAI/catalog attestations.

- `POAI_KEYS: dict[str, dict]` — process-global, keyed by `merchant_id`. **Keys are generated in-memory and never persisted** — a process restart generates fresh keys, invalidating any previously issued attestations/signatures. `kid` is hardcoded `"{merchant_id}-key-1"` — no rotation.
- `build_agent_commerce_manifest`, `build_agent_policy_manifest`, `get_poai_jwks`, `get_signed_campaign_feed` — pure/DB-read builders for the manifest endpoints. `get_signed_campaign_feed` filters `ACTIVE` campaigns to those currently inside `[starts_at, ends_at)` (INV-13: an out-of-window ACTIVE campaign would fail compiler check 12 anyway, so it's pre-filtered from the feed).
- `default_per_tx_cap_minor` (50000) and `max_per_tx_cap_minor` (50000) are hardcoded literals duplicated across two builder functions rather than sourced from one constant.

---

### `src/openstore/surfaces/studio.py`

"Policy Studio" — the operator-facing web backend for WebAuthn enrollment, spend-policy signing ceremonies, blast-radius inspection, and (S11 Phase 4) chat-originated amendment approve/reject.

- Operator identity is entirely a self-asserted `X-Operator-Id` header (`_operator` FastAPI dependency) — explicit comment: "no auth framework is in this stage's scope"; identity is only cryptographically bound through the WebAuthn ceremonies that follow, not the header itself.
- `_apply_amendment_delta(policy, delta) -> IntentPolicy` — applies only two fields an amendment draft can carry (`add_allowed_skus`, `bump_max_spend_per_tx_minor`); returns a **new, unpersisted** `IntentPolicy` via `model_copy` — never mutates the standing signed row.
- `_record_webauthn_failure(...)` — writes the audit row **directly**, bypassing the shared `core.audit.audit_log` helper — comment explains that helper's `metadata` kwarg doesn't map to the real `audit_metadata` column and would silently drop detail (documenting the same audit-module mismatch flagged in §2).
- `_current_aggregate(...)` — recomputes an operator's active-policy spend aggregate by joining `IntentPolicy.webauthn_credential_id` against the operator's enrolled credentials — comment documents a prior bug where this incorrectly filtered by `merchant_id == user_id` (worked only when there was exactly one operator per merchant).
- `_consume_and_resume(...)` — after a policy is signed via a handoff, deliberately **never raises** if resuming the parked chat errand fails — the signing itself already succeeded and must not be rolled back; failure is only reported in the response payload.
- Routes: `GET /intent/studio` (signing/enrollment UI, or the amendment approval UI if `?token=` resolves to an `AMENDMENT` handoff), WebAuthn register/assertion begin/complete, `GET /internal/policy/blast-radius`, `POST /intent/amendment/{id}/approve` (requires a fresh WebAuthn assertion — R0.5, no self-approval), `POST /intent/amendment/{id}/reject` (no assertion required).

---

### `src/openstore/verify/checks.py`

The 14 fully-offline PoAI evidence-bundle verification checks (PRD §3.6) used by the `openstore-verify` CLI — no network access.

Exit codes: `EXIT_OK=0`, `EXIT_FAIL=1`, `EXIT_MALFORMED=2`, `EXIT_UNSUPPORTED_COMPILER_DIGEST=3`, `EXIT_USAGE=4`.

The 14 checks, run in fixed order: `check_schema`, `check_chain_integrity`, `check_merchant_signature`, `check_time_anchor`, `check_webauthn_assertion`, `check_challenge_binding`, `check_uv_flag`, `check_catalog_attestations`, `check_compiler_digest`, `check_re_execution`, `check_amount_consistency`, `check_aal`, `check_delegation_chain`, `check_spend_chain`.

**Documented limitations (from the code's own docstrings/comments — not third-party observations):**
- `check_webauthn_assertion` / `check_catalog_attestations` — structural validation only (base64url/shape), no actual cryptographic signature verification (would require a public key not present in the bundle).
- `check_re_execution` — **does not actually re-execute `compile_decision`**; only checks the transcript's shape and the recorded verdict. Docstring: "In a full verifier, the transcript would be replayed through compile_decision()."
- `check_compiler_digest`'s `KNOWN_COMPILER_DIGESTS` is a single hardcoded hash equal to `SHA256("")` — a placeholder, not a real pinned compiler binary digest.
- `check_delegation_chain` / `check_spend_chain` — explicitly MVP/structural-only, no cryptographic chain verification.
- `check_merchant_signature` — skips (passes with detail `"unverified_no_jwks"`) rather than fails, whenever no `--merchant-jwks` directory is supplied.

---

### `src/openstore/verify/cli.py`

The `openstore-verify` Typer CLI (registered as a console script in `pyproject.toml`) — offline PoAI bundle verifier entry point.

- `verify(bundle_path, --json, --merchant-jwks, --detect-forks)` — loads the bundle, runs all 14 checks, computes the exit code, prints either human-readable or `--json` output, and (if `--detect-forks <dir>` given) scans a directory of sibling bundles for the same `(envelope_id, sequence)` appearing more than once (a fork/double-spend proof).
- `_load_jwks(path)` — **effectively dead code**: despite its name, it never returns parsed JWKS content (only does an existence/is-file check and always returns `None`) — the real per-file JWK lookup happens inline inside `checks.check_merchant_signature` via `jwks_dir.glob("*.json")`, duplicating the "must exist" validation that `verify()` also performs.

---

## 7. Agents, CLI, server

### `src/openstore/agents/llm.py`

Pluggable LLM provider interface.

- `LLMProvider` (base) — `complete()`/`chat()` both `raise NotImplementedError`.
- `OpenAIProvider` — real HTTP client (`httpx`), POSTs to `{base_url}/chat/completions` (OpenAI-compatible shape). **Does not implement `complete()`** — only `chat()` is functional.
- `DummyProvider` — offline; `chat()` always returns the fixed string `'{"answer": "This is a test response from DummyProvider."}'`, ignoring its input entirely.
- `create_llm(config) -> LLMProvider` — provider selection: `provider_name = os.getenv("LLM_PROVIDER", "dummy")` **defaults to `"dummy"`** (no network call unless an operator explicitly sets `LLM_PROVIDER`). **Exception:** if `provider_name` isn't a registered key but the model name contains `"gpt"` or `"claude"`, it still auto-selects `OpenAIProvider` (and thus calls out to `https://api.openai.com/v1` by default) even without `LLM_PROVIDER` being set.
- `llm_complete(...)` — **always raises `NotImplementedError`** with either built-in provider, since neither implements `complete()`.

---

### `src/openstore/agents/campaign_agent.py`

**The one place in the codebase that makes an actual LLM call** (via `llm_chat`, subject to the provider-selection rules above — offline/`DummyProvider` by default).

- `CampaignAgent.draft_campaign(session, merchant_id, calendar_event=None)` — reads *only* `get_analytics_view` (INV-14, no raw PII), computes top-5/bottom-5 SKUs by 30d units and a `headroom_minor = max(0, 200000 - gross_minor)` (hardcoded 200000 target), prompts an LLM with "You are a campaign strategist. Respond with valid JSON only.", and shape-validates the JSON response (`title: str`, `discount_bps: int` required; `applies_to_skus` defaults to `top_skus` if omitted).
- **With the default `DummyProvider`, this always fails**: the dummy's fixed `{"answer": "..."}` response has no `title`/`discount_bps` keys, so `draft_campaign` always returns `{"error": "missing_title"}` unless an operator has configured a real `LLM_PROVIDER`.
- No retry logic — a single malformed LLM response is terminal for that call. Deterministic validation (`validate_campaign`, `core/campaigns.py`) is not invoked here — this file only shape-checks the LLM output.

---

### `src/openstore/agents/buyer_agent.py`

Buyer-side planning/shopping loop plus the Discord bot front-end. **Entirely rule-based — no LLM call anywhere in this file.**

- `NEGOTIABLE_REASON_CODES = {"policy.tag_violation", "policy.sku_blocked", "policy.spend_per_tx_exceeded"}`; `MAX_NEGOTIATION_ROUNDS = 3`.
- `BuyerAgent.shop(...)` — plan → `create_cart` → if denied with a negotiable reason, loop (bounded to 3 rounds) calling `MerchantAgent.negotiate` and applying its `cart_delta` via `apply_cart_delta`, breaking immediately if a round produces no change (explicit anti-infinite-loop guard) → on success, `checkout_initiate` (stamping chat identity + `request_text` only if chat-originated).
- `compute_cart_hash(cart)` — deterministic SHA-256 over sorted, canonically-serialized cart items (enables server-side recomputation — R0.8).
- `BuyerBot` — Discord command parsing: `!shop <goal>` (any channel) / `shop <goal>` (DM only, no prefix); `!cancel <id>` / `cancel <id>` (DM only). Requires the buyer to have an active signed policy (`require_active_policy`) — if not, creates a `HandoffKind.POLICY` handoff and sends a signing link instead of proceeding. On a policy-reason denial, offers an amendment path (`MerchantAgent.draft_amendment` → `HandoffKind.AMENDMENT` handoff → signing link) — **never auto-applies** (R0.9, human-approval-gated).
- `_handle_cancel` — checks `checkout.chat_user_id == chat_user_id` before allowing cancellation (never leaks or cancels another user's checkout).

---

### `src/openstore/agents/merchant_agent.py`

Merchant-side reasoning: negotiation, amendment drafting, evidence narration. **Entirely rule-based — no LLM anywhere in this file.**

- `MerchantAgent.negotiate(cart, reason_code, trace_id, policy)` — a **static lookup table**, not a search/reasoning process: `policy.tag_violation` → `remove_violating_tags`; `policy.sku_blocked` → `swap_sku`; `policy.spend_per_tx_exceeded` → `reduce_qty`; anything else → `NO_COMPLIANT_PATH`.
- `draft_amendment(...)` — builds an unsigned (`approval` fields all `None`) `PENDING_APPROVAL` draft with a computed digest; explicit doc comment: "R0.5: NO self-approval. The human signs the amendment."
- `narrate(bundle, verifier_output=None)` — builds a plain-English cover note purely from string formatting (no LLM); appends an AAL-specific sentence (0: "No verifiable human authority exists; no order is created at this level." … 3: strongest evidence language).
- `apply_cart_delta(cart, cart_delta, policy)` — the mechanical executor for the three flags `negotiate()` can produce (`remove_violating_tags`, `swap_sku` — literally drops the blocked line item, no substitution catalog exists — and `reduce_qty`, which pops items from the end of a SKU-sorted copy until under cap). Any unrecognized flag is a no-op that returns the **original, uncopied** `cart` object (an asymmetry versus the other three branches, which return new lists — not currently exploited as a bug since callers compare by equality, not identity).

---

### `src/openstore/agents/mcp_client.py`

`InProcessMCPClient` — a real (non-HTTP, non-stub) in-process MCP client used by `BuyerAgent`: mints a genuine OAuth token (`BUYER_SCOPES = ["catalog:read","cart:write","checkout:initiate","checkout:confirm","order:read"]`) and dispatches tool calls directly into `mcp_server.handle_mcp_request` within the same process. Uses `session_scope` (commit-on-success/rollback-on-error) rather than a plain session+close, with an explicit comment that the latter "silently drops every write". No token refresh/expiry retry logic — the cached token is reused for the client's lifetime.

---

### `src/openstore/agents/__init__.py`

Empty (0 bytes) — package marker only.

---

### `src/openstore/cli.py`

Typer CLI (`openstore` command).

- `openstore init --merchant NAME [--currency INR] [--output DIR] [--deployment same-origin|subdomain] [--public-base-url URL]` — scaffolds `gelateria.yaml` (config, via `build_config_dict` + `yaml.safe_dump`), `.env.example`, `catalog.yaml` (5 demo SKUs). Fails (`exit 2`) if `--deployment subdomain` is chosen without `--public-base-url`. **Note:** the output config filename is always literally `gelateria.yaml`, never parameterized by `--merchant`.
- `openstore serve CONFIG_PATH [--host 0.0.0.0] [--port 8000]` — boot order: `load_config` → `apply_migrations` → (lazy import to avoid circularity) `create_app` → `uvicorn.run(..., log_config=None)`.

---

### `src/openstore/server.py`

FastAPI application factory.

- **SID-5 origin check, at `create_app()` call time (fail-loud, no silent fallback):** if `config.public_base_url` is set, its hostname must match `config.webauthn.rp_id`, or `create_app` **raises `ValueError`** — the server refuses to start with a mismatched config.
- **CORS**: single allowed origin (`config.public_base_url` or `config.webauthn.origin`), not `*`; `allow_credentials=True`.
- **Readiness-gating middleware**: for any request whose path is in `health.GATED_PATHS`, returns `503 {"error":"service_unavailable","reason_codes":[...]}` if `not is_ready(config)`.
- **Lifespan**: registers the running event loop with `notifier.set_main_loop`; sets `psp.router`'s module config (soft-fails with a logged warning, doesn't abort startup); if a real Discord bot token is configured, constructs exactly one `discord.Client` (with the privileged `message_content` intent enabled), registers it via `notifier.set_discord_client`, wires up `BuyerBot(BuyerAgent(InProcessMCPClient(config)))`, and starts it as a background task — **also soft-fails** (logged warning, server still comes up without Discord). Unconditionally starts `_hold_release_loop` as a background task.
- **`_hold_release_loop`** (every 30s): DMs chat-originated `HELD` checkouts within 120s of expiry ("hold expires in under 2 minutes"), then calls `hold_release_worker_tick_with_notifications` and DMs anyone whose hold was just auto-released/cancelled. Comment: this loop is what makes `hold_release_worker_tick` actually get called — it previously had zero callers.
- **Routes defined directly here**: `/healthz`, `/health/live`, `/health/ready`, `/internal/metrics`, the six `.well-known/*` manifest endpoints, `/agent/catalog`, `POST /agent/mcp` (bearer-token validation **swallows all exceptions**, silently degrading to `client_id="anonymous"`/no scopes rather than a 401 — downstream scope checks in `mcp_server.py` are the actual enforcement point), `POST /agent/acp` (stub, always `{"error": "not implemented"}`), `/agent/campaigns` (duplicate implementation of `/.well-known/agent-campaigns.json`, same underlying call), `POST /campaign/{id}/approve|reject` (both return errors as **200-status JSON with an `"error"` key**, not a non-2xx status), `/campaign/studio` and `/` (both marked "stub" in comments, serving static HTML files).
- **Mounted routers** (in this order, no explicit prefixes passed): `psp_router`, `policy_studio_router` (comment: "previously built but never mounted" — i.e. this mount was itself a fix, not new functionality), `evidence_router`.

---

## 8. Cross-cutting findings

These span multiple modules and are worth knowing before touching related code.

1. **`core/aal.py` does not exist.** It was deleted in commit `cf7ca0a` — the AAL ladder now lives entirely in `core/holdcancel.py` and is re-exported from `core/__init__.py` under the expected names (`AALLevel`, `compute_aal_level`, etc.), so callers importing from `openstore.core` are unaffected, but a direct `from openstore.core.aal import ...` would fail.

2. **Two `AALLevel` classes with the same name, different types.** `holdcancel.AALLevel` is an `IntEnum` (used throughout `core/api.py`, ledger/hold logic). `poai.AALLevel` is a plain `int` subclass with its own `__str__`. They are not interchangeable and not the same object. Similarly, **two separately-implemented `get_aal_liability_sentence` functions** exist (`holdcancel.py`'s version is the one re-exported from `core/__init__.py`; `poai.py`'s is a second, unexported definition with different wording).

3. **Two independent webhook pipelines.** `core/webhooks.py` (`process_webhook_event`, dispatching on `payment.captured`/`payment.failed`, driven by `core/api.py`/redteam tests) and `psp/razorpay_driver.py` (`process_incoming_webhook`/`_dispatch_webhook_event`, dispatching on `payment_link.paid`/`cancelled`/`partially_paid`/`payment.failed`, driven by `psp/router.py`, which is what the live `/webhooks/razorpay` route actually uses) are separate implementations with different event-type vocabularies. `core/webhooks.py` appears to be an earlier/parallel implementation not wired into the live webhook route.

4. **`audit_log`'s `metadata` kwarg vs. the `audit_metadata` column.** `core/audit.py::audit_log(..., metadata=...)` stores into `AuditLog.audit_metadata`. At least one call site (`surfaces/studio.py::_record_webauthn_failure`) explicitly bypasses the shared `audit_log` helper and writes the `AuditLog` row directly, citing this exact mismatch as the reason.

5. **`refund_checkout` in `psp/razorpay_driver.py` likely passes the wrong id to Razorpay** — `client.payment.refund(payment_id, ...)` is called with `checkout.psp_payment_link_id` (a payment-*link* id), not a Razorpay payment id, which that endpoint expects. No payment_link→payment resolution exists in the file. Untested against live Razorpay in this pass — flagged as a probable real bug, not confirmed against the live API.

6. **PoAI signing keys and catalog-attestation keys are in-memory only (`wellknown.POAI_KEYS`), never persisted, no rotation** (`kid` hardcoded to `-key-1`). A process restart invalidates every previously issued attestation and OAuth-token signature for that merchant, since `core/oauth.py` also defaults to signing with this same keypair.

7. **`verify_poai_bundle` (core/poai.py) only checks hash-chain integrity — it does not verify the merchant JWS signature or the time anchor.** A bundle with a forged/absent `merchant_signature` but an internally-consistent hash chain still verifies `True` from this function. (The separate, more thorough `verify/checks.py::check_merchant_signature` used by the `openstore-verify` CLI *does* verify the signature, but only when a `--merchant-jwks` directory is supplied — otherwise it also skips with `"unverified_no_jwks"`.)

8. **The compiler's transcript position for `campaign_validity` differs between the pass and fail paths** (documented as intentional in `compiler.py`, but worth knowing if code parses the transcript positionally rather than by name).

9. **Broad `except Exception` around WebAuthn assertion verification** appears in three places with slightly different consequences: `core/api.py::create_checkout` and `::confirm_checkout` (silently degrade to "not verified" rather than surfacing the underlying error — and the two call sites use different assertion `binding` contexts), and `server.py`'s `/agent/mcp` bearer-token handler (silently degrades to anonymous/no-scope access rather than 401).

10. **The "platform fee" ledger leg (`core/ledger.py::create_capture_entry`) is a pure stub** — always 0, no fee calculation exists anywhere in the codebase.

11. **LLM usage is narrower than the word "agent" suggests.** Of the four "agents", only `CampaignAgent` makes an actual LLM call (via `agents/llm.py::llm_chat`), and even that defaults to a fully offline `DummyProvider` unless an operator sets `LLM_PROVIDER` (or the configured model name happens to contain `"gpt"`/`"claude"`, which auto-selects the real HTTP-calling `OpenAIProvider` even without `LLM_PROVIDER` set). `BuyerAgent`/`BuyerBot` and `MerchantAgent` (negotiation, amendment drafting, narration) are 100% deterministic rule-based Python — no LLM calls anywhere in either file. `llm_complete`/`LLMProvider.complete()` is unimplemented by both concrete providers and always raises `NotImplementedError` if called.

12. **Health/readiness has a side effect.** `core/health.py::_signing_keys_ready` (used by `compute_readiness`/`is_ready`, which gates all of `health.GATED_PATHS`) calls `wellknown._load_or_generate_poai_keys`, which **generates signing key material if it doesn't already exist** — meaning hitting the readiness endpoint can itself trigger key generation as a side effect.
