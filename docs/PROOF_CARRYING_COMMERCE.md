# Brutal audit, and the contribution that isn't table stakes

Two parts. **Part 1** scores OpenStore as it stands against the track it is entered in, from
the point of view of someone who reads the agentic-commerce specs for a living. It is harsh
because a soft read is useless to you now. **Part 2** is the actual contribution — a gap
that AP2, ACP, Visa TAP, and Mastercard Agent Pay all leave open, why NPCI, Razorpay and
Stripe structurally cannot ignore it, and a design that fits in the days you have left.

`[verify]` marks any claim about an external spec, product, or regulation. This space moved
fast through 2025–26; check the primary source before any of it goes in a submission.

---

# Part 1 — The audit

## 1.1 The thesis sentence is the weakest thing in the project

> "Razorpay's live pilots let an AI buyer act for a human against a conventional merchant
> backend. **Nobody has standardised the merchant side.**"

That sentence is the load-bearing claim in `plan.md`, `Day 0.md`, and the README. It is the
one a judge will test first, and `[verify]` it is no longer true:

- **ACP (Agentic Commerce Protocol)** — OpenAI + Stripe, open-sourced 2025. It is
  *explicitly* a merchant-side specification: a product feed spec, a checkout-session API the
  merchant implements, and a delegated payment token spec. That is precisely "standardising
  the merchant side," shipped by the two parties with the most distribution in the room.
- **AP2 (Agent Payments Protocol)** — Google + 60-odd partners, 2025. Intent Mandate and Cart
  Mandate: a human-signed statement of authority, and a per-transaction binding to a specific
  cart. That is OpenStore's Intent Compiler thesis, published, with a spec and reference code.
- **Visa Trusted Agent Protocol / Intelligent Commerce**, **Mastercard Agent Pay** — network-
  side agent identity and mandate registration.
- **Web Bot Auth** (Cloudflare/Stripe-adjacent) — HTTP Message Signatures so a merchant can
  identify an agent at the edge.

So the honest read of OpenStore today is: **a competent third implementation of AP2's Intent
Mandate, with WebAuthn as the signing method, wired to Razorpay.** That is a real engineering
achievement in nine days. It is not a contribution. A judge who has read AP2 will place it
within ninety seconds, and everything after that lands as execution, not insight.

## 1.2 The cryptographic novelty claim is worse than neutral — it is behind an existing standard

The differentiator you claim over AP2 is "we implement it with WebAuthn, the authenticator
billions of devices already have." `[verify]` W3C **Secure Payment Confirmation (SPC)** is a
WebAuthn extension built for exactly this: authorising a *payment* with a passkey, where the
browser renders a transaction dialog — amount, payee, instrument — inside the ceremony, and
the resulting signature covers **what the user was shown**.

That last property is the entire point, and OpenStore does not have it. Your ceremony signs
`SHA-256(canonical policy JSON)` — an opaque hash. The human sees your HTML page, but nothing
cryptographically attests to what they saw; a compromised page shows one policy and submits
another. SPC solves that; you reinvented the ceremony and dropped the security property.

It gets sharper. Compare what each scheme's signature actually authorises:

| Scheme | Signature covers | Replayable? |
|---|---|---|
| 3DS / SPC | this transaction: amount, merchant, instrument | No |
| AP2 Cart Mandate | this cart, hash-bound | No |
| Your OTP fallback | this `checkout_id` + `cart_hash` | No — `jti` burned |
| **Intent Compiler (shipped)** | a policy blob, reusable forever | **Yes, unlimited** |

`latest-assertion` hands the agent a bearer artifact with no expiry, no per-transaction
binding, no counter check, and no revocation. **You replaced a per-transaction human act with
a long-lived bearer token and called it stronger.** For a broad class of carts inside the
policy, it is weaker than the OTP path you deprecated. That is the single most damaging
sentence a hostile expert can say about this project, and it is currently true.

And it is moot anyway, because `verify_assertion_policy()` in `merchant/webauthn.py:40` is
never called from `checkout_confirm` — grep the repo. The money path checks a hash and an
agent-supplied credential ID. Today, forging authorisation requires no key and no human.
(Detail, exploit and fix: `docs/PRODUCTION_READINESS.md` §0.1–0.2. Fix it regardless of
anything in this document — it is four lines and the whole thesis rests on it.)

## 1.3 There is no answer to "why would a merchant deploy this?"

Every payments person asks this inside a minute, and OpenStore has no answer.

Read the value flow: the *buyer* sets a policy. The *merchant* buys, runs, and pays for the
infrastructure that enforces the buyer's constraints on the buyer's agent. The merchant's
return is a **smaller** cart (the compiler only ever rejects) and a new attack surface.
Enforcing the customer's spending limits is a service to the customer, rendered at the
merchant's expense.

This is the deepest problem in the project and it is not technical. A protocol nobody has an
incentive to run does not become a standard, no matter how correct it is. The history is
unambiguous: **EMV and 3DS were not adopted because merchants admired the cryptography.
They were adopted because a network rule reassigned who pays for fraud, and the cryptography
was the price of admission to the better side of that rule.** `[verify]` the US EMV liability
shift (Oct 2015) moved counterfeit-fraud liability to whichever party had not upgraded, and
terminal adoption went from marginal to majority in about eighteen months.

Every agentic-commerce spec published so far has shipped the cryptography and skipped the
liability rule. That is the actual reason adoption is stalled, and it is the opening.

## 1.4 Scoring against the track

Track: *AI Growth & Agentic Commerce*, arm: *"makes a merchant transactable by an AI buyer
end to end."*

