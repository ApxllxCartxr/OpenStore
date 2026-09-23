# Proof-Carrying Commerce

Formerly `docs/BRIEF-stakeholder-overview.md`. Renamed during the documentation reorganization.

OpenStore: stakeholder briefing.
Prepared 20 September 2026. Audience: new stakeholder, no prior context. Scope: concept, problem, landscape.

Software agents now buy things for people. The same companies that own demand wrote the protocols that let them do this. OpenStore is the verification and discovery layer. It makes a purchase by an agent provable to anyone. It puts no new platform between buyer and merchant.

---

## In brief

When an artificial intelligence agent buys for you, three questions lack a machine-checkable answer today:

- Did a human actually authorize this exact purchase, at this exact price, from this exact merchant?
- Was the bought item the item the merchant actually offered?
- When the order goes wrong, what evidence exists and who is liable?

The current answer to all three is trust the platform. OpenStore replaces that answer with signed portable proof. The proof travels with the order. Any third party can verify it offline without trust in OpenStore. Third parties include:

- Merchant
- Bank
- Payment service provider
- Insurer
- Court
- The buyer

---

## Part one: The problem

### Agentic commerce is arriving with a weaker trust model than the web it replaces

For thirty years, authority for online payment rested on a human present at purchase. The human typed a card number. The human entered a One-Time Password. The human confirmed a Unified Payments Interface Personal Identification Number.

Every dispute-resolution process rests on that presence. Every chargeback rule rests on it. Every liability-shift framework in card and account payments rests on it.

An autonomous agent breaks the assumption. The agent is not the human. It can act minutes or weeks after the last message from the human. It can act on a loose instruction such as restock my kitchen under four thousand rupees. The record it leaves is a conversation transcript inside a proprietary product. No party outside that product can check that transcript as evidence.

### Problem one: authority is a platform assertion, not a fact

In every shipping agentic-commerce system today, the merchant must trust the demand platform. The platform signs the request with its own credential. The statement has the form we, a large company, assert a user approved this.

That statement is a contractual assurance. It is not a cryptographic fact. The merchant cannot check it independently. The merchant cannot re-check it six months later during a dispute. The merchant cannot check it at all if the platform declines to cooperate.

### Problem two: the merchant integrates once per aggregator, forever

A merchant who wants reach by agents needs separate work for each demand platform. The work includes:

- A separate catalogue feed
- A separate checkout endpoint
- A separate agent-profile registration
- A separate commercial agreement

Discovery is granted, not earned. If the platform did not on-board a merchant, agents of that platform never find the merchant. For the small and medium merchant, the cost exceeds the revenue it unlocks. Examples include a Shopify store, a WooCommerce install, a WhatsApp Business catalogue, or a spreadsheet. These merchants stay absent from the agentic channel.

### Problem three: Payment rules in India do not bend for agents

The Reserve Bank of India mandates an Additional Factor of Authentication for digital payment transactions. In practice, the factor is a Unified Payments Interface Personal Identification Number entered in a certified application. An equivalent device-bound factor also satisfies the rule.

No regulator-blessed path exists today for silent completion of an arbitrary purchase by an agent with no human factor. Systems built on card-network delegation assumptions do not transfer cleanly to this rule. An honest agentic-commerce design in India must treat the human tap as a constraint. Teams must engineer around it, not ignore it.

---

## Part two: The landscape, protocol by protocol

These are not competitors in a single category. They occupy different layers. A working system needs several of them at once. The layers include:

- Transport
- Catalogue
- Payment authorization
- Settlement rail

Each protocol below states what it does and the specific gap it leaves.

### Model Context Protocol

Steward: Anthropic. Open specification, November 2024.

Model Context Protocol is an open standard. It connects a language model to external systems. It defines a JavaScript Object Notation Remote Procedure Call (version 2.0) message format. The format travels over standard input/output streams or streamable Hypertext Transfer Protocol.

