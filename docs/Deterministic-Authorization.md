# Deterministic Merchant-Side Authorization for Agentic Commerce on Indian Rails

Formerly `docs/NOTE-deterministic-authorization.md`. Renamed during the documentation reorganization.

Status: draft technical note. Date: 2026-09-19. Audience: payment providers, acquirers, NPCI.

## Abstract

Agentic commerce protocols published to date assume a delegated payment credential. The consumer authorizes once. An agent spends later without the consumer present. That assumption is structurally difficult in India.

The Reserve Bank requires two factors for every domestic digital payment. The factors must link dynamically to the specific transaction. A UPI PIN by construction enters in the PSP app of the payer.

This note describes the alternative. Keep the agent proposal-only. Put a deterministic authorizer on the side of the merchant. Bind one human authentication to the exact priced basket. Emit a signed offline-verifiable evidence bundle. No agent ever holds spending authority.

The design is protocol-agnostic. UCP, ACP, and AP2 become thin translations over one authorization core. It is implementable today on rails that exist today. It does not wait for an agentic mandate framework to clear its regulator.

## 1. The problem

Three things are true at once in September 2026.

Agentic traffic is arriving. Assistant surfaces can browse and build baskets. The major commerce protocols, ACP, UCP, and AP2, are shipping merchant-side integrations.

The authentication rules tightened. The RBI (Authentication mechanisms for digital payment transactions) Directions, 2025, issued 25 September 2025. Compliance was required by 1 April 2026. The Directions require two factors from separate categories.

The factors must link dynamically to the specific transaction. Static credentials are explicitly insufficient. This applies across rails, not to cards alone.

The national agentic framework is not available. The Unified Agent Protocol from NPCI was scheduled to launch at Global Fintech Fest on 12 September 2026. NPCI pulled it the night before. It waits on RBI clearance while NPCI works through user protection, liability when an agent goes rogue, and governance standards.

Merchants operate right now in the gap between the first fact and the other two, without guidance.

## 2. Position

The architecture follows from one separation. The rail operator also stated it publicly. At GFF 2026, Ajay Kumar Choudhary, non-executive chairman of NPCI, stated that decision making and execution must remain separate. He stated that AI can recommend, but authentication and final settlement must follow deterministic auditable rules. He gave the reason that AI is adaptive and its output is probabilistic. He stated that payment infrastructure must stay deterministic, auditable and final.

Three roles, strictly separated:

- Buyer Agent proposes. It is keyless and proposal-only. It can search, build a basket, and request a quote. It cannot move money. It holds no spending credential.
- Gate authorizes. Gate means a deterministic authorizer that runs on the own domain of the merchant, single-tenant. It checks a closed set of conditions in a fixed order. It fails closed. It is not a model. It makes no probabilistic decision.
- Transcript and sealed receipt form the auditable artifact. Transcript means the decision record of the Gate. Sealed receipt means the basket record. A party that trusts neither merchant nor agent can recover both after the fact.

## 3. Mechanism

Authority is a closed set. `authority ∈ {tap, mandate}`. Only `tap` is accepted. `mandate` is defined, registered, and recorded in the Transcript. The Gate refuses it with a named code.

A future bank-held mandate then arrives as a provider adapter, not an architecture change. The first check of the Gate is `human-authority-present`. No `confirm` proceeds without a fresh tap, regardless of what any credential asserts.

Merchant quote and sidecar check. Shipping, tax, and discount codes are one question. What is the final total of this basket for this consumer at this address. The merchant answers it.

The merchant already owns prices, zones, GSTIN, HSN/SAC, and whatever code table it runs. The authorization layer ships no rate table. It ships no tax engine. It ships no discount engine.

Verification replaces computation. Immediately before any money move, the Gate calls the quote door of the merchant again with the same inputs. It byte-compares the result against the quote pinned in the `cart_hash` the human actually tapped. Any difference fails closed with `price-changed`. The consumer then re-taps on the new number.

This closes the window between signature and settlement. In that interval a price can drift. An item can go out of stock. A compromised agent can substitute a line.

Evidence bundle. The bundle has five sections:

- Bought
- Tapped
- Decided
- Told
- Moved

The sections are hash-chained and merchant-ES256-signed. Attestation per item is pinned by hash. The bundle carries its own JWKS snapshot. Verification is therefore genuinely offline.

A disputing party needs no live key lookup. It needs no access to systems of the merchant. The bundle opens by unguessable 128-bit receipt ID rather than login. It can pass directly to an acquirer or an ombudsman.

The supported claim is narrow and checkable. This specific human authenticated this specific priced basket at this specific time. Here is the deterministic decision record. That artifact is what a disputed agentic transaction needs. A delegated-credential flow cannot produce it.

## 4. Relation to prior work

This design is a synthesis, not a new primitive. The honest accounting follows.

AP2 (Google, announced 17 September 2025, 60+ partners) is the closest prior art and establishes the central idea. Its Cart Mandate has the user cryptographically sign the exact items and price. The intent → cart → payment chain is explicitly described as a non-repudiable audit trail. Anyone who claims novelty for human signs the cart, evidence chain results is claiming the contribution of AP2.