| Dimension | Score | Why |
|---|---|---|
| Working end-to-end system | **8/10** | Three processes, real OAuth 2.1 AS, real MCP, real money. Most teams will not have this. Genuinely strong. |
| Engineering craft | **7/10** | Server-side repricing, cart versioning, idempotency, audit trail, the AST isolation test. Undercut by §1.2's unverified money path. |
| Track fit | **9/10** | Exactly the stated arm. No stretch required. |
| **Novelty** | **3/10** | AP2's Intent Mandate re-implemented. The one differentiator (WebAuthn) is behind SPC. |
| **Insight into the domain** | **4/10** | Correct primitives, no argument about incentives, adoption, disputes, or why any of this is currently stalled. |
| Demo strength | **7/10** | Fingerprint correlation, idempotent retry, prompt injection are good beats — all *defensive*. Nothing new exists at the end. |
| **Memorability** | **3/10** | "Agent shops within a signed budget." Every judge will hear that phrase from several teams. |

Novelty, insight, and memorability are the three that decide a winner. They are your three
lowest. That is the honest picture, and you were right to call the current build table stakes.

## 1.5 What the failure-mode demos actually prove

Day 7's beats — idempotent retry, over-cap rejection, cart tampering, mandate replay, prompt
injection — are the best-designed part of the plan and they will land well. But notice what
they have in common: **every one of them is a demonstration that nothing bad happened.**
Blocked, rejected, refused, ignored. They are a competence signal, not a contribution. A
judge leaves impressed by your rigour and unable to name a thing that exists now which did
not exist before.

The prompt-injection beat is the exception with real teeth — *"the defence is architectural,
not a prompt"* is a genuine insight, well staged. Keep it. But it argues for a property
(keep the LLM out of the authorisation path) that AP2 also argues for.

---

# Part 2 — The gap, and what to build

## 2.1 Every spec stops at "authorised." Commerce doesn't.

Read AP2, ACP, TAP and Agent Pay next to each other and the shape of the omission is
obvious. All four define, in varying detail:

- how an agent proves who it is
- how a human delegates authority to it
- how that delegation binds to a transaction
- how the payment credential is presented

Then they stop. **None of them define what happens when it goes wrong** — and in commerce,
"wrong" is not an edge case, it is a standing 60-to-120-day obligation. The customer disputes
the charge. The goods are wrong. The agent misread the request. The human says *"I never
asked for this."*

For a human transaction, the merchant answers that with a body of evidence the whole industry
knows how to weigh: an IP, a device fingerprint, a delivery confirmation, an AVS match, a
3DS ECI value. Thin evidence, but a *shared, adjudicable format*.

For an agent transaction there is **nothing**. The party that made the purchasing decision is
a stochastic model whose reasoning is preserved nowhere in the transaction record, running
under an authority whose scope is written in a document no dispute process has ever seen. A
merchant asked *"should I let agents buy from me?"* today has to answer *"I have no idea what
happens when one of them is disputed, and I have no way to defend it."*

**So they block agent traffic.** That is the actual bottleneck in agentic commerce, and it is
a liability problem wearing a technology costume.

The gap in one line:

> **Agentic commerce has an authorization layer and no adjudication layer.**

## 2.2 The reframe: the Intent Compiler's real asset is determinism, not cryptography

OpenStore already contains the one thing this gap needs, and the project has not noticed it.

`verify_cart_against_policy()` is a **pure function**: cart × policy × catalog → verdict. No
LLM, no clock beyond an explicit timestamp, no network, no hidden state. Which means its
verdict is **reproducible by anyone, offline, at any point in the future, from the inputs
alone.**

Nothing else in agentic commerce has that property. An LLM's purchase decision is not
reproducible — run it twice, get two answers, and neither can be re-derived by a third party
in a dispute nine weeks later. That irreproducibility is exactly why merchants can't defend
agent transactions.

So the contribution is not "we sign policies with passkeys." It is:

> **Move the purchasing decision out of the model and into a pure function, then make the
> transaction carry a portable proof that the function was run, on these exact inputs, and
> returned this exact verdict — verifiable by any party, offline, without trusting the
> merchant.**

That is **proof-carrying commerce**, and the name is deliberate: proof-carrying code (Necula,
1997) had a host that could not trust a binary, so the binary shipped with a machine-checkable
proof of its own safety, and the host verified the proof instead of trusting the producer.
Same structure here. An acquirer, an issuer, an arbitrator, and a customer cannot trust a
merchant's account of what an agent was authorised to do — so the transaction carries a
machine-checkable proof, and they verify it instead.

## 2.3 The primitive: three parts

### Primitive 1 — AAL: Agentic Authorization Level

A deterministic tier, computed at authorization time from evidence **actually present**, that
states what kind of human authority stood behind this purchase. This is the economic half —
the thing that gives a merchant a reason to deploy any of it.

Nine boolean predicates, each independently checkable from the bundle:

| | Predicate | Established by |
|---|---|---|
| E1 | `agent_authenticated` | valid OAuth token, required scope |
| E2 | `policy_signature_valid` | WebAuthn assertion verifies against the enrolled credential |
| E3 | `assertion_fresh` | assertion age < policy-declared max |
| E4 | `user_verified` | UV bit set in `authenticatorData` |
| E5 | `cart_bound` | the signed challenge covers **this** `cart_hash` |
| E6 | `catalog_attested` | every line item covered by a merchant signature predating the cart |
| E7 | `compiler_allow` | deterministic verdict = ALLOW, with transcript |
| E8 | `intent_recorded` | hash of the human's original request is in the bundle |
| E9 | `notified` | pre-debit notification delivered, with receipt |

| Tier | Condition | Meaning | Proposed liability |
|---|---|---|---|
| **AAL0** | ¬E2 | No human cryptographic authority exists | Merchant |
| **AAL1** | E2 ∧ (¬E3 ∨ ¬E6) | Human authorised *something*; binding is stale or the catalog is self-certified | Merchant |
| **AAL2** | E1–E4, E6–E9 ∧ ¬E5 | Policy-bound autonomous purchase, fully evidenced, no per-transaction human act | Shared / issuer, by rule |
| **AAL3** | all, incl. E5 | Fresh user-verified assertion over **this exact cart** | Cardholder-authenticated equivalent — full shift |

