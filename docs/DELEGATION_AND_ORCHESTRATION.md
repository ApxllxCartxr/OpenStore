# Delegated authority and multi-merchant orchestration

Normative. Extends `docs/IMPLEMENTATION_SPEC.md` (its §0 rules apply unchanged) and amends it
where §9 says so.

Two requests — "shop across several merchants when one can't fulfil" and "let an agent
sub-delegate part of its budget to another agent" — are **the same problem wearing two hats**,
and this document treats them as one. The mechanism that makes sub-delegation verifiable is
also the mechanism that makes multi-merchant sourcing safe.

---

## 1. The problem, stated precisely

A human signs one thing:

> *Agent A may spend ≤ ₹1,000 total, at merchants {M1, M2, M3}, on vegan items, until 3 Sept.*

Agent A decomposes the task and needs to sub-delegate:

> *Agent B may spend ≤ ₹200 of my budget, at M2 only, until 18:00 today.*

Everything hard lives in the phrase **"of my budget."** B's spending must be deducted from A's
authority exactly once, verifiably, by parties who do not talk to each other — M2 has no idea
what A spent at M1, and no coordinator exists.

### 1.1 The attack surface

Any design must defeat all ten. This table is the design's specification.

| # | Attack | Defeated by |
|---|---|---|
| A1 | **Budget inflation** — B claims a larger sub-budget than A granted | §3.1 signed delegation link; B cannot mint its own grant |
| A2 | **Double-counting** — B's spend deducted from nobody, or from A's pool twice | §3.2 exclusive transfer: the envelope *is* the accounting |
| A3 | **Sibling collusion** — A grants ₹200 to B and ₹200 to C from a ₹300 remainder | §3.2 envelope disjointness, checkable from A's own spend chain |
| A4 | **Receipt omission** — the agent hides a spend to appear richer | §3.3 contiguous sequence numbers; a gap is a hard failure |
| A5 | **Chain fork** — same chain head presented to two merchants at once | §4 — *not* prevented offline. Structurally impossible under envelope-per-merchant; otherwise detected and attributed |
| A6 | **Stale replay** — reuse a released or expired envelope | §3.1 `expires_at` + §3.3 `RELEASE` entry terminates the chain |
| A7 | **Scope escalation** — B buys a tag or at a merchant A was not permitted | §3.1 monotone attenuation (meet) |
| A8 | **Expiry laundering** — sub-delegation outliving its parent | §3.1 `expiry_attenuation` |
| A9 | **Depth explosion** — unbounded delegation chains | §3.1 `max_depth = 3` |
| A10 | **Orphan envelope** — parent revoked, child keeps spending | §3.1 chain re-verified at every spend; a revoked ancestor invalidates all descendants |

### 1.2 Why the existing specs do not answer this

`[verify]` AP2's Intent and Cart Mandates, ACP's checkout sessions, and Visa TAP are all
specified around **one human → one agent → one merchant**. They define what an agent may do; they
do not define what happens when that agent is itself a delegator, nor how a budget is accounted
across parties who never communicate. That is the gap, and it is a real one: multi-agent
decomposition is the *default* architecture people are building buyer agents with right now.

---

## 2. Prior art worth standing on

Do not invent from scratch; this problem has been solved in adjacent domains and the vocabulary
is worth borrowing precisely.

| Prior work | What it gives us | What it lacks here |
|---|---|---|
| **Macaroons** (Google, 2014) | offline attenuation by appending caveats, HMAC-chained | no budget arithmetic; caveats are predicates, not quantities |
| **Biscuit**, **UCAN** | capability chains with offline verification, attenuation, expiry | same — no depletable resource |
| **X.509 proxy certificates** (RFC 3820) | delegation chains with depth limits, in production for decades | identity delegation, not spend |
| **OAuth Token Exchange** (RFC 8693) | delegation semantics (`act`, `may_act`) | requires an online authorization server as coordinator |
| **Hierarchical token bucket** (networking) | nested budgets that provably cannot exceed the parent | single machine, shared memory |
| **Auth-then-capture / incremental auth** (cards) | reservation semantics, release of unused authorisation | single merchant |