Two differences remain. AP2 is a protocol with a distributed verification model. The Payment Mandate goes to the credential provider, networks, and processor. This design puts the deterministic authorizer on the own domain of the merchant as the single decision point.

AP2 equally supports human-not-present through a pre-signed Intent Mandate. This design refuses that mode as an invariant rather than offering it as a flow. The announcement of AP2 does not mention India or UPI.

ACP (OpenAI/Stripe) and UCP preserve merchant control over products, pricing, and fulfillment. Both are supported here as thin translations. Completion on gated assistant surfaces runs on delegated credentials. This design declines that trust tier and accepts redirect-completion.

Redirect-completion converts worse. Redirect is not itself non-conformance. UCP defines buyer escalation with a `continue_url` and an embedded binding for it.

P3P (Pine Labs, launched 11 June 2026) is the direct Indian counterexample and the opposite pole. It is the first agentic payment protocol in India on UPI. The consumer authorizes once upfront. The agent then browses, selects, negotiates, and pays with no human authentication.

It uses Grantex for delegated authorization and auditability. P3P and this design answer the same question with opposite invariants. Which answer is correct is at time of writing an open regulatory question, not a settled engineering one.

Merchant-side reference implementations exist and are converging. They include Retail-Agentic-Commerce from NVIDIA (ACP plus UCP, merchant-controlled checkout). They include the commerce-agents blueprint from Anthropic (3 September 2026, which stages merchant writes until approval and renders the cart for the host to complete). They include Saleor and the Agentic Plan from Shopify, itself described as a sidecar to an existing commerce stack. The sidecar shape is not distinctive.

What is distinctive is narrower than the architecture. State it precisely:

- Pre-settlement byte-comparison of a re-fetched quote against the tapped `cart_hash`
- Refusal of delegation as a closed-set invariant rather than a configuration
- Offline verification with an embedded JWKS snapshot
- Reconciliation of all of it with Indian authentication rules, which none of the global protocols address

## 5. What UAP changes

UAP is designed to register, verify, and authorize AI agents to transact over UPI without change to the underlying rails. It builds on UPI Circle delegation and Reserve Pay fund blocking. Published limits for the underlying features follow.

UPI Circle full delegation is around ₹5,000 per transaction and ₹15,000 per month. A Reserve Pay block is a maximum of ₹10,000 for up to 90 days (NPCI/UPI/OC-228/2025-26, 8 October 2025, enriching NPCI/UPI/OC-200/2024-25, 31 July 2024). The same circular restricts Reserve Pay to online verified merchants with low ticket and high frequency transactions. That is an acquirer selection rule, not merely a ceiling. The own limits and authentication model of UAP are unpublished.

Three consequences follow.

It narrows the band rather than closing it. Below the delegation caps, agent-side delegation is coming. It will be the better experience for reorder-shaped purchases. Concede that trade plainly.

Above the caps, per-spend authentication bound to the cart remains what the rules expect. It remains what a merchant wants when an order is disputed. A ₹15,000 monthly ceiling covers a substantial share of Indian D2C baskets. This band is narrower than a first reading suggests. Sizing it against real basket distributions is prerequisite to any commercial claim.

It takes the agent-identity layer. Agent registration and verification become NPCI infrastructure. Teams must expect NPCI infrastructure to supersede merchant-side self-registration schemes. Do not over-invest in them.

It does not make a store transactable. UAP governs payment authority. Catalog truth, stock, quoting, tax, order state, and the dispute artifact remain merchant-side problems. No payment protocol addresses them.

When UAP ships, it must reach this design the way every instrument does. The payment provider declares its methods. UAP then enters as a second member of the authority closed set, not a reversal of the invariant.

The most useful observation concerns the hold itself. RBI is reported to hold UAP over user protection, liability when an agent goes rogue, and governance standards. Those are not payment-rail problems. They are evidence and accountability problems. A deterministic auditable merchant-side decision record is a partial answer to exactly the question that blocks the protocol.

## 6. Limits and open questions

- Where the authentication line actually falls between delegated and per-spend flows is a legal question. Do not present per-spend tap as the compliant path to a counterparty without an Indian regulatory opinion.
- Redirect-completion converts worse than native in-agent completion. This is a real unquantified cost. It is accepted deliberately.
- The value of the evidence bundle depends on acceptance by an acquirer or adjudicator. That acceptance does not exist yet. Teams must build it.
- Single-merchant scope is assumed throughout. One deploy serves one merchant domain. Batch anchoring and multi-merchant aggregation are deliberately out.
- The band sizing in §5 is unverified against real basket data.

## Sources

- Google Cloud, Announcing Agent Payments Protocol (AP2), 17 September 2025. ap2-protocol.org
- Business Standard, UPI AI protocol temporarily on hold as NPCI builds safeguards, 11 September 2026
- Pine Labs, P3P launch, 11 June 2026
- NPCI/UPI/OC-228/2025-26, 8 October 2025. NPCI/UPI/OC-200/2024-25, 31 July 2024
- RBI (Authentication mechanisms for digital payment transactions) Directions, 2025, 25 September 2025
- Ajay Kumar Choudhary, NPCI, remarks at Global Fintech Fest 2026
- NVIDIA Retail-Agentic-Commerce. Anthropic commerce-agents (3 September 2026). agenticcommerce.dev
