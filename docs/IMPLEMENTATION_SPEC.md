# OpenStore PoAI — Implementation Contract v0.1

Normative build specification for the Proof-of-Authorized-Intent evidence layer and its four
front-facing surfaces. The *argument* for why any of this exists is in
`docs/PROOF_CARRYING_COMMERCE.md`; this document is the contract.

---

## 0. Rules for the implementer

These rules bind whoever builds this, human or model. Read them before §1.

**R0.1** — The key words MUST, MUST NOT, SHOULD, MAY are used as in RFC 2119.

**R0.2 — Never invent an identifier.** Field names, reason codes, route paths, function
signatures, table names, column names, exit codes and CLI flags are **closed sets** defined in
this document. If a name you need is not here, it does not exist. Do not pluralise, abbreviate,
re-case, or "improve" a name defined here.

**R0.3 — Never invent a value.** Every enum in this document is exhaustive. A value outside an
enum MUST cause a hard error, never a fallback branch.

**R0.4 — When this document is silent, stop and ask.** §11 lists the questions already known to
be open. If you hit a decision that is not answered here and not in §11, add it to §11 and ask
the repository owner. Do **not** choose a default and proceed. A wrong guess in an evidence
format is unrecoverable: it silently invalidates every bundle already issued.

**R0.5 — Fail loud.** No `except Exception: pass`, no silent coercion, no defaulting a missing
required field. Every rejection carries a reason code from §3.4 or §7.4.

**R0.6 — Do not change existing behaviour that this document does not mention.** The legacy
OTP/mandate path (`merchant/mandate.py`, `merchant/internal_routes.py`, `checkout_confirm`
Path B) keeps EdDSA and stays exactly as it is. This spec adds a parallel layer; it does not
refactor the old one.

**R0.7 — Prerequisite.** `docs/PRODUCTION_READINESS.md` §0.1 and §0.2 MUST be fixed before any
work in this document begins. Until `verify_assertion_policy()` is called on the money path and
the compiler runs over the frozen snapshot, every bundle this layer produces is AAL0 and the
whole layer is decorative.

### 0.1 Global encoding rules

| Concern | Rule |
|---|---|
| Money | Integer minor units (paise). Field name MUST end `_minor`. Floats MUST NOT appear in any structure that is canonicalised, hashed, or signed. |
| Currency | ISO 4217 uppercase. v0.1 supports `"INR"` only; any other value MUST be rejected with `currency_mismatch`. |
| Timestamps (bundle, DB, HTTP) | RFC 3339, UTC, `Z` suffix, **second precision**, e.g. `2026-08-27T09:14:22Z`. Field name MUST end `_at`. Produce with `datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")`. |
| Timestamps (inside `IntentPolicy` only) | **Integer Unix seconds**, because already-signed policies use that shape. `IntentPolicy.not_before` and `IntentPolicy.expires_at` are ints. This is the one exception; do not "normalise" it. |
| Binary | base64url **without** padding, alphabet `[A-Za-z0-9_-]`. Reuse `merchant.webauthn.b64url_encode` / `b64url_decode`. |
| Digests | String `"sha256:"` + 64 lowercase hex chars. Never raw hex, never bytes, never uppercase. |
| Object keys in canonicalised structures | MUST match `^[a-z][a-z0-9_]*$`. This constraint exists so that byte-order and code-point-order key sorting are provably identical; do not relax it. |
| Identifiers | `bundle_id` = `"poai_"` + 26 uppercase Crockford base32 chars (ULID). `checkout_id` = UUID4 string, unchanged from today. |

### 0.2 Files this spec creates

```
merchant/
  compiler.py            # NEW — the pure decision procedure (§3)
  attest.py              # NEW — catalog attestation (§4)
  aal.py                 # NEW — predicates + tier resolution (§5)
  evidence.py            # NEW — bundle assembly, chain, signing, anchoring (§6)
  blast_radius.py        # NEW — Policy Studio engine (§9.1)
  agent_console.py       # NEW — session/rejection queries (§9.2)
  hold.py                # NEW — HELD state machine (§9.3)
  keys/poai_es256.pem    # NEW — P-256 private key, gitignored
  storefront/
    intent_studio.html   # NEW (§9.1)
    agents_console.html  # NEW (§9.2)
    agents_public.html   # NEW (§9.5)
openstore_verify/        # NEW — standalone package, MUST NOT import merchant.* (§7)
  __init__.py  canonical.py  chain.py  webauthn.py  compiler.py  aal.py  cli.py
  schema/poai-0.1.schema.json
  viewer.html            # single-file offline verifier (§9.4)
tests/
  test_compiler_properties.py  test_aal.py  test_evidence.py
  test_verifier.py  test_surfaces.py  test_golden_vectors.py
  fixtures/golden/*.json
```

`merchant/intent_compiler.py` is **retained unchanged** as the legacy entry point and MUST be
reduced to a thin wrapper that calls `merchant.compiler.compile_decision`. Its existing public
name `verify_cart_against_policy` MUST keep working so nothing else breaks.

---

## 1. Cryptographic primitives

### 1.1 Canonical JSON — `canonical_json_bytes(obj) -> bytes`

Lives in `openstore_verify/canonical.py`; `merchant/evidence.py` MUST import a byte-identical
implementation (duplicate the file — the verifier may not import `merchant`; `tests/test_golden_vectors.py`
asserts the two agree).

Algorithm, in exactly this order:

1. If `obj` is a `float`, `bool` inside a money field, `NaN`, `Infinity`, or a Python object
   that is not `dict | list | str | int | bool | None` → raise `ValueError("noncanonical_type")`.
2. Recursively normalise every **string** (keys and values) to Unicode NFC via
   `unicodedata.normalize("NFC", s)`.
3. Assert every object key matches `^[a-z][a-z0-9_]*$` → else `ValueError("noncanonical_key")`.
4. Serialise with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
   allow_nan=False)`.
5. Encode UTF-8. Return the bytes. No trailing newline.

`digest(obj) -> str` returns `"sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()`.

> This is **not** RFC 8785 (JCS). It is a restricted profile that coincides with JCS for the
> value space this spec permits. Documentation MUST describe it as "canonical JSON (sorted keys,
> tight separators, NFC strings, integer-only numbers)" and MUST NOT cite RFC 8785 as implemented.

### 1.2 Signature suite

| Layer | Algorithm | Key | Why |
|---|---|---|---|
| PoAI: merchant root signature, catalog attestations | **ES256** (ECDSA P-256 + SHA-256), JWS Compact | `merchant/keys/poai_es256.pem` | `crypto.subtle` verifies P-256 natively → the offline HTML viewer (§9.4) needs zero dependencies |
| PoAI: accepted WebAuthn credential algorithm | **ES256 (COSE alg -7) only** | authenticator | same reason |
| Legacy mandate path | EdDSA, unchanged | `merchant_signing_key.pem` | R0.6 |

**R1.2a** — A WebAuthn credential whose COSE `alg` is not `-7` MUST be rejected at **enrolment**
with HTTP 400 `webauthn_unsupported_alg`, so an unusable credential never reaches the money path.

**R1.2b** — Every PoAI JWS header MUST be exactly `{"alg":"ES256","kid":"<kid>"}`. Verifiers MUST
allowlist `alg`; `none` and any other value MUST be rejected.