Four things this buys that nothing in the project currently has:

- **It is falsifiable.** Not a marketing tier — a boolean expression anyone can evaluate from
  the bundle and disagree with you about.
- **It grades your own system honestly.** OpenStore ships at **AAL0** today (§1.2: E2 is never
  evaluated). After the four-line fix it reaches AAL2. Only the step-up flow reaches AAL3.
  A framework whose first published measurement is a failing grade for its own author reads
  as real. Lead the demo with it.
- **It makes step-up economically motivated.** "₹4,000 requires a fingerprint" stops being a
  nice UX beat and becomes *"this cart crosses the threshold where I want the liability
  shifted, so I buy AAL3 with two seconds of the customer's time."* Merchants understand that
  trade instantly; it is the 3DS decision they already make daily.
- **It gives the merchant a reason to run the software.** Not "enforce your customer's
  budget" but **"accept agent traffic you are currently blocking, with a defensible position
  when it is disputed."** That is a P&L sentence.

### Primitive 2 — PoAI: the Proof of Authorized Intent bundle

One self-contained, hash-linked JSON document per transaction. Portable, offline-verifiable,
producer-untrusted.

```jsonc
{
  "poai_version": "0.1",
  "bundle_id": "poai_01J…",
  "transaction": { "merchant_id": "gelateria-roma", "checkout_id": "…",
                   "amount_minor": 42000, "currency": "INR",
                   "psp": { "provider": "razorpay", "order_id": "order_…",
                            "payment_link_id": "plink_…" } },

  "human_intent": {                       // E8 — the fidelity evidence (§2.5)
    "request_digest": "sha256:…",         // hash of the human's original words
    "request_text": "a vegan gift under ₹500",
    "captured_at": "2026-08-27T09:14:22Z",
    "channel": "discord", "channel_message_id": "…" },

  "authority": {                          // E2–E5 — what the human actually signed
    "policy": { /* the IntentPolicy, verbatim */ },
    "policy_hash": "sha256:…",
    "webauthn": { "credential_id": "…", "aaguid": "…",
                  "client_data_json": "b64u…", "authenticator_data": "b64u…",
                  "signature": "b64u…", "uv": true, "sign_count": 41,
                  "challenge_binding": { "mode": "policy" | "cart",
                                         "cart_hash": "sha256:…" } },
    "enrolment": { "attestation_format": "packed",
                   "enrolled_at": "…", "enrolment_ceremony_digest": "sha256:…" } },

  "goods": {                              // E6 — kills merchant self-certification (§2.4)
    "cart_hash": "sha256:…", "cart_version": 3,
    "items": [ { "sku": "GEL-VAN-500", "qty": 2, "unit_minor": 21000,
                 "tags": ["vegan","dairy-free"],
                 "catalog_attestation": "eyJhbGciOiJFZERTQSJ9…" } ] },

  "agent": {                              // E1
    "client_id": "client_…", "display_name": "OpenStore Buyer",
    "scopes": ["cart:write","checkout:confirm"],
    "consent_granted_at": "…", "token_jti": "…" },

  "adjudication": {                       // the replayable core (§2.6)
    "compiler_version": "1.2.0",
    "compiler_digest": "sha256:…",        // content hash of the decision procedure
    "policy_schema_version": 1,
    "evaluated_at": "2026-08-27T09:15:02Z",
    "verdict": "ALLOW",
    "transcript": [
      { "check": "merchant_lock", "expected": "gelateria-roma", "actual": "gelateria-roma", "result": "pass" },
      { "check": "policy_expiry", "expires_at": 1788… , "now": 1787…, "result": "pass" },
      { "check": "blocked_skus",  "sku": "GEL-VAN-500", "result": "pass" },
      { "check": "tag_allowlist", "mode": "all", "item_tags": ["vegan","dairy-free"],
        "allowed": ["vegan","dairy-free"], "result": "pass" },
      { "check": "spend_per_tx",  "total_minor": 42000, "limit_minor": 50000, "result": "pass" },
      { "check": "spend_cumulative", "spent_minor": 12000, "total_minor": 42000,
        "limit_minor": 100000, "result": "pass" } ] },

  "notification": { "sent_at": "…", "channel": "discord",
                    "receipt_digest": "sha256:…" },        // E9

  "aal": { "level": 2, "predicates": { "E1": true, …, "E5": false },
           "reasons": ["no_per_transaction_binding"] },

  "chain": { "links": ["sha256:…", "sha256:…"],            // §2.6 hash linking
             "root": "sha256:…",
             "merchant_signature": "eyJhbGciOiJFZERTQSIsImtpZCI6…",
             "time_anchor": { "type": "rekor", "log_index": 1234567,
                              "inclusion_proof": "…" } }    // §2.7
}
```

Every field is data OpenStore already produces or is one commit from producing. The
contribution is not the data — it is that the data is **assembled, hash-linked, signed,
anchored, and independently verifiable.**

### Primitive 3 — the verifier, and the property that makes it undismissable

A standalone package — `openstore-verify` — that imports **nothing** from `merchant/`.