The synthesis: **capability attenuation gives us the constraint half; hierarchical budgeting gives
us the quantity half; the reservation model gives us release.** None of the three has been
combined into an offline-verifiable form for agent commerce, and that combination is what §3 is.

---

## 3. The design

Three layers, three pure functions, three pinned digests.

### 3.1 Layer 1 — the delegation chain (constraints)

A chain of signed links. Link 0 is the human's WebAuthn-signed root policy; each subsequent link
is signed by the previous link's delegate.

```python
@dataclass(frozen=True, slots=True)
class DelegationLink:
    link_id: str                  # "dl_" + 26 Crockford base32
    parent_link_id: str | None    # None iff depth == 0
    depth: int                    # 0..3
    envelope_id: str              # "env_" + 26 Crockford base32
    delegator_thumbprint: str     # RFC 7638 JWK thumbprint; at depth 0 the WebAuthn credential_id
    delegate_thumbprint: str
    grant: Grant
    issued_at: str                # RFC 3339
    signature: str                # JWS Compact ES256 over canonical(link without `signature`)

@dataclass(frozen=True, slots=True)
class Grant:
    budget_minor: int
    currency: str
    merchant_ids: tuple[str, ...]      # sorted; note plural — see §9.1
    allowed_tags: tuple[str, ...]      # sorted; empty == unconstrained
    tag_mode: str                      # "all" | "any"
    blocked_skus: tuple[str, ...]      # sorted
    max_transactions: int
    not_before: int                    # Unix seconds
    expires_at: int                    # Unix seconds
```

**Attenuation is a meet.** The effective policy is the greatest lower bound of every grant in the
chain — a monotone narrowing that a verifier computes in one pass:

| Field | Meet operation | Rejection if violated |
|---|---|---|
| `budget_minor` | `min` | `budget_not_attenuating` |
| `merchant_ids` | set intersection; child's set MUST be a subset of parent's | `merchant_not_attenuating` |
| `allowed_tags` | child's set MUST be a subset of parent's, unless parent is empty (⊤) | `tag_not_attenuating` |
| `tag_mode` | parent `all` ⇒ child MUST be `all`; parent `any` ⇒ child may be either | `tag_not_attenuating` |
| `blocked_skus` | **union** — blocking is additive; a child may block more, never fewer | — |
| `max_transactions` | `min` | `tx_count_not_attenuating` |
| `not_before` | `max` | `expiry_not_attenuating` |
| `expires_at` | `min` | `expiry_not_attenuating` |
| `currency` | MUST be equal | `currency_mismatch` |

**R3.1a** — `max_depth = 3`. Depth 0 is the human's root. A link at depth 4 MUST be rejected with
`depth_exceeded`. This is a constant, not configuration.

**R3.1b** — Link 0's signature is the WebAuthn assertion over the policy hash
(`IMPLEMENTATION_SPEC.md` §5.2). Links 1+ are ES256 JWS by the delegator's key. A chain whose
link 0 is not a human WebAuthn signature MUST be rejected with `root_not_human_signed` — an
agent cannot bootstrap its own authority.

**R3.1c** — Verification is a pure function, re-runnable offline from the bundle:

```python
def verify_delegation_chain(links: tuple[DelegationLink, ...]) -> EffectivePolicy: ...
```

`DELEGATION_DIGEST = sha256:f4d24d08ca1f31813479584ffa5c514d0267ec00ada416b33f497ee9d4a04016`

Pinned exactly as `COMPILER_DIGEST` is (`IMPLEMENTATION_SPEC.md` §3.6), over `DELEGATION_SPEC`
in §5.2. `tests/test_delegation.py::test_delegation_digest_is_pinned` asserts the literal.

### 3.2 Layer 2 — budget envelopes, and the decision that makes this tractable

Here is the design decision everything rests on:

> **Delegation is an exclusive transfer, not a shared view.**