**R1.2c** — The public key is published at `GET /.well-known/poai-jwks.json` as a JWKS with one
key: `{"kty":"EC","crv":"P-256","x":"<b64u>","y":"<b64u>","kid":"<kid>","alg":"ES256","use":"sig"}`.
This is a **new** endpoint and MUST NOT be confused with the OAuth `jwks_uri`.

### 1.3 The hash chain

```python
SECTION_ORDER = ("transaction", "human_intent", "authority", "goods",
                 "agent", "adjudication", "notification", "aal")
```

**R1.3a** — All eight keys MUST be present in the bundle. A section that does not apply MUST be
JSON `null`, never absent. This removes every ambiguity about how to hash a missing section.

**R1.3b** — Construction, where `||` is raw byte concatenation:

```
c_i    = canonical_json_bytes(bundle[SECTION_ORDER[i]])      # for null this is b"null"
link_0 = SHA256(c_0)                                          # 32 raw bytes
link_i = SHA256(link_{i-1} || c_i)          for i = 1..7
root   = link_7
```

**R1.3c** — `bundle["chain"]["links"]` is a list of exactly 8 strings in `SECTION_ORDER` order,
each `"sha256:" + hex(link_i)`. `bundle["chain"]["root"]` equals `links[7]`.

**R1.3d** — `bundle["chain"]["merchant_signature"]` is a JWS Compact (ES256) whose payload is
exactly:

```json
{"bundle_id": "poai_…", "issued_at": "2026-08-27T09:15:04Z", "root": "sha256:…"}
```

The `chain` object itself is **not** part of the chain (it contains the chain), which is why
`SECTION_ORDER` has eight entries and not nine.

---

## 2. Data model additions

Append to `merchant/models.py`. Do not modify existing tables except where stated.

```python
class CatalogAttestation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True)
    price_minor: int
    tags_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    catalog_digest: str                 # "sha256:…" of the whole catalog at issue time
    jws_compact: str                    # ES256 JWS, payload per §4.1
    issued_at: datetime = Field(index=True)

class EvidenceBundle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bundle_id: str = Field(unique=True, index=True)
    checkout_id: str = Field(unique=True, index=True)
    aal_level: int = Field(index=True)          # 0..3
    root: str                                    # "sha256:…"
    bundle_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    anchor_type: str = Field(default="none")     # "rekor" | "merkle_daily" | "none"
    anchor_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    issued_at: datetime

class AgentSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_key: str = Field(unique=True, index=True)   # f"{client_id}:{policy_hash}"
    client_id: str = Field(index=True)
    policy_hash: str = Field(index=True)
    display_name: str
    frozen: bool = Field(default=False)
    first_seen_at: datetime
    last_seen_at: datetime = Field(index=True)

class HoldRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(unique=True, index=True)
    aal_level: int
    hold_seconds: int
    state: str = Field(default="HELD", index=True)   # HELD | RELEASED | CANCELLED
    holds_until_at: datetime = Field(index=True)
    cancel_token: str = Field(unique=True, index=True)  # 32-byte urlsafe, single use
    resolved_at: Optional[datetime] = None
```

**Additions to existing models** (additive only):

- `Checkout`: `cart_snapshot_json: list[dict]` (`Column(JSON)`) and `cart_version: int`. Per
  `PRODUCTION_READINESS.md` §0.2, the compiler runs over this snapshot, never over `Cart`.
- `IntentPolicyRow`: `policy_version: int = 1`.
- `AuditLogEntry`: `reason_code: Optional[str] = Field(default=None, index=True)` — the Agent
  Console rejection feed (§9.2) reads this column, so it MUST be populated on every rejection.
- `Order.status` gains the value `HELD` and `CANCELLED` (see §9.3 for the transition table).

**R2.1** — Use Alembic. `init_db()`'s `create_all` MUST NOT be relied on for these additions.

---

## 3. The decision procedure — `merchant/compiler.py`

### 3.1 Signature

```python
def compile_decision(
    items: tuple[CompilerItem, ...],
    policy: CompilerPolicy,
    context: CompilerContext,
) -> CompilerVerdict: ...
```

### 3.2 Input types — frozen dataclasses, exactly these fields

```python
@dataclass(frozen=True, slots=True)
class CompilerItem:
    sku: str
    qty: int
    unit_minor: int
    tags: tuple[str, ...]          # from the catalog attestation (§4), NOT from the live catalog

@dataclass(frozen=True, slots=True)
class CompilerPolicy:
    policy_version: int            # MUST be 2; see R3.2b
    merchant_id: str
    currency: str
    max_spend_per_tx_minor: int
    max_spend_total_minor: int
    max_transactions: int
    allowed_tags: tuple[str, ...]
    tag_mode: str                  # "all" | "any"
    blocked_skus: tuple[str, ...]
    not_before: int                # Unix seconds
    expires_at: int                # Unix seconds

@dataclass(frozen=True, slots=True)
class CompilerContext:
    merchant_id: str
    currency: str
    evaluated_at_unix: int         # injected; the compiler MUST NOT read a clock
    spent_minor: int               # already spent under this policy_hash
    transactions_count: int        # already completed under this policy_hash

@dataclass(frozen=True, slots=True)
class CompilerVerdict:
    verdict: str                   # "ALLOW" | "DENY"
    reason_code: Optional[str]     # None iff verdict == "ALLOW"
    transcript: tuple[dict, ...]
```

**R3.2a — Determinism.** `merchant/compiler.py` MUST NOT import or call: `time`, `datetime`,
`random`, `secrets`, `uuid`, `os`, any database session, any network client, or
`merchant.trace.emit`. `tests/test_compiler_properties.py::test_compiler_module_is_pure` asserts
this by AST walk, in the same style as `tests/test_isolation.py`.

**R3.2b — Policy versioning.** `compile_decision` accepts `policy_version == 2` only. A
`policy_version` of 1 (the shape already in `merchant/models.py::IntentPolicy`) MUST be rejected
by raising `LegacyPolicyError`. There is **no** automatic v1→v2 mapping — inferring
`max_spend_total_minor` from `max_spend_minor` would silently change what a human signed. A
bundle whose policy is v1 is AAL1 with reason `policy_schema_legacy` (§5.3).

**R3.2c** — `IntentPolicy` in `merchant/models.py` gains the v2 fields with
`policy_version: int = 2`, and the v1 field `max_spend_minor` is **kept** so existing signed
policies still deserialise. New signings emit v2.

### 3.3 Check order — normative

Evaluate in exactly this order. **Stop at the first failure**; the transcript ends there. Each
check appends exactly one transcript entry.

| # | `check` | Passes iff | `reason_code` on failure |
|---|---|---|---|
| 1 | `currency_match` | `policy.currency == context.currency == "INR"` | `currency_mismatch` |
| 2 | `merchant_lock` | `policy.merchant_id == context.merchant_id` | `merchant_mismatch` |
| 3 | `policy_not_before` | `context.evaluated_at_unix >= policy.not_before` | `policy_not_yet_valid` |
| 4 | `policy_expiry` | `context.evaluated_at_unix < policy.expires_at` | `policy_expired` |
| 5 | `transaction_count` | `context.transactions_count + 1 <= policy.max_transactions` | `tx_count_exceeded` |
| 6 | `item_qty` | for each item: `isinstance(qty, int) and qty >= 1` | `qty_invalid` |
| 7 | `item_blocked_sku` | for each item: `sku not in policy.blocked_skus` | `sku_blocked` |
| 8 | `item_tag_allowlist` | for each item, per R3.3c | `tag_violation` |
| 9 | `spend_per_tx` | `total_minor <= policy.max_spend_per_tx_minor` | `spend_per_tx_exceeded` |
| 10 | `spend_cumulative` | `context.spent_minor + total_minor <= policy.max_spend_total_minor` | `spend_cumulative_exceeded` |