```console
$ openstore-verify poai_01J8Z….json --merchant-jwks merchant.jwks.json

  chain integrity ................ ok   6 links, root sha256:9f2a…
  merchant signature ............. ok   kid=merchant-key-1, EdDSA
  time anchor .................... ok   rekor log_index 1234567, 2026-08-27T09:15:04Z
  webauthn assertion ............. ok   ES256, uv=1, sign_count 41 > 40
  challenge → policy binding ..... ok   sha256(policy) == signed challenge
  catalog attestations ........... ok   2/2 items, all issued before cart creation
  compiler digest ................ ok   1.2.0  sha256:4d1c…  (pinned)
  RE-EXECUTING DECISION PROCEDURE
    merchant_lock ................ pass
    policy_expiry ................ pass
    tag_allowlist (mode=all) ..... pass
    spend_per_tx ................. pass   ₹420.00 ≤ ₹500.00
    spend_cumulative ............. pass   ₹540.00 ≤ ₹1000.00
  re-derived verdict ............. ALLOW  ==  claimed verdict  ✓
  AAL ............................ 2      ==  claimed  ✓   (E5 false: no per-tx binding)

  VERDICT REPRODUCED. Bundle is internally consistent and independently verified.
  Liability position under the proposed rule: SHARED / ISSUER.
```

Four properties, and they are the whole argument:

1. **Offline.** No network, no merchant API, no merchant cooperation, works with the merchant
   out of business.
2. **Producer-untrusted.** Security rests on the human's authenticator signature and the hash
   chain, not on the merchant's word. The merchant cannot forge E2; the merchant cannot
   backdate E6; the merchant cannot silently alter a transcript.
3. **Durable.** The compiler is pinned by content digest, the policy schema is versioned. A
   verdict rendered today re-derives bit-identically in 2031.
4. **Adversarial-proof by construction.** Change one paise anywhere and a named link breaks,
   with the exact field printed.

You already have the test that proves the isolation claim — `tests/test_isolation.py` walks
the AST and fails on a forbidden import. Point it at `openstore_verify/` too. *"The verifier
cannot import the thing it verifies, and here is the test that enforces it"* is the same
discipline you already used on the buyer agent, applied where it matters most.

## 2.4 Why catalog attestation matters more than it looks

The compiler enforces `allowed_tags` against tags the **merchant** publishes in its own YAML.
As designed, this is self-certification: a merchant who mislabels alcohol as `dessert` defeats
the human's policy, and a merchant defending a dispute in November can edit October's tags.
AP2 has the same hole — it assumes merchant catalog data is trustworthy.

The fix is small and the property is real. At cart creation, the merchant signs each line
item's facts with a timestamp:

```python
# merchant/attest.py — reuses the Ed25519 key already in mandate.py
def attest_catalog_item(sku, price_minor, tags, catalog_digest) -> str:
    return jws_sign({"sku": sku, "price_minor": price_minor, "tags": sorted(tags),
                     "catalog_digest": catalog_digest, "iat": now()}, alg="EdDSA")
```

Now the tag claim is a **dated, signed commitment**. The merchant can still lie — but only
*before* the sale, in a signed artifact, and only in a way that binds them when the customer
disputes. Retroactive lying becomes impossible. That converts a trust assumption into a
liability the merchant knowingly accepts, which is exactly what an evidence standard is for.

Same idea, one level up: publish the daily catalog digest. Then "these tags were what the
whole world could see on that date" is checkable by anyone.

## 2.5 The fidelity gap — name it, because nobody else has

A policy bounds **what may be bought**. It says nothing about **whether it is what was asked
for**. A policy permitting "vegan, ≤₹500" does not stop an agent asked for a birthday cake
from buying vegan gelato. Cryptography cannot close this: no signature over a policy can
attest that the agent understood the request.

Do not pretend to solve it. **Make it adjudicable** — which is a genuine contribution,
because the alternative is that the evidence simply does not exist.

The bundle carries three layers, and their relationship is the dispute:

```
human_intent.request_text        "a vegan gift under ₹500"        ← what was asked
authority.policy                 vegan ∧ ≤₹500 ∧ gelateria-roma   ← what was permitted
goods.items                      2 × GEL-VAN-500 @ ₹210           ← what was bought
```

Layers 2→3 are **machine-adjudicable**: the compiler decides deterministically and the
verifier re-derives it. Layers 1→2 are **human-adjudicable**: an arbitrator reads the
request, the policy, and the cart, and forms a judgement — the same thing dispute analysts
already do all day, except that for the first time the evidence exists in one signed place.

Stating that boundary precisely — *here is exactly where machine verification stops and human
judgement must begin, and here is the evidence handed across the line* — is a stronger
intellectual move than pretending an LLM-scored "intent match" is a security control. Every
technical judge has seen a team put an LLM judge on a security boundary. Naming why you
refused to is a differentiator.

## 2.6 Determinism is a build requirement, not a description

The claim "anyone can re-derive this verdict in 2031" is only true if you engineer for it.
Four rules, all cheap, all testable:

1. **The compiler is a pure function of the bundle.** No wall clock (`evaluated_at` is an
   input), no DB reads (cumulative spend is an input), no catalog reads (attested item facts
   are inputs). Today `verify_cart_against_policy` calls `product_lookup` and `time.time()`;
   both become parameters. That is a twenty-line refactor and it converts an implementation
   detail into the system's core property.
2. **The compiler is content-addressed.** `compiler_digest = sha256(intent_compiler.py)`,
   emitted into every bundle, asserted in CI. The verifier refuses a bundle whose digest it
   does not implement, and says so, rather than silently re-adjudicating under different
   rules. Silent semantic drift is the failure mode that destroys an evidence standard.
3. **Canonicalisation is pinned by golden vectors.** Committed `(object, sha256)` fixtures
   including unicode, empty lists, and key-order permutations. Without them, a refactor
   silently invalidates every bundle ever issued. `[verify]` the plan cites RFC 8785 (JCS);
   `mandate.py` implements *JCS-style*, not JCS — no Unicode normalisation, different number
   handling. Either use a JCS library or describe precisely what you built. Do not cite a
   spec you have not implemented; the one person in the room who knows JCS will check.