When A delegates ₹200 to B, A's own spendable budget drops to ₹800 **at the moment of
delegation**, recorded as a `DELEGATE` entry in A's own spend chain. B receives an *envelope* —
a disjoint slice of budget that only B can draw against.

Why this is the right call, and it is not obvious until you try the alternative:

- The naive model — parent and child both draw from a shared pool — is a **distributed counter
  under a cap**. Correct solutions to that need either an online coordinator (which does not
  exist in agentic commerce) or consensus (absurd here). Every offline approximation is
  exploitable by A3 sibling collusion.
- Exclusive transfer turns it into a **static partition**, and a partition verifies itself:

```
Σ(all envelopes issued) + Σ(all spends) ≤ root_budget            — invariant, by construction
```

No coordination at spend time. No merchant needs to know any other merchant exists. The
arithmetic is checkable offline, by anyone, from the chain.

The cost is capital efficiency: an envelope of ₹200 where B spends ₹50 strands ₹150 until it is
released. Two mitigations, both required:

**R3.2a** — Envelopes carry short `expires_at` values. On expiry, unspent budget returns to the
parent automatically — the parent's available budget is *derived*, never stored:

```
available(envelope) = budget_minor
                    − Σ SPEND entries
                    − Σ DELEGATE entries to children that are neither expired nor released
```

**R3.2b** — A delegate MAY release early by appending a signed `RELEASE` entry, which terminates
its chain. Released budget is immediately available to the parent.

**R3.2c — Disjointness.** At issuance, `Σ(sibling envelope budgets) ≤ available(parent)`.
Checkable from the parent's spend chain alone, so it verifies offline. Violation is
`envelope_overlaps_sibling`.

### 3.3 Layer 3 — the spend chain (accounting)

One hash chain per envelope. Every event that moves budget appends a link.

```python
@dataclass(frozen=True, slots=True)
class SpendEntry:
    envelope_id: str
    sequence: int                  # 0-based, contiguous, no gaps
    entry_type: str                # "SPEND" | "DELEGATE" | "RELEASE"
    amount_minor: int              # RELEASE: the amount returned
    ref: str                       # SPEND: bundle_id · DELEGATE: child envelope_id · RELEASE: ""
    prev_link: str                 # "sha256:…"; at sequence 0 the genesis (below)
    issued_at: str
    signature: str                 # see R3.3b — signer depends on entry_type
```

```
genesis  = sha256(canonical_json_bytes({"envelope_id": envelope_id}))
link_i   = sha256(prev_link_raw_32_bytes || canonical_json_bytes(entry_without_signature))
```

**R3.3a** — An agent spending at a merchant MUST present the **complete** entry list for its
envelope. The merchant verifies: `sequence` contiguous from 0, every `prev_link` correct, and
`Σ` within `budget_minor`. A missing entry produces a `sequence_gap` — which is exactly why
receipt omission (A4) fails.

**R3.3b — Who signs what, and why it matters.**

| `entry_type` | Signed by | Consequence |
|---|---|---|
| `SPEND` | **the merchant that processed it**, with its PoAI ES256 key | the agent cannot forge, omit, reorder, or rewrite a spend |
| `DELEGATE` | the envelope holder | it is the holder's own act of granting |
| `RELEASE` | the envelope holder | likewise |

Merchant-countersigned `SPEND` entries are what make the chain trustworthy without a coordinator.
The agent holds the chain but cannot author the entries that cost it money.

**R3.3c** — `verify_spend_chain(entries, envelope) -> ChainState` is pure and offline.
`SPENDCHAIN_DIGEST = sha256:80fc5c08cab7ae4c9a82d6dec4eece8c6965c14554ab88097fe86ef3db7623ff`

---

## 4. The honest limit: cross-merchant double-spend

**This design does not prevent A5.** State it plainly, because a reviewer will find it in about
ninety seconds and finding it yourself is worth more than hiding it.

An agent holding envelope `E` with ₹200 remaining can present the same chain head to M1 and M2
**simultaneously**. Neither can see the other. Both allow. ₹400 is spent against a ₹200 envelope.

