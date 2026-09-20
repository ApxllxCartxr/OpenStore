# Deterministic Merchant-Side Authorization for Agentic Commerce on Indian Rails

**Status:** draft technical note · **Date:** 2026-09-19 · **Audience:** payment providers, acquirers, NPCI

## Abstract

Agentic commerce protocols published to date assume a *delegated payment credential*: the consumer authorizes once, and an agent spends later without them present. That assumption is structurally difficult in India, where the Reserve Bank requires every domestic digital payment to be authenticated by two factors dynamically linked to the specific transaction, and where a UPI PIN is by construction entered by the payer in the payer's own PSP app.

This note describes the alternative: keep the agent proposal-only, put a deterministic authorizer on the merchant's side of the transaction, bind a single human authentication to the exact priced basket, and emit a signed, offline-verifiable evidence bundle. No agent ever holds spending authority. The design is protocol-agnostic — UCP, ACP, and AP2 become thin translations over one authorization core — and it is implementable today, on rails that exist today, without waiting for an agentic mandate framework to clear its regulator.

## 1. The problem

Three things are simultaneously true in September 2026.

Agentic traffic is arriving. Assistant surfaces can browse and build baskets, and the major commerce protocols — ACP, UCP, AP2 — are shipping merchant-side integrations.

The authentication rules tightened. The RBI (Authentication mechanisms for digital payment transactions) Directions, 2025, issued 25 September 2025 with compliance required by 1 April 2026, require two factors from separate categories, **dynamically linked to the specific transaction**, with static credentials explicitly insufficient. This applies across rails, not to cards alone.

And the national agentic framework is not available. NPCI's Unified Agent Protocol was scheduled to launch at Global Fintech Fest on 12 September 2026 and was pulled the night before, held pending RBI clearance while NPCI works through user protection, liability when an agent goes rogue, and governance standards.

The gap between the first fact and the other two is where merchants are operating right now, without guidance.

## 2. Position

The architecture follows from one separation, which the rail operator has also stated publicly. NPCI's non-executive chairman, Ajay Kumar Choudhary, at GFF 2026: *"Decision making and execution must remain separate"*, and *"AI may recommend, but authentication and final settlement must follow deterministic auditable rules"* — because *"AI is adaptive and its output is probabilistic. Payment infrastructure has to stay deterministic, auditable and final."*

Three roles, strictly separated:

- **The Buyer Agent proposes.** It is keyless and proposal-only. It may search, build a basket, and request a quote. It cannot move money, and it holds no credential that could.
- **The Gate authorizes.** A deterministic authorizer running on the merchant's own domain, single-tenant. It checks a closed set of conditions in a fixed order and fails closed. It is not a model and makes no probabilistic decision.
- **The Transcript and the sealed receipt are the auditable artifact.** Every decision the Gate made, and the basket it made them about, recoverable after the fact by a party who trusts neither the merchant nor the agent.

## 3. Mechanism

**Authority is a closed set.** `authority ∈ {tap, mandate}`. Only `tap` is accepted. `mandate` is defined, registered, recorded in the Transcript, and refused with a named code — so that a future bank-held mandate arrives as a provider adapter rather than an architecture change. The Gate's first check is `human-authority-present`; no `confirm` proceeds without a fresh tap regardless of what any credential asserts.

**The merchant quotes; the sidecar verifies.** Shipping, tax, and discount codes are one question: what is this basket's final total for this consumer at this address? The merchant answers it — it already owns prices, zones, GSTIN, HSN/SAC, and whatever code table it runs. The authorization layer ships no rate table, no tax engine, and no discount engine.

Verification replaces computation. Immediately before any money move, the Gate re-calls the merchant's quote door with the same inputs and **byte-compares** the result against the quote pinned in the `cart_hash` the human actually tapped. Any difference fails closed with `price-changed` and the consumer re-taps on the new number. This closes the window between signature and settlement — the interval in which a price can drift, an item can go out of stock, or a compromised agent can substitute a line.

**The evidence bundle.** Five sections — bought, tapped, decided, told, moved — hash-chained and merchant-ES256-signed, with the per-item attestation pinned by hash. The bundle carries its own JWKS snapshot, so verification is genuinely offline: a disputing party needs no live key lookup and no access to the merchant's systems. It opens by unguessable 128-bit receipt ID rather than login, so it can be handed to an acquirer or an ombudsman directly.

The claim this supports is narrow and checkable: *this specific human authenticated this specific priced basket at this specific time, and here is the deterministic decision record.* That is the artifact a disputed agentic transaction needs and that a delegated-credential flow cannot produce.

## 4. Relation to prior work

This design is a synthesis, not a new primitive. The honest accounting:

**AP2** (Google, announced 17 September 2025, 60+ partners) is the closest prior art and establishes the central idea. Its **Cart Mandate** has the user cryptographically sign the exact items and price, and the intent → cart → payment chain is explicitly described as a non-repudiable audit trail. Anyone claiming novelty for "human signs the cart, evidence chain results" is claiming AP2's contribution. Two differences remain. AP2 is a protocol with a distributed verification model — the Payment Mandate goes to the credential provider, networks, and processor — whereas this design puts the deterministic authorizer on the merchant's own domain as the single decision point. And AP2 equally supports human-not-present via a pre-signed **Intent Mandate**; this design refuses that mode as an invariant rather than offering it as a flow. AP2's announcement does not mention India or UPI.

