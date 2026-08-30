# OpenStore — Multi-Protocol Interoperability Contract v0.1

Normative. Extends `docs/IMPLEMENTATION_SPEC.md`; its §0 rules apply here unchanged (closed
identifier sets, fail loud, and **when this document is silent, stop and ask**).

The goal: one merchant server that is transactable by an AI buyer over **any** of the agentic
commerce protocols, without the business logic knowing which one was used — and one evidence
format that works across all of them.

---

## 0. The rule that matters most

**R0.I1 — An adapter MUST NOT be written from memory of an external specification.**

This is the single highest-risk instruction in the entire repository, because external field
names are exactly what a language model will confidently invent. The procedure is mandatory:

1. Fetch the live specification. Save the relevant excerpt to
   `merchant/protocols/<name>/SPEC_EXCERPT.md`, with the source URL and the UTC date fetched at
   the top.
2. Fill in the adapter's **mapping table** (§6 gives the empty table for each protocol) from that
   excerpt — external field name on the left, our core field on the right, one row per field.
3. Only then write code. Every external field name in the adapter MUST appear in the mapping
   table.
4. `tests/test_interop.py::test_every_adapter_has_a_dated_spec_excerpt` fails the build if
   `SPEC_EXCERPT.md` is missing, has no URL, or has no date.

**R0.I2** — An adapter for a protocol whose spec has not been fetched MUST NOT be listed as
supported in discovery (§5). Advertising a capability you have not verified is worse than not
having it: it turns a missing feature into a broken promise a judge can curl.

**R0.I3** — Every external field name appearing anywhere in this document is marked `[verify]`
and is a **placeholder to be replaced from the fetched spec**, not a fact. This document
specifies *our* side exactly and *their* side structurally.

---

## 1. Architecture

Today the business logic lives inside MCP tool functions (`merchant/mcp_server.py`), so MCP is
not one protocol among several — it *is* the server. That is the thing to fix, and it is a
mechanical refactor rather than a redesign.

```
        MCP          ACP          AP2         A2A       (future: x402)
         │            │            │           │
   ┌─────┴────────────┴────────────┴───────────┴─────┐
   │              PROTOCOL ADAPTERS                   │   translate only. no business logic.
   │  merchant/protocols/<name>/adapter.py            │   no DB access. no money decisions.
   └─────────────────────┬────────────────────────────┘
                         │  Actor · LineItemRequest · AuthorityPresentation
   ┌─────────────────────┴────────────────────────────┐
   │           COMMERCE CORE  (merchant/core/)         │   protocol-free. the only place
   │  catalog · cart · checkout · compiler · evidence  │   money decisions are made.
   └─────────────────────┬────────────────────────────┘
                         │
   ┌─────────────────────┴────────────────────────────┐
   │      PoAI EVIDENCE LAYER  (protocol-agnostic)     │   records WHICH protocol authorised,
   │  authority.scheme discriminates the presentation  │   in a format none of them define.
   └──────────────────────────────────────────────────┘
```

**R1.1** — `merchant/core/` MUST NOT import `mcp`, `fastapi`, `a2a`, or any protocol library.
`tests/test_interop.py::test_core_is_protocol_free` enforces this by AST walk, in the same style
as `tests/test_isolation.py`.

**R1.2** — `merchant/protocols/*/adapter.py` MUST NOT import `sqlmodel`, open a DB session, call
Razorpay, or reference `IdempotencyRecord`, `SpendLedgerEntry`, or `Checkout` directly. Adapters
translate and delegate. Enforced by
`tests/test_interop.py::test_adapters_do_not_touch_persistence`.

**R1.3 — Why this is the interop story, not just tidiness.** AP2, ACP and MCP disagree about
transport, about authorization objects, and about who holds the payment credential. They agree
about nothing that would let a merchant compare them. The PoAI bundle is the one artifact that
spans all of them, because it records *what authority was presented and how strong it was*
regardless of which protocol carried it. **The adjudication layer is the interoperability
layer** — that is the claim this refactor makes checkable.

---

## 2. The Commerce Core API — `merchant/core/api.py`