where `total_minor = sum(i.unit_minor * i.qty for i in items)`.

**R3.3a — Item ordering.** `items` MUST be sorted by `sku` ascending before the call. Checks 6–8
run over items in that order: check 6 for all items, then check 7 for all items, then check 8 —
**not** all three checks per item. This ordering is part of the digest.

**R3.3b — Duplicates.** Two items with the same `sku` MUST cause `DENY` / `sku_duplicate` at
check 6, before `item_qty` passes. Callers merge quantities before calling.

**R3.3c — Tag semantics** (`policy.tag_mode`, and this is the exact definition; §0.8 of
`PRODUCTION_READINESS.md` flagged the old behaviour as under-specified):

- `"all"` (**default for new policies**) — passes iff `set(item.tags) <= set(policy.allowed_tags)`.
  Every tag the item carries must be one the human permitted. An item tagged
  `["vegan","alcohol"]` FAILS a `["vegan"]` policy.
- `"any"` — passes iff `set(item.tags) & set(policy.allowed_tags)` is non-empty. This is the
  legacy behaviour and MUST be selected explicitly.
- If `policy.allowed_tags` is empty, the check passes for every item under **both** modes
  (an empty allowlist means "unconstrained by tags"). This is deliberate and is encoded in the
  digest as `empty_allowed_tags_semantics: "unconstrained"`.

**R3.3d** — An empty `items` tuple MUST raise `ValueError("empty_cart")`, not return ALLOW.

### 3.4 Reason codes — exhaustive

```
currency_mismatch  merchant_mismatch  policy_not_yet_valid  policy_expired
tx_count_exceeded  qty_invalid  sku_blocked  sku_duplicate  tag_violation
spend_per_tx_exceeded  spend_cumulative_exceeded
```

Eleven values. No others exist. These are the compiler's codes; §7.4 defines the verifier's, and
they are a disjoint set.

### 3.5 Transcript entries — exact shapes

Every entry has `check` (str) and `result` (`"pass"` | `"fail"`) plus exactly the fields below.
No extra fields. Integer values stay integers.

```jsonc
{"check":"currency_match",     "result":"pass","policy_currency":"INR","context_currency":"INR"}
{"check":"merchant_lock",      "result":"pass","expected":"gelateria-roma","actual":"gelateria-roma"}
{"check":"policy_not_before",  "result":"pass","not_before":1787000000,"evaluated_at":1787000900}
{"check":"policy_expiry",      "result":"pass","expires_at":1788000000,"evaluated_at":1787000900}
{"check":"transaction_count",  "result":"pass","completed":2,"limit":10}
{"check":"item_qty",           "result":"pass","sku":"GEL-VAN-500","qty":2}
{"check":"item_blocked_sku",   "result":"pass","sku":"GEL-VAN-500"}
{"check":"item_tag_allowlist", "result":"pass","sku":"GEL-VAN-500","mode":"all",
                               "item_tags":["dairy-free","vegan"],"allowed":["dairy-free","vegan"]}
{"check":"spend_per_tx",       "result":"pass","total_minor":42000,"limit_minor":50000}
{"check":"spend_cumulative",   "result":"pass","spent_minor":12000,"total_minor":42000,"limit_minor":100000}
```

**R3.5a** — `item_tags` and `allowed` are sorted ascending. All lists in a transcript are sorted
unless the field name says otherwise.

**R3.5b** — A failing entry has the identical field set with `"result":"fail"`. The
`reason_code` lives on the verdict, not on the entry.

### 3.6 `COMPILER_DIGEST` — the pinned semantics

Both implementations MUST declare this object verbatim and derive the digest from it:

```python
COMPILER_SPEC = {
  "compiler_version": "1.0.0",
  "check_order": ["currency_match","merchant_lock","policy_not_before","policy_expiry",
                  "transaction_count","item_qty","item_blocked_sku","item_tag_allowlist",
                  "spend_per_tx","spend_cumulative"],
  "reason_codes": ["currency_mismatch","merchant_mismatch","policy_not_yet_valid","policy_expired",
                   "tx_count_exceeded","qty_invalid","sku_blocked","sku_duplicate","tag_violation",
                   "spend_per_tx_exceeded","spend_cumulative_exceeded"],
  "tag_modes": ["all","any"],
  "item_iteration_order": "sku_ascending",
  "money_unit": "minor_integer",
  "empty_allowed_tags_semantics": "unconstrained",
}
COMPILER_DIGEST = digest(COMPILER_SPEC)
```

**The digest of the object above is exactly:**

```
sha256:96543354c15751ccfdd7700c1cf9d1e0735559cdc166313186d593adcee9b03b
```

**R3.6a** — `tests/test_golden_vectors.py::test_compiler_digest_is_pinned` MUST assert that
literal string. If a change to semantics changes the digest, that is correct and intended — bump
`compiler_version`, update the literal, and understand that every previously issued bundle now
refers to an older procedure that the verifier must still be able to run.

**R3.6b** — The verifier MUST refuse a bundle whose `adjudication.compiler_digest` it does not
implement, with exit code 3 and message `unsupported_compiler_digest`. It MUST NOT re-adjudicate
under different rules. Silent semantic drift is the one failure mode that destroys an evidence
format.

---

## 4. Catalog attestation — `merchant/attest.py`

### 4.1 Payload

Issued once per `(sku, price_minor, tags)` combination, at or before cart creation.

```json
{"sku":"GEL-VAN-500","price_minor":21000,"tags":["dairy-free","vegan"],
 "catalog_digest":"sha256:…","merchant_id":"gelateria-roma","iat":1787000000}
```

`tags` sorted ascending. `catalog_digest` = `digest()` of the full normalised catalog:
`{"items":[{"sku":…,"price_minor":…,"tags":[sorted]} … sorted by sku]}`.
`iat` is integer Unix seconds. Signed ES256 → JWS Compact.

### 4.2 Functions

```python
def compute_catalog_digest(products: list[Product]) -> str: ...
def attest_item(sku: str, price_minor: int, tags: list[str],
                catalog_digest: str, merchant_id: str, iat_unix: int) -> str: ...
def get_or_create_attestation(session, sku: str) -> CatalogAttestation: ...
def verify_attestation(jws_compact: str, jwks: dict) -> dict: ...   # returns payload, raises on failure
```

**R4.1** — `create_cart` and `update_cart` MUST call `get_or_create_attestation` for every line
item and MUST fail the call if an attestation cannot be produced. An unattested item can never
enter a cart.

**R4.2** — Reuse an existing row iff `sku`, `price_minor`, sorted `tags`, and `catalog_digest`
all match. Any change mints a new attestation with a new `iat`.