This is the double-spend problem. It has exactly three known answers — a trusted coordinator,
consensus, or detection-with-penalty — and no offline scheme escapes it. What this design does
give you:

**4.1 — The default configuration makes it structurally impossible.**

**R4.1** — A delegation whose `grant.merchant_ids` contains exactly one merchant is a
**single-merchant envelope**, and it is the default that `mint_envelope` produces. Forking
requires two merchants; a single-merchant envelope has only one, and that merchant sees every
entry in the chain. **Cross-merchant double-spend is impossible by construction, offline, with
zero coordination.**

This is the interesting result, and it is what ties this document to multi-merchant orchestration:
the safe way to shop across three merchants is not one envelope spanning three merchants — it is
**three single-merchant envelopes**, minted from one root. §7 is exactly that.

**4.2 — When a multi-merchant envelope is genuinely needed**, it MUST declare a sequencer:

```json
{"sequencer": {"type":"none"}}
{"sequencer": {"type":"holder_service","endpoint":"…","jwks":"…"}}
```

`type: "none"` on a multi-merchant envelope MUST cause the merchant to cap the transaction at
**AAL1** and record `reason: multi_merchant_envelope_unsequenced`. The agent may still spend; the
evidence simply states that the budget guarantee is detect-only. The system does not refuse — it
**prices the weaker guarantee**, which is the same move as the whole AAL design.

**4.3 — Detection is cryptographic and attributable.** Two `SPEND` entries with the same
`(envelope_id, sequence)`, countersigned by different merchants, are a non-repudiable proof that
the holder of `delegate_thumbprint` forked its chain. `openstore-verify --detect-forks <dir>`
scans a set of bundles and emits any such pair as `fork_proof`. That is a real artifact for a
dispute, and it is more than the current specs offer.

---

## 5. Normative additions

### 5.1 `merchant/compiler.py` — two changes

`merchant_lock` becomes set membership, and a new envelope check is added at position 10:

| # | `check` | Passes iff | `reason_code` |
|---|---|---|---|
| 2 | `merchant_lock` | `context.merchant_id in policy.merchant_ids` | `merchant_mismatch` |
| 10 | `spend_envelope` | `chain_state.spent_minor + total_minor <= envelope.budget_minor` | `spend_envelope_exceeded` |

`spend_cumulative` moves to position 11 and continues to check the **root** budget.

**R5.1a** — `COMPILER_SPEC.compiler_version` becomes `"1.1.0"` and the pinned digest becomes:

```
sha256:10230521796f94d9039a8f25d0c03c0e48f870b53ad54885001a3a440f9f7ed9
```

The v1.0.0 digest `sha256:9654…` is **retired, not supported** — legitimate only because no
bundle has been issued yet. Update the literal in `IMPLEMENTATION_SPEC.md` §3.6. After first
issuance this same change would require the verifier to implement both procedures and dispatch on
`adjudication.compiler_digest` (`IMPLEMENTATION_SPEC.md` R3.6b). This is the versioning machinery
working as designed; do not treat it as a licence to change semantics casually later.

### 5.2 The pinned spec objects

```python
DELEGATION_SPEC = {
  "delegation_version": "1.0.0",
  "check_order": ["root_is_human_signed","link_signature","link_order","depth_limit",
                  "budget_attenuation","merchant_attenuation","tag_attenuation",
                  "expiry_attenuation","tx_count_attenuation","envelope_disjointness"],
  "reason_codes": ["root_not_human_signed","link_signature_invalid","link_order_invalid",
                   "depth_exceeded","budget_not_attenuating","merchant_not_attenuating",
                   "tag_not_attenuating","expiry_not_attenuating","tx_count_not_attenuating",
                   "envelope_overlaps_sibling"],
  "attenuation_operator": "meet",
  "max_depth": 3,
  "delegation_semantics": "exclusive_transfer",
}

SPENDCHAIN_SPEC = {
  "spendchain_version": "1.0.0",
  "check_order": ["genesis_matches_envelope","sequence_contiguous","prev_link_matches",
                  "no_fork_in_presented_set","sum_within_envelope"],
  "reason_codes": ["genesis_mismatch","sequence_gap","prev_link_mismatch",
                   "fork_detected","envelope_overspent"],
  "link_formula": "sha256(prev_link || canonical(entry))",
  "genesis_formula": "sha256(canonical({envelope_id}))",
}
```