Exact signatures. These are the only entry points an adapter may call.

```python
def search_products(query: SearchQuery) -> list[ProductView]: ...
def get_product(sku: str) -> ProductView: ...

def create_cart(actor: Actor, items: tuple[LineItemRequest, ...]) -> CartView: ...
def update_cart(actor: Actor, cart_id: int, items: tuple[LineItemRequest, ...]) -> CartView: ...

def initiate_checkout(actor: Actor, cart_id: int,
                      delivery: DeliveryAddress) -> CheckoutView: ...

def confirm_checkout(actor: Actor, checkout_id: str,
                     authority: AuthorityPresentation,
                     idempotency_key: str) -> ConfirmResult: ...

def get_order(actor: Actor, checkout_id: str) -> OrderView: ...
def get_evidence(actor: Actor, checkout_id: str) -> dict: ...
```

### 2.1 Types — frozen dataclasses, exactly these fields

```python
@dataclass(frozen=True, slots=True)
class Actor:
    subject: str                      # stable caller id; for OAuth this is client_id
    display_name: str
    scopes: frozenset[str]
    protocol: str                     # closed set, §5.2
    protocol_session_id: str | None   # e.g. an ACP checkout_session id [verify]
    auth_method: str                  # closed set: "oauth2_bearer" | "http_message_signature"

@dataclass(frozen=True, slots=True)
class LineItemRequest:
    sku: str
    qty: int                          # price is NEVER accepted from a caller, in any protocol

@dataclass(frozen=True, slots=True)
class DeliveryAddress:
    raw: str

@dataclass(frozen=True, slots=True)
class ConfirmResult:
    checkout_id: str
    status: str                       # "ORDER_CREATED" | "HELD"
    payment_link_url: str
    aal_level: int
    hold: HoldView | None
    bundle_id: str
```

**R2.1a** — `LineItemRequest` carries no price field, in any protocol, ever. Several external
protocols include prices in their cart payloads `[verify]`; the adapter MUST discard them and
the core MUST re-derive from the catalog. This is the existing `_validate_and_price` discipline,
promoted to a type-level guarantee.

**R2.1b** — `Actor.scopes` is normalised to OpenStore's scope vocabulary
(`catalog:read`, `cart:write`, `checkout:initiate`, `checkout:confirm`) by the adapter. The core
knows no other scope strings.

**R2.1c** — `ProductView`, `CartView`, `CheckoutView`, `OrderView`, `HoldView` and `SearchQuery`
are plain frozen dataclasses mirroring the existing response dicts. They MUST NOT contain
protocol-specific fields; a protocol needing an extra field adds it in its own adapter's
serialiser.

**R2.1d** — `CheckoutView` MUST include `required_aal: int` (`IMPLEMENTATION_SPEC.md` §9.5b) so
every protocol can tell the agent the tier requirement before confirmation.

---

## 3. `AuthorityPresentation` — the discriminated union

This is the type that makes multi-protocol authorization possible. It is what an agent presents
to prove a human authorised the purchase, in whatever form its protocol produces.

```python
AuthorityScheme = Literal[
    "native_webauthn",       # OpenStore's own ceremony (IMPLEMENTATION_SPEC §5.2)
    "ap2_intent_mandate",    # [verify] AP2 Intent Mandate
    "ap2_cart_mandate",      # [verify] AP2 Cart Mandate
    "acp_delegated_token",   # [verify] ACP delegated payment credential
    "none",                  # no authority presented
]

@dataclass(frozen=True, slots=True)
class AuthorityPresentation:
    scheme: AuthorityScheme
    raw: Mapping[str, Any]            # the presentation, verbatim as received
    policy_json: Mapping[str, Any] | None   # v2 IntentPolicy, when the scheme carries one
```

**R3.1** — Five schemes. No others exist in v0.1. An unknown value MUST be rejected with
`authority.unknown_scheme` — never treated as `"none"`, which would silently downgrade rather
than fail.

**R3.2** — Verification dispatches on `scheme` to
`merchant/core/authority/<scheme>.py::verify(presentation) -> VerifiedAuthority`, returning the
predicate inputs of `IMPLEMENTATION_SPEC.md` §5.1. Each verifier is a pure function of its
presentation plus the enrolled credential material.