**R4.3 — The freshness rule.** An attestation is valid evidence for a checkout iff
`attestation.iat <= cart.created_at`. An attestation issued *after* the cart was created MUST
set predicate E6 false. This is what makes retroactive tag editing detectable, and it is the
whole point of §4.

---

## 5. AAL — `merchant/aal.py`

### 5.1 Predicates

```python
@dataclass(frozen=True, slots=True)
class Predicates:
    e1_agent_authenticated: bool
    e2_policy_signature_valid: bool
    e3_assertion_fresh: bool
    e4_user_verified: bool
    e5_cart_bound: bool
    e6_catalog_attested: bool
    e7_compiler_allow: bool
    e8_intent_recorded: bool
    e9_notified: bool
```

Exact definitions — each MUST be computable from the bundle alone, by a party with no access to
the merchant:

| Predicate | True iff |
|---|---|
| `e1_agent_authenticated` | `agent.client_id`, `agent.scopes` and `agent.token_jti` are all present and non-empty, and `"checkout:confirm" in agent.scopes` |
| `e2_policy_signature_valid` | the WebAuthn assertion in `authority.webauthn` verifies (ES256) against `authority.enrolment.public_key`, **and** the challenge inside `client_data_json` equals the value required by `authority.webauthn.challenge_binding` (§5.2) |
| `e3_assertion_fresh` | `adjudication.evaluated_at − authority.webauthn.signed_at <= authority.policy.assertion_max_age_seconds` |
| `e4_user_verified` | bit 2 (`0x04`, UV) is set in byte 32 of the decoded `authenticator_data` |
| `e5_cart_bound` | `authority.webauthn.challenge_binding.mode == "cart"` **and** its `cart_hash` equals `goods.cart_hash` |
| `e6_catalog_attested` | every item in `goods.items` has a `catalog_attestation` that verifies against the merchant JWKS, whose payload `sku`/`price_minor`/`tags` match the item exactly, and whose `iat <= transaction.cart_created_at` (R4.3) |
| `e7_compiler_allow` | the verifier's re-execution of `compile_decision` returns `ALLOW` **and** its transcript is byte-identical to `adjudication.transcript` under `canonical_json_bytes` |
| `e8_intent_recorded` | `human_intent` is not null, and `digest({"text": human_intent.request_text})` equals `human_intent.request_digest` |
| `e9_notified` | `notification` is not null, has a `sent_at`, and `receipt_digest` is a well-formed digest |

**R5.1a** — `authority.enrolment.public_key` is the COSE public key, base64url, exactly as stored
in `IntentPolicyRow.public_key`. It MUST be embedded in the bundle; a verifier cannot call the
merchant to fetch it.

**R5.1b** — `e3` requires `assertion_max_age_seconds` on the policy. Add it to `IntentPolicy` v2.
Default for new policies: `86400`. It MUST be signed as part of the policy.

### 5.2 Challenge binding

`authority.webauthn.challenge_binding` is exactly one of:

```json
{"mode": "policy", "policy_hash": "sha256:…"}
{"mode": "cart",   "policy_hash": "sha256:…", "cart_hash": "sha256:…"}
```

- `mode: "policy"` — the signed challenge equals the stored `PolicyChallenge.challenge_id` whose
  `policy_hash` matches. This is today's ceremony. Reaches AAL2.
- `mode: "cart"` — the signed challenge is a nonce whose `PolicyChallenge` row carries **both**
  `policy_hash` and `cart_hash`. This is the step-up ceremony (§8, `/intent/step-up`). Reaches AAL3.

**R5.2** — `PolicyChallenge` gains a nullable `cart_hash: Optional[str]` column. `mode` is derived
from whether it is set — it MUST NOT be a client-supplied field.

### 5.3 Tier resolution — first match wins, evaluate top to bottom

```python
def resolve_aal(p: Predicates, policy_version: int) -> tuple[int, tuple[str, ...]]:
```

| Order | Condition | Level | `reasons` |
|---|---|---|---|
| 1 | `not p.e2` | **0** | `("policy_signature_invalid",)` |
| 2 | `not p.e7` | **0** | `("compiler_denied_or_transcript_mismatch",)` |
| 3 | `not p.e1` | **0** | `("agent_unauthenticated",)` |
| 4 | `policy_version != 2` | **1** | `("policy_schema_legacy",)` |
| 5 | `not p.e3` | **1** | `("assertion_stale",)` |
| 6 | `not p.e6` | **1** | `("catalog_unattested",)` |
| 7 | `not p.e4` | **1** | `("user_not_verified",)` |
| 8 | `not (p.e8 and p.e9)` | **1** | subset of `("intent_unrecorded","notification_missing")`, in that order |
| 9 | `p.e5` | **3** | `()` |
| 10 | otherwise | **2** | `("no_per_transaction_binding",)` |

**R5.3a** — `reasons` is a tuple of strings from the closed set:
`policy_signature_invalid`, `compiler_denied_or_transcript_mismatch`, `agent_unauthenticated`,
`policy_schema_legacy`, `assertion_stale`, `catalog_unattested`, `user_not_verified`,
`intent_unrecorded`, `notification_missing`, `no_per_transaction_binding`. Ten values.

**R5.3b** — `resolve_aal` MUST be a pure function of its two arguments and MUST be implemented
identically in `merchant/aal.py` and `openstore_verify/aal.py`.
`tests/test_aal.py::test_all_512_predicate_combinations` enumerates all 2⁹ predicate tuples × both
policy versions and asserts the two implementations agree on every one.

**R5.3c — Liability wording.** The tier→liability mapping is a **proposal**, not a rule. Any UI
or CLI that displays it MUST use the exact string
`"Proposed liability position (not a network rule): …"`. Do not soften this.

---

## 6. The bundle — `merchant/evidence.py`

### 6.1 Assembly

```python
def build_bundle(session, checkout_id: str) -> dict: ...
def sign_and_anchor(bundle: dict) -> dict: ...     # fills bundle["chain"]
def persist_bundle(session, bundle: dict) -> EvidenceBundle: ...
```

`build_bundle` MUST be called **after** the Razorpay order exists and **inside** the same
transaction that writes the `Order` row.

### 6.2 Field reference

The canonical example with every field is in `docs/PROOF_CARRYING_COMMERCE.md` §2.3. This table
is normative for types and optionality. `R` = required, `N` = nullable-but-present (R1.3a).