A server exposes three kinds of capability to a client:

- Tools, or callable functions with typed input schemas
- Resources, or readable data addressed by a Uniform Resource Identifier
- Prompts, or reusable templates

It is now the de-facto way for an agent to reach any external system, commerce included.

> Where it stops: Model Context Protocol is a connection protocol with no commerce semantics. It has no concept of money, price, inventory, identity, authorization, consent, or proof. It tells you how to call a function. It says nothing about permission to spend eight thousand rupees. Every commerce guarantee must be built on top of it.

### Agent-to-Agent Protocol (published as "A2A")

Steward: Google, contributed to the Linux Foundation.

Agent-to-Agent Protocol is a peer protocol. It lets independent agents from different vendors discover each other and delegate work. Each agent publishes an Agent Card. Agent Card means a machine-readable document at a well-known Uniform Resource Locator. The document declares identity, skills, endpoints, and authentication requirements.

Work moves as a task with a defined lifecycle over JavaScript Object Notation Remote Procedure Call. Lifecycle states are submitted, working, input-required, completed, and failed.

> Where it stops: like Model Context Protocol, it transports capability, not authority over money. It discovers capability, not payment permission. It is a necessary base for multi-party agent commerce. It is not sufficient alone. Payment work by Google sits as an extension on top of it.

### Agentic Commerce Protocol

Steward: OpenAI with Stripe. Open specification, 2025.

Agentic Commerce Protocol powers Instant Checkout inside ChatGPT. It has three distinct parts.

1. A product feed specification. The merchant publishes a structured catalogue. The assistant surfaces items from it.
2. A Checkout Session application programming interface over Representational State Transfer. The agent creates a session. The agent updates it with buyer address and selected fulfillment. The agent completes it. The merchant returns authoritative pricing, tax, and availability at every step. The agent never invents a price.
3. The Delegated Payment Specification. The system exchanges the stored payment method of the buyer for a Shared Payment Token. Shared Payment Token means a single-use credential scoped to amount and merchant. The merchant charges through its own payment service provider. The merchant stays the merchant of record. The merchant keeps the customer relationship and bears the settlement.

> Where it stops: the buyer-agent side belongs in practice to one company. Trust is bilateral and contractual. The merchant trusts an assertion by OpenAI that a human approved the order. The protocol produces no artefact for handoff to a third party months later. It cannot prove what the human agreed to.
>
> Payment is card-rail-shaped through a Shared Payment Token. That shape does not map onto the Unified Payments Interface. There, no card exists to delegate. The authentication factor must be collected inside a certified application.
>
> Discovery also stays with the aggregator to grant. A place in the catalogue is an onboarding decision by the platform. It is not a property a merchant can establish alone.

### Universal Commerce Protocol

Steward: Shopify, with Google and other launch partners. 2025.

Universal Commerce Protocol is an agent-facing surface over commerce primitives that Shopify already operates. It covers catalogue and product data. It covers a cart that an agent can construct and mutate. It covers a checkout hand-off. Agents authenticate as registered clients under OAuth 2.0. Merchants opt in to agents that can transact against the store.

Its strength is real inventory behind it. Millions of live storefronts provide accurate stock, pricing, tax, and fulfillment. That is a working commerce network, not a specification exercise.

> Where it stops: any buyer agent cannot yet participate in practice. OAuth 2.0 clients need advance registration. Participation is a permissioned club with an application process. Dynamic Client Registration (Internet Engineering Task Force Request for Comments 7591) is not the operative path. That registration standard will make any agent literally true. It does not yet do so.
>
> Completion is also not agent-native. The flow ends in a redirect to a hosted checkout. The human taps there every single time. That choice is defensible. But the protocol does not solve unattended purchase. Systems built on it must state this fact rather than imply otherwise.
>
> Its center of gravity is the Shopify-hosted merchant. The WooCommerce store, the custom site, and the WhatsApp Business catalogue are not first-class citizens.