**R3.3** — `scheme` is recorded in the bundle. This **amends `IMPLEMENTATION_SPEC.md` §6.2**,
adding one required field:

```jsonc
"authority": { "scheme": "native_webauthn", "policy": {…}, "policy_hash": "sha256:…", … }
```

Per the versioning discipline in `docs/AGENT_LAYER.md` R5.1b, this amendment is free only
because nothing is implemented yet. Make it now.

**R3.4** — When `scheme != "native_webauthn"`, the `authority.webauthn` object is JSON `null`
and the scheme-specific presentation lives in `authority.presentation` (an object, verbatim).
`SECTION_ORDER` is unchanged; only the contents of the `authority` section vary.

---

## 4. Evidence strength across protocols — the maximum reachable AAL

Nobody has published a comparison of what these protocols actually prove. This table is that
comparison, and it is a contribution in its own right: it says, per scheme, the strongest claim
the evidence supports.

| `scheme` | What the merchant holds | Max AAL | Why capped there |
|---|---|---|---|
| `native_webauthn`, `challenge_binding.mode = "cart"` | human's authenticator signature over **this cart hash**, UV set | **3** | strongest available: a human cryptographically bound this exact cart |
| `native_webauthn`, `mode = "policy"` | human's signature over a policy, reusable | **2** | no per-transaction human act |
| `ap2_cart_mandate` `[verify]` | a mandate asserted to bind a specific cart | **3** *if and only if* the mandate carries a human-held-key signature over the cart that the merchant can verify offline; else **2** | verify what the signature actually covers and who holds the key before claiming 3 |
| `ap2_intent_mandate` `[verify]` | a mandate expressing standing authority | **2** | policy-level, same shape as our `mode="policy"` |
| `acp_delegated_token` `[verify]` | a bearer credential issued by a PSP | **1** | a token issued *to the agent* proves the PSP's willingness to be charged, not a human's authorisation of these goods. It is a payment credential, not an authorisation artifact |
| `none` | nothing | **0** | |

**R4.1** — These caps are enforced in `merchant/core/authority/__init__.py::MAX_AAL_BY_SCHEME`
and MUST also be implemented in `openstore_verify/aal.py`, since a verifier evaluating a
third-party bundle needs them. The final level is
`min(resolve_aal(predicates, policy_version), MAX_AAL_BY_SCHEME[scheme])`.

**R4.2** — When the cap binds (i.e. it lowers the level), the reason
`scheme_capped_<scheme>` MUST be appended to `aal.reasons`. This extends the closed set in
`IMPLEMENTATION_SPEC.md` §5.3a by five values, one per scheme.

**R4.3** — The two `[verify]` rows for AP2 MUST be resolved by reading the specification before
either adapter ships. If the answer is unclear from the spec, the cap is the **lower** value and
§9 records the question. Never resolve an ambiguity upward.

---

## 5. Discovery and capability negotiation

### 5.1 One document, extended

`/.well-known/agent-commerce.json` gains a `protocols` array. Existing fields are unchanged, so
nothing that reads it today breaks.

```json
{"version":"0.2",
 "merchant":{"name":"Gelateria Roma","id":"gelateria-roma"},
 "protocols":[
   {"name":"mcp","version":"2025-06-18","endpoint":"/agent/mcp",
    "auth":["oauth2_bearer"],
    "authority_schemes":["native_webauthn"],
    "spec_excerpt":"/protocols/mcp/spec-excerpt"},
   {"name":"acp","version":"[verify]","endpoint":"/agent/acp",
    "auth":["oauth2_bearer","http_message_signature"],
    "authority_schemes":["acp_delegated_token","native_webauthn"],
    "spec_excerpt":"/protocols/acp/spec-excerpt"}],
 "evidence":{"poai_version":"0.1","bundle_endpoint":"/orders/{checkout_id}/evidence",
             "jwks":"/.well-known/poai-jwks.json"},
 "storefront":"…","catalog_endpoint":"…","mcp_endpoint":"…",
 "a2a_agent_card":"…","auth":{…},"policy":{…}}
```