| Path | Type | R/N | Notes |
|---|---|---|---|
| `poai_version` | str | R | `"0.1"` exactly |
| `bundle_id` | str | R | §0.1 |
| `transaction.merchant_id` | str | R | |
| `transaction.checkout_id` | str | R | |
| `transaction.cart_created_at` | str | R | RFC3339; used by R4.3 |
| `transaction.amount_minor` | int | R | MUST equal `sum(unit_minor*qty)` over `goods.items` |
| `transaction.currency` | str | R | `"INR"` |
| `transaction.psp.provider` | str | R | `"razorpay"` |
| `transaction.psp.order_id` | str | R | |
| `transaction.psp.payment_link_id` | str | R | |
| `human_intent` | obj | N | null ⇒ E8 false |
| `human_intent.request_digest` | str | R | `digest({"text": request_text})` |
| `human_intent.request_text` | str | R | |
| `human_intent.captured_at` | str | R | |
| `human_intent.channel` | str | R | `"discord"` \| `"web"` |
| `human_intent.channel_message_id` | str | R | |
| `authority.policy` | obj | R | the v2 `IntentPolicy`, verbatim as signed |
| `authority.policy_hash` | str | R | `digest(authority.policy)` |
| `authority.webauthn.credential_id` | str | R | base64url |
| `authority.webauthn.client_data_json` | str | R | base64url |
| `authority.webauthn.authenticator_data` | str | R | base64url |
| `authority.webauthn.signature` | str | R | base64url |
| `authority.webauthn.uv` | bool | R | MUST match the decoded flag; a mismatch is a verifier failure |
| `authority.webauthn.sign_count` | int | R | |
| `authority.webauthn.signed_at` | str | R | |
| `authority.webauthn.challenge_binding` | obj | R | §5.2 |
| `authority.enrolment.public_key` | str | R | base64url COSE |
| `authority.enrolment.aaguid` | str | R | base64url; all-zero if unknown |
| `authority.enrolment.attestation_format` | str | R | `"none"` if not collected |
| `authority.enrolment.enrolled_at` | str | R | |
| `goods.cart_hash` | str | R | `compute_cart_hash(cart_snapshot_json)` |
| `goods.cart_version` | int | R | |
| `goods.items[]` | list | R | sorted by `sku` ascending, ≥1 entry |
| `goods.items[].sku` \| `.qty` \| `.unit_minor` | str/int/int | R | |
| `goods.items[].tags` | list[str] | R | sorted ascending |
| `goods.items[].catalog_attestation` | str | R | JWS Compact |
| `agent.client_id` \| `.display_name` \| `.token_jti` | str | R | |
| `agent.scopes` | list[str] | R | sorted ascending |
| `agent.consent_granted_at` | str | R | |
| `adjudication.compiler_version` | str | R | `"1.0.0"` |
| `adjudication.compiler_digest` | str | R | §3.6 |
| `adjudication.policy_schema_version` | int | R | |
| `adjudication.evaluated_at` | str | R | |
| `adjudication.context` | obj | R | `{spent_minor, transactions_count, currency, merchant_id, evaluated_at_unix}` — the exact `CompilerContext` |
| `adjudication.verdict` | str | R | `"ALLOW"` \| `"DENY"` |
| `adjudication.reason_code` | str | N | null iff ALLOW |
| `adjudication.transcript` | list[obj] | R | §3.5, verbatim |
| `notification` | obj | N | null ⇒ E9 false |
| `notification.sent_at` \| `.channel` \| `.receipt_digest` | str | R | |
| `aal.level` | int | R | 0–3 |
| `aal.predicates` | obj | R | nine keys `e1_…`–`e9_…`, bool |
| `aal.reasons` | list[str] | R | §5.3a, in resolution order |
| `chain.links` | list[str] | R | exactly 8 |
| `chain.root` | str | R | |
| `chain.merchant_signature` | str | R | JWS Compact, §1.3d |
| `chain.time_anchor` | obj | N | §6.3 |

**R6.2a** — `adjudication.context` is included **because** the verifier needs `spent_minor` and
`transactions_count` to re-run the compiler. Without it, re-execution is impossible. It is a
merchant claim, and the verifier MUST label the cumulative check as merchant-asserted in its
output (§7.5).

### 6.3 Time anchor

```json
{"type":"rekor","log_index":1234567,"entry_uuid":"…","inclusion_proof":{…},"anchored_at":"…"}
{"type":"merkle_daily","root":"sha256:…","path":["sha256:…"],"published_url":"https://…","anchored_at":"…"}
{"type":"none"}
```

**R6.3a** — Anchoring MUST be asynchronous and MUST NOT block `checkout_confirm`. Bundle is
persisted with `{"type":"none"}` and updated by a worker.

**R6.3b** — Anchor the **salted** root: submit `digest({"root": root, "salt": <16 random bytes b64u>})`
and store the salt in the bundle's anchor object as `salt`. Anchoring the bare root to a public
log leaks transaction timing and volume (§2.14 of the argument doc).

**R6.3c** — A missing or `"none"` anchor does **not** change the AAL tier in v0.1. It is reported
separately by the verifier as `time_anchor: absent`.

---

## 7. The verifier — `openstore_verify/`

### 7.1 Isolation

**R7.1** — `openstore_verify/` MUST NOT import `merchant`, `fastapi`, `sqlmodel`, `razorpay`, or
any network client. Permitted third-party dependency: `cryptography` (ES256 verification) and
`cbor2` (COSE key parsing). `tests/test_verifier.py::test_verifier_imports_nothing_from_merchant`
enforces this by AST walk, reusing the pattern in `tests/test_isolation.py`.

### 7.2 CLI contract

```
openstore-verify <bundle.json> [--merchant-jwks <path|url>] [--json] [--quiet]
```

- `<bundle.json>` — required positional.
- `--merchant-jwks` — optional. If omitted, the verifier uses `chain.merchant_signature`'s `kid`
  to look for an embedded `authority.enrolment` key only, and reports the merchant signature as
  `unverified_no_jwks` (which is a **warning**, not a failure — the human's assertion is the
  load-bearing signature, and it is embedded).
- `--json` — emit the machine result object (§7.5) instead of the human table.
- `--quiet` — exit code only.

**No network access is ever performed.** A `--merchant-jwks` value that looks like a URL MUST be
rejected with exit code 4 and `usage_error: jwks must be a local file`.

### 7.3 Check order

1. `schema` — validate against `openstore_verify/schema/poai-0.1.schema.json`
2. `chain_integrity` — recompute all 8 links and the root (§1.3)
3. `merchant_signature` — ES256 over the §1.3d payload; skipped-with-warning if no JWKS
4. `time_anchor` — well-formedness only; `absent` is reported, not failed
5. `webauthn_assertion` — ES256 verify `authenticator_data || SHA256(client_data_json)`
6. `challenge_binding` — challenge in `client_data_json` matches `challenge_binding` (§5.2)
7. `uv_flag` — decoded UV bit equals `authority.webauthn.uv`
8. `catalog_attestations` — per item, per E6
9. `compiler_digest` — must be implemented; else exit 3
10. `re_execution` — run `compile_decision`, compare verdict **and** byte-compare transcript
11. `amount_consistency` — `transaction.amount_minor == sum(unit_minor*qty)`
12. `aal` — recompute predicates and `resolve_aal`; compare with `aal.level` and `aal.reasons`

**R7.3** — Checks continue after a failure so the operator sees the complete picture; the exit
code reflects whether **any** check failed.

### 7.4 Verifier reason codes — exhaustive, disjoint from §3.4

```
schema_invalid  chain_broken  merchant_signature_invalid  merchant_signature_unverified_no_jwks
time_anchor_absent  time_anchor_malformed  webauthn_signature_invalid  webauthn_unsupported_alg
challenge_binding_mismatch  uv_flag_mismatch  attestation_invalid  attestation_stale
attestation_item_mismatch  unsupported_compiler_digest  transcript_mismatch  verdict_mismatch
amount_mismatch  aal_mismatch
```

**R7.4** — On `chain_broken`, the message MUST name the failing link index, the section, and — for
a single-field edit — the first differing JSON path. "Something changed" is not an acceptable
message; the tamper demo (§9.4) depends on this precision.