**ACP** (OpenAI/Stripe) and **UCP** preserve merchant control over products, pricing, and fulfillment, and both are supported here as thin translations. Completion on gated assistant surfaces runs on delegated credentials; this design declines that trust tier and accepts redirect-completion, which converts worse. Redirect is not itself non-conformance — UCP defines buyer escalation with a `continue_url` and an embedded binding for it.

**P3P** (Pine Labs, launched 11 June 2026) is the direct Indian counterexample and the opposite pole: India's first agentic payment protocol on UPI, in which the consumer authorizes once upfront and the agent then browses, selects, negotiates, and pays with *no human authentication*, using Grantex for delegated authorization and auditability. P3P and this design answer the same question with opposite invariants. Which is correct is, at time of writing, an open regulatory question rather than a settled engineering one.

**Merchant-side reference implementations** exist and are converging: NVIDIA's Retail-Agentic-Commerce (ACP + UCP, merchant-controlled checkout), Anthropic's commerce-agents blueprint (3 September 2026, which stages merchant writes until approval and renders the cart for the host to complete), Saleor, and Shopify's Agentic Plan, itself described as a sidecar to an existing commerce stack. The sidecar shape is not distinctive.

**What is distinctive** is narrower than the architecture and worth stating precisely: pre-settlement byte-comparison of a re-fetched quote against the tapped `cart_hash`; refusal of delegation as a closed-set invariant rather than a configuration; offline verification with an embedded JWKS snapshot; and the reconciliation of all of it with Indian authentication rules, which none of the global protocols address.

## 5. What UAP changes

UAP is designed to register, verify, and authorize AI agents to transact over UPI without changing the underlying rails, building on UPI Circle delegation and Reserve Pay fund blocking. Published limits for the underlying features: UPI Circle full delegation around ₹5,000 per transaction and ₹15,000 per month; a Reserve Pay block a maximum of ₹10,000 for up to 90 days (NPCI/UPI/OC-228/2025-26, 8 October 2025, enriching NPCI/UPI/OC-200/2024-25, 31 July 2024). The same circular restricts Reserve Pay to "online verified merchants with low ticket and high frequency transactions" — an acquirer selection rule, not merely a ceiling. UAP's own limits and authentication model are unpublished.

Three consequences.

**It narrows the band rather than closing it.** Below the delegation caps, agent-side delegation is coming and will be the better experience for reorder-shaped purchases; that trade is lost and should be conceded plainly. Above them, per-spend authentication bound to the cart remains what the rules expect and what a merchant wants when an order is disputed. Note that a ₹15,000 monthly ceiling covers a substantial share of Indian D2C baskets — this band is narrower than a first reading suggests, and sizing it against real basket distributions is prerequisite to any commercial claim.

**It takes the agent-identity layer.** Agent registration and verification become NPCI infrastructure. Merchant-side self-registration schemes should expect to be superseded there and should not be over-invested in.

**It does not make a store transactable.** UAP governs payment authority. Catalog truth, stock, quoting, tax, order state, and the dispute artifact remain merchant-side problems, unaddressed by any payment protocol. When UAP ships, it should reach this design the way every instrument does — through the payment provider declaring its methods — as a second member of the authority closed set, not a reversal of the invariant.

The most useful observation is about the hold itself. RBI is reported to be holding UAP over user protection, liability when an agent goes rogue, and governance standards. Those are not payment-rail problems; they are evidence and accountability problems. A deterministic, auditable, merchant-side decision record is a partial answer to exactly the question blocking the protocol.

## 6. Limits and open questions

- Where the authentication line actually falls between delegated and per-spend flows is a legal question. No claim here that per-spend tap is *the* compliant path should be made to a counterparty without an Indian regulatory opinion.
- Redirect-completion converts worse than native in-agent completion. This is a real, unquantified cost, and it is accepted deliberately.
- The evidence bundle's value depends on an acquirer or adjudicator accepting it. That acceptance does not exist yet and has to be built.
- Single-merchant scope is assumed throughout: one deploy, one merchant domain. Batch anchoring and multi-merchant aggregation are deliberately out.
- The band sizing in §5 is unverified against real basket data.

## Sources

- Google Cloud, "Announcing Agent Payments Protocol (AP2)", 17 September 2025; ap2-protocol.org
- Business Standard, "UPI's AI protocol temporarily on hold as NPCI builds safeguards", 11 September 2026
- Pine Labs, P3P launch, 11 June 2026
- NPCI/UPI/OC-228/2025-26, 8 October 2025; NPCI/UPI/OC-200/2024-25, 31 July 2024
- RBI (Authentication mechanisms for digital payment transactions) Directions, 2025, 25 September 2025
- Ajay Kumar Choudhary, NPCI, remarks at Global Fintech Fest 2026
- NVIDIA Retail-Agentic-Commerce; Anthropic commerce-agents (3 September 2026); agenticcommerce.dev