**R5.1a** — `mcp_endpoint` and the existing top-level `auth`/`policy` objects are **retained** for
backward compatibility even though `protocols[]` supersedes them. Bump `version` to `"0.2"`.

**R5.1b** — A protocol appears in `protocols[]` **only** if its `SPEC_EXCERPT.md` exists and its
conformance tests pass (R0.I2). The array is generated from the adapter registry at startup, not
hand-written, so it cannot drift.

**R5.1c** — `GET /protocols/<name>/spec-excerpt` serves that adapter's committed excerpt as
`text/markdown`. This is unusual and deliberate: it lets a reviewer see exactly which version of
which spec you built against, and it is one of the cheapest credibility signals in the repo.

### 5.2 `Actor.protocol` — closed set

`"mcp"`, `"acp"`, `"ap2"`, `"a2a"`, `"internal"`. Five values.

### 5.3 Cross-protocol identity

**R5.3a** — `Actor.subject` MUST be stable for the same agent across protocols where the
protocol provides a stable identifier. Where it does not, the adapter mints
`f"{protocol}:{opaque_id}"` and MUST NOT invent a merge with an existing OAuth `client_id`.

**R5.3b** — Rate limits, spend ledger entries, and `AgentSession` rows key on `Actor.subject`. An
agent MUST NOT be able to reset its budget by switching protocol. `tests/test_interop.py::
test_budget_is_shared_across_protocols` proves it: spend over MCP, then confirm the remaining
budget seen over ACP reflects it.

---

## 6. Per-protocol adapter specifications

Each adapter is `merchant/protocols/<name>/` containing `SPEC_EXCERPT.md`, `mapping.md`,
`adapter.py`, and `conformance.py`.

### 6.1 MCP — fully specified, already implemented

The only adapter this document can specify completely, because it is the one already built and
running. The refactor is mechanical: each existing tool body moves to `merchant/core/api.py`,
and the tool function becomes claims-extraction plus a core call.

| MCP tool | Core call |
|---|---|
| `search_products` | `search_products(SearchQuery(...))` |
| `get_product` | `get_product(sku)` |
| `create_cart` / `update_cart` | `create_cart` / `update_cart` |
| `checkout_initiate` | `initiate_checkout` |
| `checkout_confirm` | `confirm_checkout(..., AuthorityPresentation(scheme="native_webauthn", raw=policy_token, policy_json=policy_json))` |
| `get_order` | `get_order` |

**R6.1a** — `checkout_confirm`'s legacy `jws` parameter (the OTP/mandate fallback path) stays on
the MCP adapter and is **not** promoted into the core. Per `IMPLEMENTATION_SPEC.md` R0.6, that
path is frozen, not refactored.

**R6.1b** — `@audited_tool` moves to the core so every protocol is audited identically. Today
only MCP calls are audited, which would make the Agent Console blind to other protocols.

### 6.2 ACP — structure specified, field names to be fetched

`SPEC_EXCERPT.md` MUST cover: the checkout-session lifecycle, the product feed schema, and the
delegated payment credential. Then fill:

| ACP field `[verify]` | Direction | Core field |
|---|---|---|
| *(fill from spec)* | in | `LineItemRequest.sku` |
| *(fill from spec)* | in | `LineItemRequest.qty` |
| *(fill from spec)* | in | `DeliveryAddress.raw` |
| *(fill from spec)* | in | `AuthorityPresentation.raw` (scheme `acp_delegated_token`) |
| *(fill from spec)* | in | `Actor.protocol_session_id` |
| *(fill from spec)* | out | `CheckoutView.checkout_id` |
| *(fill from spec)* | out | `CheckoutView.total_minor` |
| *(fill from spec)* | out | `ConfirmResult.status` |

**R6.2a** — Session-state mapping MUST be an explicit dict, not inferred:
`ACP_STATE_BY_CHECKOUT_STATUS: dict[str, str]`, with a `KeyError` on anything unmapped rather
than a default. An unmapped state is a bug to surface, not to paper over.