### 7.5 Output

Human output is the table shown in `docs/PROOF_CARRYING_COMMERCE.md` §2.3. `--json` emits:

```json
{"poai_version":"0.1","bundle_id":"poai_…","ok":true,
 "checks":[{"name":"chain_integrity","result":"pass","detail":"8 links, root sha256:…"}],
 "warnings":["merchant_signature_unverified_no_jwks"],
 "failures":[],
 "re_derived":{"verdict":"ALLOW","reason_code":null,"aal_level":2,
               "aal_reasons":["no_per_transaction_binding"]},
 "claimed":{"verdict":"ALLOW","aal_level":2},
 "merchant_asserted":["adjudication.context.spent_minor",
                      "adjudication.context.transactions_count"],
 "liability_note":"Proposed liability position (not a network rule): SHARED / ISSUER"}
```

**R7.5** — `merchant_asserted` MUST list every input the verifier could not independently
confirm. Overstating what was proven is the failure mode that would discredit the whole format.

### 7.6 Exit codes

| Code | Meaning |
|---|---|
| 0 | all checks passed (warnings allowed) |
| 1 | one or more checks failed |
| 2 | bundle malformed / unparseable / schema invalid |
| 3 | `unsupported_compiler_digest` |
| 4 | usage error |

---

## 8. HTTP surface

Auth column: `none` = public; `session` = merchant human session cookie (see R8.1); `bearer` =
OAuth access token with the named scope.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/.well-known/poai-jwks.json` | none | §1.2c |
| GET | `/.well-known/agent-policy.json` | none | §9.5 |
| GET | `/agents` | none | rendered merchant agent policy (§9.5) |
| GET | `/intent/studio` | session | Policy Studio page (§9.1) |
| POST | `/internal/policy/blast-radius` | session | `{policy}` → §9.1.2 response |
| POST | `/internal/webauthn/challenge` | session | existing; now session-gated (PROD_READINESS §0.5) |
| POST | `/internal/webauthn/register` | session | existing; now session-gated + R1.2a |
| POST | `/internal/webauthn/begin-signing` | session | existing; now session-gated |
| POST | `/internal/webauthn/complete-signing` | session | existing; now session-gated |
| POST | `/intent/step-up/begin` | bearer `checkout:confirm` | `{checkout_id}` → `{nonce, options}`; binds `cart_hash` (§5.2) |
| POST | `/intent/step-up/complete` | session | `{checkout_id, assertion}` → `{status:"signed"}` |
| GET | `/admin/agents` | session | Agent Console page (§9.2) |
| GET | `/internal/agents/sessions` | session | §9.2.1 |
| GET | `/internal/agents/rejections` | session | §9.2.2 |
| POST | `/internal/agents/freeze` | session | `{session_key, frozen: bool}` |
| GET | `/orders/{checkout_id}/evidence` | session | bundle JSON, `Content-Disposition: attachment` |
| GET | `/orders/{checkout_id}/evidence/view` | none | Evidence Viewer with the bundle inlined (§9.4) |
| POST | `/hold/{cancel_token}/cancel` | none | §9.3; the token *is* the credential, single-use |
| GET | `/hold/{cancel_token}` | none | confirmation page for the above |

**R8.1 — The merchant session.** Signed cookie `openstore_session`, `HttpOnly`, `SameSite=Strict`,
`Secure` when not on localhost, 12h TTL, established by `POST /admin/login` against a single
operator credential from `settings.merchant_operator_password_hash` (Argon2, the hasher already
in `internal_routes.py`). CSRF: double-submit token on every session-gated POST.
`tests/test_surfaces.py::test_no_internal_route_is_unauthenticated` asserts every route under
`/internal/` and `/admin/` rejects an anonymous request with 401 or 403.

**R8.2** — `/hold/{cancel_token}` is unauthenticated **by design** — the token is a 32-byte
`secrets.token_urlsafe` capability delivered only in the customer's notification, single-use,
and expires with the hold. Do not "improve" this by adding a login.

**R8.3** — All error responses use the envelope from `PRODUCTION_READINESS.md` §1.6:
`{"error":{"code":…,"message":…,"retriable":…,"trace_id":…,"details":{…}}}`.

---

## 9. The front-facing surfaces

### 9.1 Policy Studio — `/intent/studio`

#### 9.1.1 Engine — `merchant/blast_radius.py`

```python
def compute_blast_radius(policy: CompilerPolicy,
                         catalog: tuple[CompilerItem, ...],
                         evaluated_at_unix: int) -> dict: ...
```

**MUST be computed by calling `compile_decision`** — never by re-implementing the rules. That
identity is the point of the surface.

Algorithm, exactly:

1. For each product `p` in the catalog sorted by `sku` ascending: call `compile_decision` with
   `items=(CompilerItem(p.sku, 1, p.unit_minor, p.tags),)`, the given `policy`, and
   `CompilerContext(merchant_id=policy.merchant_id, currency=policy.currency,
   evaluated_at_unix=evaluated_at_unix, spent_minor=0, transactions_count=0)`.
2. `p` is **reachable** iff the verdict is `ALLOW`. Otherwise record the `reason_code` verbatim.
3. `reachable = [p for p in catalog if reachable(p)]`, preserving sku order.
4. `max_units_per_cart` = `policy.max_spend_per_tx_minor // min(unit_minor of reachable)`, or `0`
   if `reachable` is empty.
5. `worst_case_example_basket`: greedily, over `reachable` sorted by `unit_minor` **descending**
   then `sku` ascending, add one unit at a time while the running total stays
   `<= policy.max_spend_per_tx_minor`. Deterministic. It is an **example**, not a maximum.
6. `total_exposure_minor` = `policy.max_spend_total_minor`.
7. `policy_lifetime_seconds` = `max(0, policy.expires_at - evaluated_at_unix)`.

**R9.1a** — Step 5 produces an example. The UI MUST label it `"An example basket this policy
permits"` and MUST NOT call it the maximum or the worst case. A preview that overstates precision
is worse than none.

**R9.1b** — Step 1 uses `spent_minor=0`, so `spend_cumulative` never fails during preview. The UI
MUST show `total_exposure_minor` separately so the human still sees the cumulative bound.

#### 9.1.2 `POST /internal/policy/blast-radius`

Request: `{"policy": {…v2 IntentPolicy…}}`. Response:

```json
{"reachable":[{"sku":"GEL-VAN-500","name":"…","unit_minor":21000,"tags":["dairy-free","vegan"]}],
 "blocked":[{"sku":"GEL-RUM-500","name":"…","unit_minor":26000,"reason_code":"tag_violation"}],
 "summary":{"reachable_count":6,"catalog_count":12,
            "most_expensive_reachable_minor":26000,
            "max_units_per_cart":2,
            "per_tx_limit_minor":50000,
            "total_exposure_minor":200000,
            "policy_lifetime_seconds":604800},
 "worst_case_example_basket":[{"sku":"GEL-PIS-500","qty":1,"unit_minor":26000}],
 "compiler_digest":"sha256:9654…"}
```

**R9.1c** — `reason_code` values in `blocked` come from §3.4 verbatim. The page renders them with
a human gloss beside the code; it MUST show the code itself, because that is the same string the
verifier prints in a dispute.