4. **Hash-link the sections.** Each section's digest includes the previous:
   `link[i] = sha256(link[i-1] || canonical(section[i]))`, root signed by the merchant. Now
   tampering is not merely detectable — it is **locatable**, which is what makes the live
   tamper demo devastating rather than abstract.

## 2.7 Time, and why the merchant cannot backdate

A dispute is a fight about *when*. A merchant who can regenerate a bundle after the fact can
manufacture evidence, so the bundle's timestamps must be anchored outside the merchant.

`[verify]` **Sigstore Rekor** is a public append-only transparency log with a free public
instance and a small client; submit the bundle root, get back a log index and an inclusion
proof, embed both. Now anyone can prove the bundle existed at that log position and could not
have been altered since. RFC 3161 timestamping is the traditional alternative.

Fallback if either is fiddly on demo day: publish a daily Merkle root of all bundle roots to
any public immutable surface and include the inclusion path. Cheaper, same property.

The sentence this earns is one no other team will say: **"the merchant cannot backdate this,
and here is the public log entry that proves it."**

## 2.8 Interoperate; do not compete

Do not position OpenStore against AP2 or ACP. You lose that fight on distribution and it is
the wrong fight — they solved authorization, which you need.

Position it **above** both:

```
  AP2 / ACP / TAP  ──▶  authorization    (who may spend, bound to what)
  PoAI             ──▶  adjudication     (what happens when it is disputed)
```

Concretely: `merchant/interop/` emits the same authorisation facts as an AP2 Intent Mandate
and Cart Mandate, and consumes an ACP-shaped checkout session, so the bundle wraps a
**standards-native** authorization rather than a bespoke one. `openstore-verify` accepts a
bundle whose `authority` section is an AP2 mandate instead of your own.

That reframes the whole project: *"AP2 and ACP tell you how an agent gets permission. Neither
tells you what to hand an arbitrator ninety days later. This is that layer, and it works on
top of both."* Nobody can dismiss it as a competing standard, because it composes with theirs.

## 2.9 Why each of the three cannot ignore it

**Razorpay.** Their customer is the merchant, and the merchant's question right now is
literally *"should I let agents buy from me?"* Razorpay's honest answer today is *"we can't
tell you what happens if it's disputed."* PoAI turns that into a product: agent transactions
arrive with an AAL tier and an evidence bundle, the merchant's risk position is legible before
they ship, and Razorpay's dispute-ops cost per agent transaction goes down instead of up. It
also gives them something to take to the networks — a merchant-side artifact that a liability
rule could key off. Razorpay is an acquirer with a large Indian merchant base and a real
interest in being the party that defines that artifact rather than implementing someone
else's.

**Stripe.** They co-authored ACP's authorization half. Radar and chargeback protection are
product lines — they already sell adjudication as a business. The evidence layer above ACP is
their obvious next build, and a working reference implementation with an offline verifier is
the fastest way to be part of that conversation rather than a spectator.

**NPCI.** `[verify]` UPI dispute resolution runs on their rails; e-mandate already requires
pre-debit notification and accessible revocation. If delegated or agentic payment constructs
land on UPI without an evidence standard, dispute volume on a national rail rises with no
defined artifact for adjudicating it — an operational and political exposure, not a merchant
inconvenience. The e-mandate mapping is exact and worth putting on one slide:

| e-mandate | OpenStore + PoAI |
|---|---|
| AFA once at mandate registration | WebAuthn policy signing ceremony (E2–E4) |
| Mandate limits: amount, validity, frequency | `IntentPolicy` fields |
| Debits within limits, no per-debit AFA | Compiler ALLOW at AAL2 |
| Above-threshold debit requires AFA | Step-up to AAL3 |
| Pre-debit notification | E9, with a signed delivery receipt |
| Revocation | `active=False`, and a bundle after revocation is AAL0 |
| **Dispute over a debit** | **PoAI bundle — the piece that does not exist today** |

The regulatory argument writes itself: India already decided that authenticate-once-then-
transact-within-limits is acceptable for recurring payments. Agentic commerce is the same
shape. The missing piece in both is the artifact you hand an arbitrator.

## 2.10 What a hostile expert will say, and the answers

**"You can't unilaterally assign liability — networks do that."**
Correct, and the document should say so before they do. You are not assigning liability; you
are specifying **the evidence artifact and the tiering a rule would key off**, plus a
reference implementation and an independent verifier. That is exactly how 3DS started: a
scheme spec plus merchant-side plugins, with the liability rule following adoption. The claim
is "here is the artifact the rule will need," not "here is the rule."

**"Why would anyone trust evidence the merchant produced?"**
They don't trust it — they verify it. The load-bearing facts are signed by the *human's*
authenticator (unforgeable by the merchant), the catalog attestations are dated *before* the
sale, and the root is in a public log. What remains under merchant control is whether to
produce a bundle at all — and **absence defaults to AAL0, which is merchant-liable.** Honest
evidence is the merchant's dominant strategy. That incentive alignment is a design feature and
worth stating as one.

**"A compromised agent can still spend inside the policy."**
Yes, and no scheme prevents that — AP2 doesn't either. What changes is that the spend is
bounded, notified (E9), revocable, and leaves a proof showing exactly which authority was
exercised. That is the difference between an unbounded loss and a bounded, evidenced,
remediable one. Say the limit plainly; the plan's §7 already has the right instinct.

**"This is just structured logging."**
Structured logs are (a) merchant-controlled, (b) unverifiable by third parties, (c) not
reproducible, (d) alterable. A PoAI bundle is externally verifiable, offline, from a
producer you do not trust, and its central claim — the verdict — is **re-executed**, not
read. A log tells you what a system says it did. This lets you re-run the decision.