Both digests as given in §3.1c and §3.3c. Both MUST be asserted as literals in
`tests/test_delegation.py`.

### 5.3 Bundle amendment

A third amendment to `IMPLEMENTATION_SPEC.md` §6.2, inside the existing `authority` section — no
change to `SECTION_ORDER`, so the chain construction is untouched:

```jsonc
"authority": {
  "scheme": "native_webauthn",
  "delegation": {                                   // NEW, nullable
    "chain": [ {…DelegationLink…}, … ],             // depth 0 first
    "delegation_digest": "sha256:f4d2…",
    "envelope_id": "env_…",
    "envelope_budget_minor": 20000,
    "spend_chain": [ {…SpendEntry…}, … ],
    "spendchain_digest": "sha256:80fc…",
    "sequencer": {"type":"none"}
  },
  "policy": {…effective policy, the computed meet…},
  "policy_hash": "sha256:…"
}
```

**R5.3a** — `authority.policy` is the **effective** policy (the meet), not the root policy. The
root is recoverable as `chain[0].grant`. The compiler runs against the effective policy; that is
what "delegation is enforced" means concretely.

**R5.3b** — `delegation` is `null` for a direct, non-delegated purchase. Everything already
specified continues to work unchanged.

### 5.4 New database tables

```python
class DelegationLinkRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    link_id: str = Field(unique=True, index=True)
    parent_link_id: Optional[str] = Field(default=None, index=True)
    envelope_id: str = Field(index=True)
    depth: int
    delegate_thumbprint: str = Field(index=True)
    grant_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    jws_compact: str
    revoked: bool = Field(default=False, index=True)
    issued_at: datetime

class SpendChainEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    envelope_id: str = Field(index=True)
    sequence: int
    entry_type: str
    amount_minor: int
    ref: str
    prev_link: str
    link: str = Field(unique=True, index=True)
    jws_compact: str
    issued_at: datetime
    __table_args__ = (UniqueConstraint("envelope_id", "sequence"),)
```

**R5.4a** — The `UniqueConstraint("envelope_id", "sequence")` is what makes a *same-merchant* fork
impossible at the database level. Cross-merchant forks remain a §4 problem.

---

## 6. AAL under delegation

The human signed the root. They did not sign the leaf. That must show up in the tier.

**R6.1** — `MAX_AAL_BY_DEPTH = {0: 3, 1: 2, 2: 2, 3: 1}`, applied the same way as
`MAX_AAL_BY_SCHEME` (`INTEROP_SPEC.md` §4):

```
final = min(resolve_aal(predicates, policy_version),
            MAX_AAL_BY_SCHEME[scheme],
            MAX_AAL_BY_DEPTH[depth])
```

**R6.2** — When the depth cap binds, append `delegated_depth_capped` to `aal.reasons`. When the
unsequenced multi-merchant cap binds (§4.2), append `multi_merchant_envelope_unsequenced`. Both
extend the closed set in `IMPLEMENTATION_SPEC.md` §5.3a.

**R6.3** — A leaf agent MAY step up: the human signs a per-transaction assertion over the leaf's
actual `cart_hash` (`challenge_binding.mode = "cart"`), which restores **AAL3 at any depth**.
This is the principled statement of the whole model: *delegation trades evidence strength for
autonomy, and a human can buy the strength back one cart at a time.*

---

## 7. Multi-merchant orchestration

### 7.1 The shape

```
"6 vegan gelati for Saturday"
        │
        ▼
   OrderIntent ──▶ SourcingPlan  (§8.1, an agent)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   env_A (M1,₹300) env_B (M2,₹250) env_C (M3,₹200)   ← three SINGLE-merchant envelopes
        │               │               │              minted from one root (§4.1)
   4 units          2 units         fallback
```