**R6.2b** — ACP's currency and amount representation MUST be checked against §0.1 of
`IMPLEMENTATION_SPEC.md`. If ACP uses a different unit convention `[verify]`, conversion happens
**in the adapter only**, and `tests/test_interop.py::test_acp_amounts_round_trip` proves it is
lossless in both directions.

**R6.2c** — A delegated payment credential caps the transaction at AAL1 (§4). If the merchant
policy requires a higher tier for that basket (`IMPLEMENTATION_SPEC.md` §9.5), the adapter MUST
return the protocol's step-up/decline response rather than proceeding. Determine that response
shape from the spec, not by guessing.

### 6.3 AP2 — the authorization vocabulary

AP2 is not a transport in the sense MCP and ACP are; it is a mandate vocabulary. Two integration
points:

1. **Ingest** — accept an AP2 mandate as `AuthorityPresentation` with scheme
   `ap2_intent_mandate` or `ap2_cart_mandate`, verify it, and map its constraints onto a v2
   `IntentPolicy`.
2. **Emit** — `merchant/protocols/ap2/emit.py` renders OpenStore's own authority as AP2-shaped
   objects, so a bundle can be consumed by an AP2-native party.

| AP2 field `[verify]` | Core field |
|---|---|
| *(spending limit)* | `IntentPolicy.max_spend_per_tx_minor` |
| *(cumulative limit)* | `IntentPolicy.max_spend_total_minor` |
| *(merchant constraint)* | `IntentPolicy.merchant_id` |
| *(expiry)* | `IntentPolicy.expires_at` |
| *(cart binding)* | compared against `goods.cart_hash` |
| *(signature / proof)* | verified per §4 to set predicate E2 |

**R6.3a** — A mandate constraint with **no** OpenStore equivalent MUST cause the ingest to fail
with `ap2.unmappable_constraint`, naming the field. Silently dropping a constraint the human
signed is the worst possible failure in this system: it widens an authority the human narrowed.

**R6.3b** — Emission is lossy in the other direction and MUST be labelled so. `emit.py` returns
`(ap2_object, dropped_fields: tuple[str, ...])` and the caller MUST surface `dropped_fields`.

**R6.3c** — Ingested AP2 mandates set `policy_version` to `2` only if every v2 field can be
populated from the mandate. Otherwise the policy is legacy and the bundle is AAL1 with
`policy_schema_legacy` (`IMPLEMENTATION_SPEC.md` §5.3).

### 6.4 A2A — already present, unchanged

The merchant reasoning agent (`merchant_agent/`) keeps its A2A surface as-is. It holds no signing
key, no Razorpay write credential, and no core write access.

**R6.4a** — `merchant_agent/` MUST NOT import `merchant.core.api`'s write functions
(`create_cart`, `update_cart`, `initiate_checkout`, `confirm_checkout`). Extend
`tests/test_isolation.py` to assert this — it converts the credential-separation claim in
`plan.md` into a test that covers the refactored core, which the existing test does not.

### 6.5 Web Bot Auth — agent identity at the edge

Complements rather than replaces OAuth: it identifies the *software agent*, where OAuth
identifies the *authorised client*.

**R6.5a** — When present and valid, an HTTP Message Signature sets
`Actor.auth_method = "http_message_signature"` and its key identifier is recorded in the bundle's
`agent` section as `agent.edge_identity` (an optional string; another §6.2 amendment).

**R6.5b** — It MUST NOT be sufficient on its own for any money-touching scope. A request carrying
only an edge signature and no OAuth token gets `catalog:read` at most.

### 6.6 x402 — explicitly deferred

Not in v0.1. Recorded here so its absence is a decision rather than an oversight: x402 settles
per-request payments, typically in stablecoins, which is a different economic model from a cart
checkout and does not compose with an intent policy expressed in INR spend caps. Revisit only if
metered catalog access becomes a goal.

---

## 7. Conformance suite

`tests/test_interop.py`. Names are normative.