**"Isn't SPC already this?"**
SPC is transaction-bound authentication — an input to E5, and a good one, worth adopting.
It produces no evidence bundle, no tiering, no verifier, and says nothing about *what* was
bought or whether it was inside a delegated authority.

## 2.11 The front-facing half: four surfaces, one function

Everything above is a proof. **A proof nobody can operate is not a product**, and a hackathon
judge cannot hold a hash chain. The evidence layer has four distinct human parties, at four
distinct moments, and none of them currently has anywhere to stand:

| Party | Moment | Question they are asking | Surface today |
|---|---|---|---|
| The customer | before signing | "what am I actually authorising?" | a 5-field form with no feedback |
| The merchant | while agents shop | "what are these agents doing in my store?" | nothing |
| The customer | seconds after a purchase | "wait — I didn't want that" | nothing |
| An arbitrator | 90 days later | "was this authorised?" | nothing |

The unifying property, and the thing that makes these surfaces cheap rather than four
separate products: **`verify_cart_against_policy` is the same pure function in all of them.**

```
                     ┌─ Policy Studio ......... run it forward over the whole catalog → preview
compile(cart,policy) ─┼─ checkout_confirm ...... run it once on the real cart      → enforce
                     └─ openstore-verify ...... run it again from the bundle       → adjudicate
```

One decision procedure, three moments: **preview, enforce, replay.** That the customer's
signing-time preview is generated by the identical, content-addressed function that later
blocks the cart and later still re-derives the verdict in a dispute is a property no other
system in this space has — and it is the reason the surfaces are honest rather than
decorative. A preview computed by a *different* code path than the enforcement is marketing.

### Surface 1 — Policy Studio (`/intent/studio`): consent you can actually give

**The problem it fixes is the project's own.** OpenStore's founding critique of OTP is that
per-transaction approval "trains people to click approve without reading." True. But the
replacement — a form with five fields and a passkey prompt — trains people to **sign a
policy they cannot evaluate**, which is the same failure moved one level up and made
permanent. Signing `allowed_tags: ["vegan"]` against a catalog you have not enumerated is
consent in form only. That gap is currently the softest part of the whole thesis and a good
judge will find it.

Policy Studio closes it. The human sets constraints and sees, live, against the real catalog,
exactly what they are authorising — computed by the compiler itself, not by a description of
it:

- **Every product, badged.** `ALLOWED` or `BLOCKED`, each blocked item carrying the compiler's
  own reason code — `tag_violation`, `sku_blocked`, `spend_per_tx_exceeded`. Drag the cap
  slider, toggle a tag, and the grid re-badges as you watch.
- **Blast radius, in numbers.** Reachable SKUs, the most expensive single item an agent could
  buy, the largest number of units one cart could hold, total exposure over the policy's life,
  and how long that life is. This is the sentence the human is actually agreeing to:
  *"until Sept 3, your agent can spend up to ₹500 per purchase and ₹2,000 in total, across
  these 6 of 12 products."*
- **An example worst-case basket.** Not a bound — a concrete, named cart the policy permits,
  built greedily from the most expensive reachable items. People reason about examples.
- **A diff before you re-sign.** Change an existing policy and see `+2 products reachable,
  −₹300 per-transaction exposure` before the passkey prompt.

Nobody has built policy-comprehension tooling for agentic commerce, and the specs are silent
on it — AP2 defines the mandate's fields, not whether a human can understand what they signed.
For a regulator, "informed consent to a delegated authority" is the entire question, and this
is the only artifact in the space that even attempts to demonstrate it.

### Surface 2 — Agent Console (`/admin/agents`): the merchant's first look at agent traffic

Merchants today cannot distinguish an agent from a scraper, cannot see what agents attempt,
and cannot stop one without blocking a whole IP range. That is *why* they block agents. Give
the merchant the operational view they would ask for on day one:

- **Live sessions**, keyed by `(client_id, policy_hash)`: which agent, acting under whose
  policy, at what AAL tier, how much of that policy's budget is left, what it is doing right
  now.
- **The rejection feed as the headline.** Not a footnote — the main panel. *What agents tried
  to do and were stopped from doing*, streaming, with reason codes. This is the inversion
  that makes it a security product rather than a dashboard: a merchant watching a spike of
  `tag_violation` from one `client_id` is watching a compromised agent in real time, and no
  other agentic-commerce implementation surfaces that at all.
- **Per-session freeze.** One button, immediate, scoped to that session — not an IP ban.
  Freezing sets the session inactive; the next tool call fails closed with
  `agent.session_frozen`, and any bundle issued after the freeze is AAL0.
- **AAL mix over time.** What proportion of your agent revenue is defensible. That single
  chart is the one a merchant's finance lead cares about, and it is the number that makes
  them turn agent traffic *on*.

### Surface 3 — Hold & Cancel: reversibility as a primitive, priced by evidence

Every spec in this space treats authorization as a one-way door. Real consumer payments do
not: there are cooling-off periods, pre-debit notifications, revocation. Agentic commerce
needs the same, and nobody has proposed it.

After `checkout_confirm`, the order enters `HELD`. The payment link exists, but fulfilment is
blocked and the human gets a notification with a **Cancel** button that genuinely works —
cancel and the order goes `CANCELLED`, the payment link is cancelled, the ledger releases the
reservation. Let the window lapse and it moves to `RELEASED`.

The elegant part is that the hold duration is **driven by the AAL tier**, which unifies the
whole design:

| Tier | Hold | Why |
|---|---|---|
| AAL3 | **0 s** | the human authenticated *this exact cart* seconds ago — nothing to reconsider |
| AAL2 | 15 min | policy-bound autonomy; the human never saw this specific cart |
| AAL1 | 60 min | weak binding; give them real time |
| AAL0 | blocked | no human authority exists; do not ship at all |