**R7.1a** — Each leg MUST be a single-merchant envelope. The orchestrator MUST NOT mint one
multi-merchant envelope and reuse it across legs; that re-introduces §4's fork risk for no gain.

**R7.1b** — `Σ(leg envelopes) ≤ available(root)`. Disjointness (R3.2c) is what makes concurrent
legs safe: two legs cannot spend the same rupee because they were never given the same rupee.

### 7.2 Atomicity — the saga, and the mechanism you already built

N merchants means N independent PSP charges and no distributed transaction. The answer is a saga
with compensation, and **the compensating action already exists**: the AAL-driven hold window
from `IMPLEMENTATION_SPEC.md` §9.3, built for consumer protection, is exactly a compensation
window.

```
leg 1 → HELD (15 min)          ← compensable
leg 2 → HELD (15 min)          ← compensable
leg 3 → out of stock, fails
        ↓
compensate: cancel legs 1 and 2 inside their windows; RELEASE their envelopes
```

**R7.2a** — An orchestration MUST complete or compensate within `min(hold_seconds)` across its
legs. The orchestrator computes that deadline before executing the first leg and MUST abort the
plan if it is under 60 seconds.

**R7.2b — Ordering, and this is the sharp constraint.** AAL3 legs have `hold_seconds = 0`
(§9.3.1) and are therefore **non-compensable**. In `all_or_nothing` mode, all compensable legs
MUST execute first and non-compensable legs last. If a plan has more than one AAL3 leg it MUST be
rejected with `orchestration.multiple_noncompensable_legs` — two legs that cannot be undone
cannot be made atomic, and pretending otherwise is how a customer ends up charged for half an
order.

**R7.2c** — Compensation failure (a cancel that itself fails) MUST create an
`OrchestrationException` row, alert, and surface on the Agent Console. It MUST NOT be retried
silently. This is a money-visible inconsistency and a human needs to see it.

### 7.3 Fulfilment mode — signed, because it is the human's call

**R7.3a** — `IntentPolicy` v2 gains `fulfilment_mode`, signed with the rest:

| Value | Meaning |
|---|---|
| `all_or_nothing` | every required line item, or nothing. Saga with full compensation |
| `best_effort` | take what is available; no compensation; report the shortfall |
| `required_subset` | the SKUs in `required_skus` are all-or-nothing; the rest are best-effort |

**R7.3b** — Mode is **never** chosen by an agent. Partial fulfilment is a consumer decision — an
agent silently deciding that four of six gelati is fine is exactly the class of judgement the
whole project exists to take away from the model.