| Test | Asserts |
|---|---|
| `test_core_is_protocol_free` | AST: no protocol library imported under `merchant/core/` (R1.1) |
| `test_adapters_do_not_touch_persistence` | AST: no `sqlmodel` / session / Razorpay import in any adapter (R1.2) |
| `test_every_adapter_has_a_dated_spec_excerpt` | `SPEC_EXCERPT.md` exists with URL + ISO date (R0.I1) |
| `test_every_external_field_is_in_the_mapping_table` | every string literal in an adapter that names an external field appears in `mapping.md` |
| `test_discovery_lists_only_passing_protocols` | `protocols[]` == adapters whose conformance suite passes (R5.1b) |
| `test_no_adapter_accepts_a_price` | fuzz each adapter with caller-supplied prices; the core-bound `LineItemRequest` never carries one (R2.1a) |
| `test_budget_is_shared_across_protocols` | spend over MCP is visible over ACP (R5.3b) |
| `test_scheme_cap_is_applied` | for each scheme, a bundle whose predicates would give a higher level is capped, with `scheme_capped_*` in reasons (R4.2) |
| `test_unknown_scheme_is_rejected` | `authority.unknown_scheme`, never a silent `"none"` (R3.1) |
| `test_ap2_unmappable_constraint_fails_closed` | an unrecognised mandate constraint raises, never drops (R6.3a) |
| `test_same_cart_same_verdict_across_protocols` | identical cart + policy over MCP and over ACP produce byte-identical `adjudication.transcript` |
| `test_merchant_agent_cannot_call_core_writes` | R6.4a |

**R7.1** — `test_same_cart_same_verdict_across_protocols` is the headline conformance test and
the one to show a judge. It proves the central claim: the decision is protocol-independent, so
the evidence is comparable across protocols that agree on nothing else.

---

## 8. Migration order

The refactor touches the money path, so sequence matters.

1. `docs/PRODUCTION_READINESS.md` §0.1–0.2 fixes. Non-negotiable prerequisite.
2. Extract `merchant/core/` with MCP still calling it directly. No behaviour change; existing
   `tests/test_mcp.py` MUST pass untouched. **Commit here.**
3. Introduce `Actor`, `LineItemRequest`, `AuthorityPresentation`; MCP adapter constructs them.
   `tests/test_mcp.py` still untouched and passing. **Commit here.**
4. Add the adapter registry, `protocols[]` discovery, and the conformance suite with MCP as the
   only entry.
5. Add PoAI `authority.scheme` (R3.3) and `MAX_AAL_BY_SCHEME` (R4.1).
6. Fetch the ACP spec → excerpt → mapping table → adapter → conformance.
7. Fetch the AP2 spec → excerpt → mapping table → ingest, then emit.

**R8.1** — Steps 2 and 3 MUST NOT change `tests/test_mcp.py`. If a test needs editing, the
refactor changed behaviour and is wrong. That test file is the safety net for the whole
migration; treat any required edit as a defect report.

---

## 9. Open questions — ask, do not decide

1. **AP2 signature semantics.** What exactly does a Cart Mandate's signature cover, and who holds
   the key? This alone decides whether AP2 can reach AAL3 (§4). `[verify]`
2. **ACP authorization model.** Does a delegated payment credential encode any human-authorisation
   claim, or purely a payment permission? Decides whether the AAL1 cap in §4 is right. `[verify]`
3. **ACP amount units.** Minor units or decimal strings? Governs R6.2b. `[verify]`
4. **Does ACP presume a specific PSP relationship** that a self-hosted merchant server cannot
   satisfy in test mode? If so, the adapter may be demonstrable only against a mock, which MUST
   be stated in discovery rather than implied.
5. **Cross-protocol identity.** Is there a defensible way to merge an ACP session identity with an
   OAuth `client_id`, or does R5.3a's `protocol:opaque_id` stand?
6. **Web Bot Auth key discovery.** Where does the verifying key come from, and is offline
   verification possible? If not, R6.5a cannot be evidence in a bundle. `[verify]`
7. **Second merchant.** Shared adapter registry or per-instance? Interacts with
   `IMPLEMENTATION_SPEC.md` §11 question 10 on JWKS and `kid` allocation.