So the human's involvement **buys immediacy**, and evidence strength now drives three
different things at once — liability, reversibility, and speed — from one number. It also
lands the regulatory story precisely: this is RBI's pre-debit notification and revocation
requirement, implemented, with the notification's delivery receipt hashed into the bundle as
predicate E9.

### Surface 4 — Evidence Viewer: a verifier a judge can hold

`openstore-verify` on a terminal is for engineers. The same verification, in a **single
self-contained HTML file that runs with no network and no server**, is for everyone else:

- Drop a bundle on the page. Every check renders green or red as it runs — chain integrity,
  merchant signature, WebAuthn assertion, catalog attestations, and then the decision
  procedure **re-executing**, check by check, in front of you.
- **A tamper control.** Edit any field in the page and watch the chain break, with the exact
  link, section, and field named. Change a catalog tag to retroactively justify a purchase and
  watch it fail on the attestation's timestamp instead.
- **A plain-language verdict.** "On 27 Aug 2026, this customer authorised purchases of up to
  ₹500 from vegan products at Gelateria Roma. This ₹420 cart was inside that authority. The
  merchant did not certify these tags after the fact. AAL2."

Everything runs on WebCrypto — which is why the spec pins every PoAI-layer signature to
**ES256/P-256**, the curve `crypto.subtle` verifies natively. That is a real design
constraint chosen for this surface, not an afterthought: it is what makes zero-dependency,
offline, third-party verification possible at all.

### The connective tissue — the merchant publishes a policy too

Right now only the buyer has a policy. Give the merchant one, at
`/.well-known/agent-policy.json` with a rendered `/agents` page: which AAL tier this merchant
requires at which basket size, its agent rate limits, its hold windows, its dispute terms.

Two things fall out of it, and both are front-facing:

- **Negotiation before rejection.** The buyer agent reads it at discovery and knows *before
  building a cart* that ₹4,000 here will require a step-up. The customer gets "your agent
  needs your fingerprint for this one" instead of a rejection after the fact.
- **A fit badge on the storefront.** With a policy signed, every product page shows whether
  the customer's own agent could buy this — `✓ your agent can buy this` /
  `✗ outside your policy: tag_violation`. The security model becomes visible on the shopping
  surface, to the shopper, at the moment it is relevant.

Bilateral, published, machine-readable policy with the enforcement running over the
*intersection* is the shape an actual standard takes, and it is one line of argument away from
robots.txt — a comparison every engineer in the room will get instantly.

## 2.12 Build plan

Feature freeze is end of Day 8 (Wed 2 Sep). This lands inside it because it **absorbs** Day 7
rather than adding a day: every adversarial test you were already going to write becomes an
AAL demonstration, doing double duty.

| # | Work | Files | Hours |
|---|---|---|---|
| 0 | **Fix the money path.** Call `verify_assertion_policy`; verify the frozen snapshot, not the live cart. Nothing below is true without this. | `mcp_server.py` | 2 |
| 1 | Make the compiler pure: clock and item facts become parameters; add `compiler_digest`; golden vectors | `intent_compiler.py` | 2 |
| 2 | Catalog attestation at cart creation | `attest.py` (new, ~60 ln) | 2 |
| 3 | AAL predicates + tier function, pure and unit-tested | `aal.py` (new, ~90 ln) | 2 |
| 4 | Bundle assembly: hash links, merchant signature, transcript emission | `evidence.py` (new, ~180 ln) | 4 |
| 5 | **`openstore-verify`** — standalone package + CLI, zero merchant imports, AST-enforced | `openstore_verify/` (~250 ln) | 5 |
| 6 | Step-up ceremony: per-transaction assertion over `cart_hash` → AAL3 | `intent_routes.py` | 3 |
| 7 | Capture `human_intent` from Discord; deliver + receipt the pre-debit notification | `bot.py`, `notifier.py` | 2 |
| 8 | Time anchor (Rekor, with the Merkle-root fallback) | `evidence.py` | 2 |
| 9 | `GET /orders/{id}/evidence` + a download button on the audit page | `app.py` | 1 |
| 10 | Publish the bundle JSON Schema + version-negotiation rules from the spec | `openstore_verify/schema/` | 3 |
| 11 | AP2/ACP interop shim for the `authority` section | `interop/` | 2 |
| 12 | **Policy Studio** — blast-radius engine + `/intent/studio` page | `blast_radius.py`, `storefront/intent_studio.html` | 5 |
| 13 | **Agent Console** — live sessions, rejection feed, per-session freeze, AAL mix | `agent_console.py`, `storefront/agents_console.html` | 5 |
| 14 | **Hold & Cancel** — `HELD` state, AAL-driven window, working Cancel action | `hold.py`, `notifier.py` | 4 |
| 15 | **Evidence Viewer** — single-file offline HTML verifier with tamper control | `openstore_verify/viewer.html` | 5 |
| 16 | Merchant agent-policy + `/agents` page + storefront fit badge | `app.py`, `storefront/` | 3 |

≈ 52 hours across the evidence layer and the four surfaces. Rows 0–5 are the spine: nothing
else is true without them. Rows 12–15 are what a judge can touch.

**Every row above is specified to the field, the signature, and the exit code in
`docs/IMPLEMENTATION_SPEC.md`.** That document is the build contract — normative field names,
exact canonicalisation, the full reason-code enum, route-by-route request and response shapes,
the blast-radius algorithm, and a test manifest. It is written so that an implementer (human or
model) never has to guess: anything it does not specify is listed in its §11 as a question to
ask rather than a blank to fill. Build from the spec; use this document for the argument.

## 2.13 The demo that cannot be dismissed

Replace Beats 7–9. Seven minutes. Every beat is something the judge looks at or touches —
there is no beat where you describe a property instead of showing it.