### Agent Payments Protocol (published as "AP2")

Steward: Google, as an extension to the Agent-to-Agent Protocol. 2025.

Agent Payments Protocol is the most advanced of the group in concept. It is closest in spirit to OpenStore. Its central idea is the mandate. Mandate means a cryptographically signed verifiable credential that captures authority. It takes three forms:

- The Intent Mandate. It captures what the human authorized in the abstract before the specific item is known. Example: buy these running shoes in size nine if they drop below six thousand rupees in the next thirty days. It makes delegated human-absent purchase expressible.
- The Cart Mandate. It captures the exact basket with exact line items and exact prices. The human signs it while present. It is the non-repudiable record of yes, this, at this price.
- The Payment Mandate. It passes to the payment network. It signals agent involvement and human presence. Issuers and networks use it to price risk.

Mandates build on World Wide Web Consortium Verifiable Credentials. The protocol is deliberately rail-agnostic. It is designed to carry card payments, real-time account-to-account payments, and stablecoin settlement equally.

> Where it stops: it is a specification and a reference sample, not a running network. It has no discovery layer. It has no merchant on-boarding path. It has no catalogue. It has no verifier.
>
> The protocol describes proofs that a verifier can check. It names nobody to check them. No merchant back-office today consumes a Cart Mandate.
>
> It leaves merchant-side integration deliberately open. That part is the hardest and least glamorous work.
>
> Its story in India is unwritten. Nobody did the work to map an Intent Mandate onto a Unified Payments Interface mandate, a fund block, or an Additional Factor of Authentication event.

### x402 (over Hypertext Transfer Protocol status 402)

Steward: Coinbase. Open specification, 2025.

x402 revives the long-reserved Hypertext Transfer Protocol status code 402 Payment Required. A server responds to an unpaid request with 402 plus a machine-readable description of wanted payment. The client pays and retries with a payment header. Payment is typically in a dollar-denominated stablecoin on a low-fee chain.

Settlement is near-instant. Fees are fractions of a cent. No account or subscription exists. It fits machine-to-machine micropayments, paid application programming interface calls, and per-inference billing.

> Where it stops: it is not consumer retail commerce. It has no chargeback. It has no dispute process. It has no consumer-protection framework. It has no Goods and Services Tax invoice.
>
> A consumer in India buying groceries will not settle in stablecoin. A merchant in India cannot legally price in one. It solves payment by an agent for a data feed. It does not solve purchase by an agent of a kilogram of coffee.

### Unified Payments Interface

Steward: National Payments Corporation of India. Live at national scale.

Unified Payments Interface is the real-time account-to-account payment rail in India. It is the rail that matters for this project. Three facilities are directly relevant:

- Unified Payments Interface Autopay. It creates a recurring electronic mandate with a ceiling amount and a validity period. The payer approves once. Debits follow on schedule.
- Unified Payments Interface Reserve Pay. It provides the single-block, multiple-debit facility. The payer blocks funds in its own account. The money stays with the payer and earns interest until debit.
- The Unified Payments Interface Personal Identification Number. Entered in a certified application, it serves as the Additional Factor of Authentication.

Two details matter enormously and many misunderstand them. Reserve Pay is not card-style authorize-then-capture. The semantics differ. The reversal behavior differs. The permitted debit patterns differ. A design that treats it as a hold on a card fails in production.

An Autopay mandate binds the payer to a payee with a cap. By itself, it does not encode what was bought.

> Where it stops: Unified Payments Interface knows that money moved from one account to another. It knows nothing about carts, items, prices, agents, or consent. A mandate proves permission by a payer for a payee to draw up to a limit. It does not prove agreement by the human to three specific bought items. That gap between payment authorized and purchase authorized is the gap OpenStore fills.

### Web Authentication (World Wide Web Consortium "WebAuthn", part of FIDO2)

Steward: World Wide Web Consortium and the FIDO Alliance. Universally deployed.