#### 9.1.3 Page behaviour

Controls: per-transaction cap slider, total cap slider, `max_transactions`, tag checkboxes
(from the union of catalog tags), `tag_mode` radio (`all` default), blocked-SKU multiselect,
expiry date, `assertion_max_age_seconds`. Every change re-POSTs (debounce 250 ms) and re-badges
the grid. Signing is the existing ceremony; the **only** change is that the policy passed to
`/internal/webauthn/begin-signing` is the one currently in the Studio.

**R9.1d** — The diff panel compares against the currently active `IntentPolicyRow.policy_json`
and MUST render, in this order: products gained, products lost, per-tx delta, total delta,
expiry delta. Absent an active policy, the panel is hidden — not zero-filled.

### 9.2 Agent Console — `/admin/agents`

#### 9.2.1 `GET /internal/agents/sessions`

Rows come from `AgentSession` joined to spend under `policy_hash`. Poll every 2 s (no SSE in
v0.1 — R9.2c).

```json
{"sessions":[{"session_key":"client_abc:sha256:…","client_id":"client_abc",
  "display_name":"OpenStore Buyer","policy_hash":"sha256:…","frozen":false,
  "aal_level_last":2,"spent_minor":54000,"budget_minor":200000,
  "transactions_count":3,"max_transactions":10,
  "first_seen_at":"…","last_seen_at":"…","calls_last_60s":7}]}
```

#### 9.2.2 `GET /internal/agents/rejections?since=<rfc3339>&limit=<1..200>`

Reads `AuditLogEntry` where `success == False`, newest first.

```json
{"rejections":[{"at":"2026-08-27T09:14:51Z","client_id":"client_abc","tool":"checkout_confirm",
  "reason_code":"tag_violation","trace_id":"…","detail":"GEL-RUM-500 tags [alcohol,dessert]"}],
 "next_since":"2026-08-27T09:14:51Z"}
```

**R9.2a** — `AuditLogEntry.reason_code` MUST be populated on every rejection. This requires fixing
`merchant/audit.py` so `client_id` and `trace_id` are real (`PRODUCTION_READINESS.md` §0.8) —
the console is unusable otherwise, since today every row has `client_id = None`.

#### 9.2.3 `POST /internal/agents/freeze`

Request `{"session_key":"…","frozen":true}`. Effect: sets `AgentSession.frozen`. Every MCP tool
MUST check it immediately after scope verification and, when frozen, raise HTTP 403 with code
`agent.session_frozen`. A bundle built while frozen MUST set `e1_agent_authenticated = false`.

**R9.2b** — Freeze is scoped to `session_key`, never to an IP or a `client_id` alone.

**R9.2c** — v0.1 uses polling. Do not introduce SSE or WebSockets; the page must survive a server
restart mid-demo without a reconnect story.

#### 9.2.4 Layout

Three panels, in this priority order: **rejection feed (largest, left)**, live sessions (right
top), AAL mix over the last 24 h as a stacked bar of levels 0–3 (right bottom). The rejection
feed is the headline — do not demote it to a tab.

### 9.3 Hold & Cancel — `merchant/hold.py`

#### 9.3.1 Windows

```python
HOLD_SECONDS = {3: 0, 2: 900, 1: 3600}     # AAL0 never reaches this table
```

**R9.3a** — AAL0 MUST NOT create a hold; `checkout_confirm` rejects before Razorpay with
`policy.no_human_authority`.

**R9.3b** — `HOLD_SECONDS` is a module constant, not configuration, in v0.1. A policy field for
it would be signed data and is out of scope — listed in §11.

#### 9.3.2 State machine

```
ORDER_CREATED ──aal in {1,2}──> HELD ──cancel──────> CANCELLED   (terminal)
                                  └──window lapses─> RELEASED    (terminal)
ORDER_CREATED ──aal == 3───────────────────────────> RELEASED    (terminal, hold_seconds = 0)
```

Allowed transitions are exactly `{("HELD","CANCELLED"), ("HELD","RELEASED")}`. Any other MUST
raise. A second cancel on a terminal record returns HTTP 409 `hold.already_resolved`.

#### 9.3.3 Cancel

`POST /hold/{cancel_token}/cancel` — no auth (R8.2). Steps, in order, in one transaction:

1. Load by `cancel_token`; 404 if absent.
2. If `state != "HELD"` → 409 `hold.already_resolved`.
3. If `now > holds_until_at` → 409 `hold.window_expired`.
4. Set `state="CANCELLED"`, `resolved_at=now`.
5. Set `Order.status = "CANCELLED"`.
6. Cancel the Razorpay payment link. **`[verify]` the exact SDK call and its error shape against
   the live API before writing this line** — do not guess a method name.
7. Write a `RELEASE` ledger entry (`PRODUCTION_READINESS.md` §1.3) so the budget returns.
8. Emit a trace and an `AuditLogEntry`.

**R9.3c** — The release worker runs every 30 s over `HoldRecord` where `state="HELD"` and
`holds_until_at <= now`, moving them to `RELEASED`. Fulfilment MUST key off `RELEASED`, never off
`ORDER_CREATED`.

#### 9.3.4 Notification

Sent immediately on entering `HELD`, via the existing merchant bot (`merchant/notifier.py`).
Contains: merchant name, line items with quantities and unit prices, total, delivery address
verbatim, AAL level, the deadline as an absolute timestamp, and a link to
`/hold/{cancel_token}`. `notification.receipt_digest` =
`digest({"channel":…,"message_id":…,"sent_at":…})`.

**R9.3d** — The full bundle, the assertion, and the payment link MUST NOT appear in the
notification. Same discipline as the existing mandate fingerprint rule.

### 9.4 Evidence Viewer — `openstore_verify/viewer.html`

**R9.4a** — One file. No `<script src>`, no `<link rel=stylesheet>`, no `fetch`, no `XMLHttpRequest`,
no fonts, no images by URL. `tests/test_surfaces.py::test_viewer_is_self_contained` greps for
`src=`, `href="http`, `fetch(`, `XMLHttpRequest` and fails on any hit.

**R9.4b** — All cryptography via `crypto.subtle`: `digest("SHA-256", …)` and
`verify({name:"ECDSA", hash:"SHA-256"}, key, sig, data)` with keys imported as JWK
(`P-256`). This is why §1.2 pins ES256 everywhere.

**R9.4c** — The viewer MUST implement the identical check order (§7.3), reason codes (§7.4) and
`resolve_aal` (§5.3) as the Python verifier.
`tests/test_verifier.py::test_viewer_and_cli_agree_on_corpus` runs both over
`tests/fixtures/golden/*.json` — including deliberately tampered bundles — and asserts identical
verdicts, identical AAL levels, and identical failure codes.

**R9.4d — Input.** Drag-and-drop a `.json` file, paste JSON into a textarea, or read a bundle
inlined by `/orders/{id}/evidence/view` into `<script type="application/json" id="bundle">`.

**R9.4e — Tamper control.** An editable tree of the bundle. Every edit re-runs verification
immediately and renders the failing check, the link index, the section name, and the JSON path of
the first differing value. Include a `Reset` button.

**R9.4f — Plain-language verdict.** Rendered from bundle fields only, using this template with no
free-text invention:

> On `{authority.webauthn.signed_at|date}`, this customer authorised purchases of up to
> `{policy.max_spend_per_tx_minor|money}` per transaction and
> `{policy.max_spend_total_minor|money}` in total, from products tagged
> `{policy.allowed_tags|list}` at `{transaction.merchant_id}`, until
> `{policy.expires_at|date}`. This `{transaction.amount_minor|money}` cart was
> `{"inside" if verdict==ALLOW else "outside"}` that authority. The merchant
> `{"did not" if e6 else "may have"}` certified these product facts after the sale.
> **AAL{aal.level}.**

### 9.5 Merchant agent policy — `/.well-known/agent-policy.json` and `/agents`

```json
{"version":"0.1",
 "merchant":{"id":"gelateria-roma","name":"Gelateria Roma"},
 "agents_allowed":true,
 "minimum_aal":{"default":2,"above_minor":{"200000":3}},
 "hold_seconds_by_aal":{"1":3600,"2":900,"3":0},
 "rate_limits":[{"tool":"search_products","capacity":20,"refill_per_second":2.0}],
 "evidence":{"poai_version":"0.1","bundle_endpoint":"/orders/{checkout_id}/evidence",
             "jwks":"/.well-known/poai-jwks.json"},
 "dispute":{"contact":"disputes@example.com","evidence_retention_days":540}}
```

**R9.5a** — `minimum_aal.above_minor` maps a **string** decimal threshold in minor units to a
required tier. Evaluation: sort thresholds ascending as integers, take the highest whose value
`<= amount_minor`; if none, use `default`. Specify string keys because JSON object keys are
strings — do not "fix" this to integers.

**R9.5b** — `checkout_initiate` MUST return `required_aal` for the current cart total so the agent
learns the requirement **before** confirming. Add `required_aal: int` to its response.

**R9.5c — Storefront fit badge.** With an active policy for the session's user, each product on
`/` renders `✓ your agent can buy this` or `✗ outside your policy: {reason_code}`, computed by
`compute_blast_radius` (§9.1.1), cached per `(policy_hash, catalog_digest)`. With no active
policy, render nothing — do not render a neutral badge.

---

## 10. Test manifest

Each test's name is normative; the assertion column says what it must prove.

| File :: test | Asserts |
|---|---|
| `test_golden_vectors.py::test_canonical_json_vectors` | committed `(object, digest)` pairs incl. NFC/NFD pairs, empty lists, key permutations |
| `test_golden_vectors.py::test_compiler_digest_is_pinned` | equals the literal in §3.6 |
| `test_golden_vectors.py::test_merchant_and_verifier_canonicalisers_agree` | byte-identical over the corpus |
| `test_compiler_properties.py::test_compiler_module_is_pure` | AST: no forbidden imports (R3.2a) |
| `test_compiler_properties.py::test_never_allows_over_per_tx` | Hypothesis: ALLOW ⇒ total ≤ per-tx cap |
| `test_compiler_properties.py::test_never_allows_over_cumulative` | Hypothesis: ALLOW ⇒ spent+total ≤ total cap |
| `test_compiler_properties.py::test_monotonic_under_item_addition` | Hypothesis: DENY(c) ⇒ DENY(c+item) |
| `test_compiler_properties.py::test_deterministic` | Hypothesis: same inputs ⇒ identical verdict *and* transcript bytes |
| `test_compiler_properties.py::test_tag_mode_all_rejects_superset_tags` | `["vegan","alcohol"]` fails a `["vegan"]` `all` policy |
| `test_compiler_properties.py::test_legacy_policy_version_raises` | `policy_version=1` ⇒ `LegacyPolicyError` |
| `test_aal.py::test_all_512_predicate_combinations` | both `resolve_aal` implementations agree on 2⁹ × 2 inputs |
| `test_aal.py::test_unverified_assertion_is_aal0` | E2 false ⇒ level 0 regardless of everything else |
| `test_evidence.py::test_chain_detects_single_field_edit` | any one-field edit ⇒ named link + section + path |
| `test_evidence.py::test_all_eight_sections_present` | null-not-absent (R1.3a) |
| `test_evidence.py::test_retroactive_attestation_fails_e6` | `iat > cart_created_at` ⇒ E6 false |
| `test_verifier.py::test_verifier_imports_nothing_from_merchant` | AST walk (R7.1) |
| `test_verifier.py::test_verifier_is_offline` | monkeypatch socket to raise; verification still succeeds |
| `test_verifier.py::test_unknown_compiler_digest_exits_3` | exit code 3, no re-adjudication |
| `test_verifier.py::test_viewer_and_cli_agree_on_corpus` | R9.4c |
| `test_surfaces.py::test_no_internal_route_is_unauthenticated` | R8.1 over every `/internal/` and `/admin/` route |
| `test_surfaces.py::test_blast_radius_uses_compiler` | monkeypatch `compile_decision` to raise; endpoint must fail |
| `test_surfaces.py::test_viewer_is_self_contained` | R9.4a |
| `test_surfaces.py::test_frozen_session_blocks_tools` | frozen ⇒ 403 `agent.session_frozen` |
| `test_surfaces.py::test_cancel_token_is_single_use` | second cancel ⇒ 409 |
| `test_surfaces.py::test_aal3_creates_no_hold` | level 3 ⇒ `RELEASED`, `hold_seconds == 0` |
| `test_surfaces.py::test_hold_cancel_releases_budget` | RELEASE ledger entry written, budget restored |

---

## 11. Open questions — ask, do not decide

Unresolved. An implementer hitting one MUST stop and ask, per R0.4.

1. **Rekor availability.** Is the public Sigstore instance reachable and acceptable from the demo
   environment, or is `merkle_daily` the default? `[verify]`
2. **Razorpay payment-link cancellation.** Exact SDK method, exact error shape when a link is
   already paid, and whether cancellation is possible at all after `payment_link.paid`. This
   changes §9.3.3 step 6 materially. `[verify]` against the live API, not from memory.
3. **Payment-link `reference_id` uniqueness.** Enforced by Razorpay or not? Determines whether the
   deterministic-external-ID idempotency of `PRODUCTION_READINESS.md` §1.2 is available. `[verify]`
4. **ES256-only WebAuthn.** R1.2a excludes authenticators that only offer RS256 or Ed25519. Is
   that acceptable for the demo hardware, or must the viewer bundle a JS Ed25519 implementation?
5. **`hold_seconds` as signed policy data.** R9.3b keeps it a server constant. Should the human
   sign their own hold windows? That is a real design question with a consent argument on both
   sides.
6. **AAL2 liability wording.** §5.3c requires the "proposed, not a rule" phrasing. Confirm the
   exact sentence before it appears in a submission.
7. **`human_intent.request_text` in the bundle.** Verbatim customer text in a document that may be
   handed to third parties. Store only the digest by default and reveal on demand? Selective
   disclosure is named as designed-for, not built, in the argument doc §2.14.
8. **Operator authentication.** R8.1 specifies a single shared operator password. Sufficient, or
   is per-user auth needed?
9. **Evidence retention.** `/agents` advertises 540 days. Where is that enforced, and what deletes
   a bundle at the end of it?
10. **Second merchant.** Does the `chai.yaml` instance get its own PoAI keypair and JWKS, or share
    the primary's? Affects `kid` allocation and the verifier's JWKS handling.