**Beat A — grade yourself first.** Run `openstore-verify` on a bundle from *last week's*
build. It prints **AAL0 — `policy_signature_valid: false`**. *"My own system failed its own
standard. The assertion was verified during the browser ceremony and not at the money path.
Here is the four-line fix, and here is the test that fails against that commit."* Nobody else
in the competition will open by failing themselves, and everyone in the room has shipped that
bug.

**Beat A2 — sign a policy you can actually read.** Open **Policy Studio**. Drag the cap to
₹500, tick `vegan`. The catalog re-badges live: 6 of 12 products go `ALLOWED`, the rest carry
the compiler's own reason codes. Read the blast radius out loud — *"until 3 September, my
agent can spend ₹500 a purchase and ₹2,000 total, across these six products, and here is the
most expensive basket it could build."* Then the passkey prompt. *"That preview was generated
by the identical function that will block the cart later and re-derive the verdict in a
dispute. Not a description of the rule — the rule."*

**Beat B — a normal agent purchase, watched from the merchant's side.** ₹420 from Discord,
no human in the loop, with the **Agent Console** on screen: the session appears, budget
drains, the tool calls stream. Then have the agent try to add a non-vegan item and let the
audience watch it land in the rejection feed as `tag_violation` in real time. Bundle emitted.
**AAL2.** The order enters **HELD** for 15 minutes and the customer's phone buzzes with a
working **Cancel** button — press it, and show the order dying and the budget returning.
*"Reversibility, priced by evidence: a human who signs this exact cart gets no hold at all."*

**Beat C — kill the server.** `Ctrl-C` the merchant. Then hand the judge two files — the
bundle and `viewer.html` — and let them open it **on their own laptop, with wifi off and my
server dead.** Every check renders green as it runs, the decision procedure re-executes in
front of them, and the plain-language verdict prints. *"That decision is now checkable by
anyone, forever, with no cooperation from me. My infrastructure does not exist and the
transaction still defends itself."*

**Beat D — let them tamper.** Hand over the keyboard. Have the judge edit a `unit_minor` in
the viewer's own tamper control and watch the chain break, with the exact link, section and
field named. Then the subtle one: edit a *catalog tag* to retroactively justify the purchase —
it fails on the attestation timestamp, because those tags were signed before the sale.
*"A merchant cannot rewrite history in their own favour, and you just proved it without
trusting me."*

**Beat E — step-up, and the money sentence.** ₹4,000. Same agent, same policy — and this
time the agent knew in advance, because it read `/.well-known/agent-policy.json` at discovery
and told the customer "this one needs your fingerprint" *before* building the cart. Sign over
that exact cart hash. **AAL3** — no hold, ships immediately. Show the AAL mix chart in the
Agent Console ticking up. Then say the thing the whole project has been missing:

> *"Merchants block agents today because agent traffic is undefended liability. This makes an
> agent transaction more defensible than a human one — because a human clicking 'buy' leaves
> no proof of what they intended, and this leaves a portable cryptographic one. EMV and 3DS
> both prove merchants adopt cryptography exactly when it changes who pays for fraud. Every
> agentic spec shipped so far has the cryptography and no liability rule. This is the missing
> half."*

**Beat F — the honest limits**, per §2.14. Keep the plan's instinct: after the above, stating
the boundary reads as confidence.

## 2.14 Honest limits — state all of them

- **You cannot assign liability.** You specify the artifact and the tiering; a network or a
  regulator assigns. Say it in the first minute, not under questioning.
- **AAL2's liability position is a proposal, not a rule.** Nobody has agreed to it. Present
  it as "what a rule keyed off this evidence could reasonably say."
- **The fidelity gap (§2.5) is unsolved and unsolvable by cryptography.** You make it
  adjudicable, not decidable.
- **Catalog attestation binds the merchant; it does not make them honest.** It converts
  retroactive lying into pre-dated, signed, liable lying.
- **A compromised agent still spends within the policy.** Bounded, notified, revocable,
  evidenced — not prevented.
- **A public time anchor leaks metadata** — transaction timing and volume. Anchor a salted
  root, not the bundle itself, and say so.
- **PoAI is not a privacy design.** The bundle contains what was bought and, at present, the
  human's request text. A real deployment needs selective disclosure — issue the bundle with
  hashed sections and reveal only what a given dispute requires. Name it as designed-for, not
  built.
- **One merchant, one PSP, test mode, single region.** Everything here is a reference
  implementation, and the value is in the format and the verifier, not this deployment.

---

## The one paragraph, rewritten

Replace the thesis in `plan.md`, `Day 0.md`, and the README with this. The old one makes a
claim a judge can falsify in ninety seconds; this one makes a claim they cannot:

> AP2, ACP, Visa TAP and Mastercard Agent Pay all specify how an AI agent gets permission to
> spend. None of them specify what a merchant hands an arbitrator ninety days later when the
> customer says "I never asked for this" — and that missing piece, not the cryptography, is
> why merchants block agent traffic today. OpenStore makes the purchasing decision a pure,
> content-addressed function instead of a model output, and emits every transaction with a
> hash-linked, publicly time-anchored **Proof of Authorized Intent**: the human's WebAuthn
> assertion, the merchant's pre-dated catalog attestations, the agent's authority chain, and
> the full decision transcript. Any party can re-execute that decision offline, years later,
> with the merchant's servers switched off, and get a bit-identical verdict — and a
> deterministic **Agentic Authorization Level** that states what kind of human authority stood
> behind the purchase. Authorization is a moment; adjudication is a ninety-day obligation.
> Everyone has shipped the moment. This is the obligation.

---

*Money-path defects referenced in §1.2 (with exploits, fixes, and the tests that catch them),
plus the payments-hygiene work any of this rests on: `docs/PRODUCTION_READINESS.md`.*