Web Authentication is the standard behind passkeys. A device generates a private key in hardware. The key never leaves the device. The server issues a random challenge. The authenticator signs it after verification of the user by biometric or device Personal Identification Number. The signature binds to the challenge and to the requesting origin.

The signature carries a user-verified flag. The flag distinguishes active confirmation by a human from mere presence of a key. The design resists phishing by construction. A signature made for one origin has no value at another.

> Where it stops: Web Authentication proves approval by a human of a challenge. It says nothing about the meaning of that challenge. A useful signature must bind to a specific canonically-serialized statement of purchase authority. That binding is an application-layer design decision. The standard leaves it entirely open. Almost everybody gets it wrong by signing an opaque nonce with no committed content.

### Verifiable Credentials and JSON Web Signature

Steward: World Wide Web Consortium and the Internet Engineering Task Force.

These are the envelope formats. The Verifiable Credentials Data Model defines an issuer, a subject, a claim set, and a cryptographic proof. A third party can check the credential without contact with the issuer. JSON Web Signature and its detached form provide the concrete signing container. These formats make a proof portable. Anyone with the public key can check it months later, offline.

> Where it stops: they are containers, not content. They define how to sign a claim. They leave the full question to the application. Which claims constitute sufficient authority for a purchase. How a verifier decides.

### The landscape at a glance

| Protocol | Layer it occupies | Third-party verifiable proof | Open discovery | Works on Unified Payments Interface |
| --- | --- | --- | --- | --- |
| Model Context Protocol | Agent-to-system transport | None | n/a | n/a |
| Agent-to-Agent Protocol | Agent-to-agent transport | None | Yes | n/a |
| Agentic Commerce Protocol | Catalogue, cart, checkout, payment delegation | Platform assertion | Aggregator-granted | Card-shaped |
| Universal Commerce Protocol | Catalogue, cart, checkout hand-off | Platform assertion | Pre-registered | Via redirect |
| Agent Payments Protocol | Payment authorization semantics | By design | No layer | Unmapped |
| x402 | Machine settlement rail | On-chain only | Yes | No |
| Unified Payments Interface | Consumer settlement rail | Payment only | n/a | Native |
| OpenStore | Proof, verification, discovery | Core purpose | Permissionless | Native |

---

## Part three: What we are building

### OpenStore: a proof layer and an index, not a marketplace

We are not building another demand platform. We do not compete head-on with the Agentic Commerce Protocol or the Universal Commerce Protocol. We speak them. We add what none of them provides: an independent checkable record of authority, plus reach for any merchant, not only pre-approved ones.

- Component one: Intent Compiler. Intent Compiler means a tool that turns loose instruction into bounded policy. It takes input such as order the usual coffee if it is under nine hundred rupees. It compiles a bounded machine-checkable authorization object. The language model writes the policy but never enforces it. The human reviews the compiled policy in plain words before signing.
  - The object holds a spending ceiling, a merchant allow-list, a category restriction, a quantity limit, a validity window, and a revocation handle.
- Component two: Proof of Authorized Intent bundle. Proof bundle means the artefact that travels with every order. It contains four parts. A hash binds every element to the others. No part can be swapped after the fact.
  - Compiled intent policy signed by the passkey of the human over a server-issued single-use challenge.
  - Cart snapshot that commits to exact line items, quantities, prices, currency, and merchant identity.
  - Signed attestation by the merchant that this catalogue entry at this price was genuinely offered.
  - Payment authorization reference.