**R7.3c** — The orchestration record (one per `OrderIntent`, referencing every leg's `bundle_id`)
is retrievable at `GET /orders/intent/{intent_id}/evidence` as a bundle-of-bundles. Every leg
verifies independently; the orchestration record additionally proves the legs share a root
envelope and that the sum respects it.

---

## 8. The agents

The parts that are genuinely agent work, and both are evaluable by the swarm in
`docs/GROWTH_AGENTS.md` §2.

### 8.1 Sourcing agent (`growth/sourcing/`)

Given an `OrderIntent`, the effective policy, and the discovery documents of N merchants, produce
a `SourcingPlan`: which merchant supplies which line items, at what envelope size, in what order.

Objective, in this precedence: (1) satisfy `fulfilment_mode`; (2) minimise total cost;
(3) minimise leg count; (4) prefer merchants whose `required_aal` for the leg total is reachable
without a step-up.

**R8.1a** — Every leg of a proposed plan MUST pass `compile_decision` against the leg's envelope
**before** any envelope is minted. A plan that cannot pass is discarded, not attempted.

**R8.1b** — The plan MUST be emitted as structured data with a rationale per leg, and it goes into
`human_intent.agent_plan` (`AGENT_LAYER.md` §5.1) so a dispute can see why sourcing chose as it
did. Unsigned claim, labelled as such.

**R8.1c** — Merchant availability comes from the catalog surface. Where a merchant exposes no
stock signal, the planner MUST treat availability as unknown and MUST NOT assume in stock — an
optimistic plan produces avoidable compensation.

### 8.2 Substitution agent (`growth/sourcing/substitute.py`)

Leg fails on availability → propose an equivalent from another merchant inside the same root
budget.

**R8.2a** — Every substitution MUST pass `compile_decision` under the *new* leg's envelope, and
MUST be presented as `advisory: true`. The buyer agent decides.

**R8.2b** — A substitution that changes an item's tags MUST be flagged explicitly. Tags are
authorization inputs; a "similar" product with different tags is a different authorization
question, not a cosmetic variation.

### 8.3 What this does to the growth story

Multi-merchant sourcing turns merchants into direct competitors *for the same order*. A merchant
whose catalog is illegible to the sourcing agent does not lose a ranking position — it is never
considered at all. That raises the stakes on `docs/GROWTH_AGENTS.md` §5 from "optimisation" to
"table stakes," and it gives the swarm a second, sharper metric:

```
sourcing_win_rate = legs awarded to this merchant / plans where this merchant was eligible
```

---

## 9. Amendments to existing specs

| Doc | Section | Change |
|---|---|---|
| `IMPLEMENTATION_SPEC.md` | §3.3 | `merchant_lock` → set membership; new check 10 `spend_envelope` |
| `IMPLEMENTATION_SPEC.md` | §3.6 | `compiler_version` `1.1.0`; digest → `sha256:1023…` |
| `IMPLEMENTATION_SPEC.md` | §5.3a | + `delegated_depth_capped`, `multi_merchant_envelope_unsequenced` |
| `IMPLEMENTATION_SPEC.md` | §6.2 | + `authority.delegation` (nullable) |
| `IMPLEMENTATION_SPEC.md` | §2 | + `DelegationLinkRow`, `SpendChainEntry` |
| `IMPLEMENTATION_SPEC.md` | §7.3 | verifier gains checks 13 `delegation_chain`, 14 `spend_chain` |
| `IMPLEMENTATION_SPEC.md` | §9.1 | Policy Studio shows delegation tree + envelope allocation |
| `IMPLEMENTATION_SPEC.md` | §9.2 | Agent Console: sessions nest by envelope; freeze cascades to descendants |
| `INTEROP_SPEC.md` | §4 | final AAL = `min(resolve_aal, scheme cap, depth cap)` |
| `GROWTH_AGENTS.md` | §6 | + `sourcing_win_rate` |

**R9.1 — `merchant_id` → `merchant_ids`.** The scalar becomes a sorted tuple throughout
`IntentPolicy` and `Grant`. Free only because nothing is implemented; do it in one commit across
every doc and every fixture, never partially.

**R9.2 — Freeze cascades.** Freezing an `AgentSession` (`IMPLEMENTATION_SPEC.md` §9.2.3) MUST
also revoke every descendant delegation link. A frozen agent whose sub-agents keep spending is
attack A10 with the merchant's own console as the vector.

---

## 10. Tests

| Test | Asserts |
|---|---|
| `test_delegation.py::test_delegation_digest_is_pinned` | the §3.1c literal |
| `test_delegation.py::test_spendchain_digest_is_pinned` | the §3.3c literal |
| `test_delegation.py::test_attenuation_is_monotone` | Hypothesis: effective policy is never wider than any ancestor, on any chain |
| `test_delegation.py::test_budget_inflation_rejected` | A1 |
| `test_delegation.py::test_sibling_envelopes_cannot_exceed_parent` | A3 |
| `test_delegation.py::test_omitted_spend_entry_is_sequence_gap` | A4 |
| `test_delegation.py::test_expired_ancestor_invalidates_descendants` | A6, A10 |
| `test_delegation.py::test_scope_escalation_rejected` | A7, every field in the meet table |
| `test_delegation.py::test_depth_four_rejected` | A9 |
| `test_delegation.py::test_agent_signed_root_rejected` | R3.1b |
| `test_delegation.py::test_single_merchant_envelope_cannot_fork` | §4.1, structurally |
| `test_delegation.py::test_fork_is_detected_and_attributed` | §4.3 emits `fork_proof` with the thumbprint |
| `test_delegation.py::test_unsequenced_multi_merchant_caps_at_aal1` | §4.2 |
| `test_delegation.py::test_depth_caps_aal` | R6.1 across depths 0–3 |
| `test_delegation.py::test_leaf_step_up_restores_aal3` | R6.3 |
| `test_orchestration.py::test_all_or_nothing_compensates_all_legs` | §7.2 |
| `test_orchestration.py::test_two_noncompensable_legs_rejected` | R7.2b |
| `test_orchestration.py::test_deadline_shorter_than_60s_aborts` | R7.2a |
| `test_orchestration.py::test_concurrent_legs_cannot_overspend_root` | 10 threads across 3 legs; root invariant holds |
| `test_orchestration.py::test_agent_cannot_choose_fulfilment_mode` | R7.3b |
| `test_orchestration.py::test_freeze_cascades_to_descendants` | R9.2 |

**R10.1** — `test_concurrent_legs_cannot_overspend_root` is the headline test. It is the one that
proves the exclusive-transfer decision was correct, and it is the one to run live.

---

## 11. Demo

**D1 — Delegate, live.** Sign a ₹1,000 root policy in Policy Studio. The buyer agent spawns a
sub-agent and mints it a ₹200 single-merchant envelope. **Show the root's available budget drop to
₹800 the instant the delegation is signed, before a single rupee is spent.** *"Delegation is a
transfer, not a share. That one decision is why this verifies offline."*

**D2 — Three merchants, one budget.** "Six vegan gelati for Saturday." M1 has four, M2 has two.
Two envelopes, two legs, two bundles, one root. Show the arithmetic closing.

**D3 — A leg fails, the saga compensates.** Force M2 out of stock after M1 is held. Watch M1's
order cancel inside its hold window and its envelope release. *"The hold window I built for
consumer protection turned out to be the compensation boundary for a distributed transaction. I
did not plan that; I noticed it."*

**D4 — Try to cheat, in front of them.** Hand the judge a script that makes the sub-agent claim a
₹500 envelope from a ₹200 grant, then one that omits a spend receipt, then one that forks the
chain across two merchants. First two: rejected offline, with the reason code. Third: **allowed by
both merchants** — then run `openstore-verify --detect-forks` over the two bundles and print the
`fork_proof` naming the agent's key. *"That third one I cannot prevent without an online
coordinator, and neither can anyone else. What I can do is make it non-repudiable, and price the
weaker guarantee at AAL1 instead of pretending it is worth the same."*

**D5 — Buy the strength back.** The leaf agent, three levels down, capped at AAL2. The human signs
that one cart. AAL3, no hold, ships immediately.

---

## 12. Open questions

1. **Envelope TTL default.** Short strands less capital but forces re-delegation mid-task. No
   principled default yet; measure with the swarm before choosing.
2. **Is `max_depth = 3` right?** Chosen to bound the attack surface, not from evidence. Revisit if
   a real decomposition needs four.
3. **Sub-agent key custody.** Where does a sub-agent's ES256 key live, and what stops the parent
   from simply using the child's key and skipping the chain? (It gains nothing by doing so — but
   the answer should be written down.)
4. **Cross-root netting.** Two roots signed by the same human, at the same merchant: should they
   share a cumulative cap? Currently no. Arguably a loophole.
5. **`RELEASE` griefing.** A malicious parent could release a child's envelope mid-transaction.
   Needs a rule: release MUST NOT apply to budget already committed to a `HELD` order.
6. **Sequencer trust.** If §4.2's `holder_service` is the buyer's own wallet, is that a
   coordinator anyone should trust? Probably yes — it is the delegator's own agent — but it
   deserves a written argument, not an assumption.
7. **AP2 delegation.** `[verify]` whether AP2 mandates can express sub-delegation at all. If they
   can, the chain should be emitted in their vocabulary (`INTEROP_SPEC.md` §6.3).