- Component three: Deterministic authorization. Deterministic authorization means a purchase decision as a pure function of proof and policy. No language model sits in the authorization path. This is the most important architectural commitment. A model that can be persuaded cannot decide whether money moves. The model composes, negotiates, and explains, while arithmetic and signature checks authorize.
- Component four: Independent verifier. Verifier means a standalone tool and library that returns a verdict on a proof bundle. It uses only public keys. It needs no network call to OpenStore, no account, and no trust in OpenStore. This lets a lawyer for the merchant, a risk team of a bank, or a sceptical stakeholder check claims directly. A protocol with proofs that only its own author can verify is not a protocol.
- Component five: Merchant adapters. Adapters are connectors for storefronts that already exist. They cover Shopify, WooCommerce, and a plain comma-separated-values catalogue upload for merchants with no platform at all. A merchant becomes agent-reachable without a rebuild. Catalogue and stock are handled separately. A stale price is a bug and a stale stock count is a refund.
- Component six: Index, not mall. We never take custody of funds. Money settles merchant-direct over the Unified Payments Interface. The merchant remains the merchant of record. This is a legal position as much as a technical one.
  - A place on the money path will make us an Electronic Commerce Operator under Indian tax law. That status brings Tax Collected at Source and Goods and Services Tax duties on every transaction. It will make us the exact intermediary we exist to remove.

### How a purchase actually flows

1. State an intent. The human states it in natural language to the agent in current use. The agent need not be ours.
2. Compile a bounded policy. The Intent Compiler produces ceiling, allow-list, category, quantity, and expiry. The human approves it in plain words.
3. Sign once with a passkey. The human signs by Web Authentication over a single-use challenge. The challenge commits to the policy content. This signature is the root of all later authority.
4. Shop against the index. The agent reads catalogues over Model Context Protocol. It can use merchants on any platform. That covers merchants no aggregator on-boarded.
5. Obtain merchant attestation. The merchant signs that this item at this price with this availability was genuinely offered. A claim about price hallucination then becomes checkable, not an argument.
6. Run deterministic authorization. Arithmetic and signature verification check the cart against the signed policy. The check passes or fails. No judgment call occurs.
7. Complete payment by human tap. Today the human taps a Unified Payments Interface Personal Identification Number. The Additional Factor of Authentication requires it. We state this limit openly. We do not imply autonomy we lack.
8. Seal the proof and hand it to both parties. Buyer and merchant each hold a complete independently verifiable record. If a dispute follows, evidence settles it, not the party with more lawyers.

---

## Part four: Why it matters

### What each party actually gets

- Buyer. Agent spending that is bounded, revocable, and auditable. Arithmetic enforces the ceiling, not good judgment by a model. A receipt proves what you agreed to.
- Merchant. Reach by agents without loss of the customer relationship. No per-aggregator integration work. No platform between merchant and settlement. The merchant stays the merchant of record.
- Agent builder. One proof format and one verification routine. No separate commercial agreement, credential, and checkout integration for each demand platform.
- Banks and regulators. A machine-checkable answer to two questions. Was a human present. What exactly did the human approve. Risk models, dispute processes, and liability frameworks currently lack this input for agent-initiated payments.

### The structural argument

Every credible agentic-commerce protocol today comes from a party that also owns demand. That is a conflict, not a conspiracy. Verifier and gatekeeper are the same entity. Disputes resolve in favor of the gatekeeper by default.

The web solved this once by separation of index from transaction, and by records anyone can check. OpenStore applies that separation to agentic commerce. It is built for the rail three hundred million Indians already use.

---

## Part five: Honest limits

A briefing that lists only strengths is a sales document. A new stakeholder must know these constraints before forming a view.

- Not yet: fully unattended purchase. The Additional Factor of Authentication requires a human tap at completion today. Conformance reporting states this deviation explicitly. It does not hide behind the word autonomous.
- Not us: funds, escrow, or settlement. We deliberately stay off the money path. That limits the business model. It is not only a legal convenience. It rules out revenue designs that depend on holding float.
- Hard: merchant-side adoption. A proof is only as good as the attestation inside it by the merchant. Merchants must sign things. Near-zero-effort signing is the central adoption problem. It is unglamorous work.
- Open: who else verifies. Proofs gain value when banks, insurers, and platforms check them independently. Until a second verifier exists, portability is a property of the design, not a fact of the market.
