# Proof-Carrying Commerce

**OpenStore — stakeholder briefing**
Prepared 20 September 2026 · Audience: new stakeholder, no prior context · Scope: concept, problem, landscape

Software agents have started buying things on behalf of people. The protocols that let them
do so were written by the same companies that own the demand. OpenStore is the verification
and discovery layer that makes an agent's purchase provable to anyone — without putting a new
platform between the buyer and the merchant.

---

## In brief

When an artificial intelligence agent buys something for you, three questions have no
machine-checkable answer today:

- *Did a human actually authorise this exact purchase, at this exact price, from this exact merchant?*
- *Was the item bought the item the merchant actually offered?*
- *When it goes wrong, what evidence exists and who is liable?*

The current answer to all three is "trust the platform." OpenStore replaces that with a
signed, portable **proof** that travels with the order and that any third party — merchant,
bank, payment service provider, insurer, court, or the buyer themselves — can verify offline
without trusting OpenStore at all.

---

## Part one — The problem

### Agentic commerce is arriving with a weaker trust model than the web it replaces

For thirty years, online payment authority has rested on a human being present at the moment
of purchase: a card number typed, a One-Time Password entered, a Unified Payments Interface
Personal Identification Number confirmed. Every dispute-resolution process, every chargeback
rule, every liability-shift framework in card and account payments is built on that
assumption.

An autonomous agent breaks the assumption. The agent is not the human. It may act minutes or
weeks after the human last spoke to it. It may act on an instruction as loose as "restock my
kitchen under four thousand rupees." And the record it leaves behind — a conversation
transcript inside a proprietary product — is not evidence anyone outside that product can
check.

### Problem one: authority has become a platform assertion, not a fact

In every shipping agentic-commerce system today, the merchant is asked to trust that the
demand platform is telling the truth about the human behind the request. The platform signs
the request with its own credential. That is a statement of the form "we, a large company,
assert a user approved this." It is a contractual assurance, not a cryptographic one. The
merchant cannot independently check it, cannot re-check it six months later during a dispute,
and cannot check it at all if the platform declines to cooperate.

### Problem two: the merchant integrates once per aggregator, forever

A merchant who wants to be reachable by agents currently needs a separate catalogue feed, a
separate checkout endpoint, a separate agent-profile registration, and a separate commercial
agreement for each demand platform. Discovery is granted, not earned: if the platform has not
on-boarded you, you do not exist to its agents. For the small and medium merchant — the
business selling through a Shopify store, a WooCommerce install, a WhatsApp Business
catalogue, or a spreadsheet — the cost of that integration exceeds the revenue it would
unlock, so they are simply absent from the agentic channel.

### Problem three: India's payment rules do not bend for agents

The Reserve Bank of India mandates an Additional Factor of Authentication for digital payment
transactions — in practice, a Unified Payments Interface Personal Identification Number
entered in a certified application, or an equivalent device-bound factor. There is no
regulator-blessed path today by which an agent silently completes an arbitrary purchase with
no human factor. Systems designed around card-network delegation assumptions do not map
cleanly onto this. Any honest Indian agentic-commerce design must treat the human tap as a
constraint to be engineered around, not a limitation to be quietly ignored.

---

## Part two — The landscape, protocol by protocol

These are not competitors in a single category. They occupy different layers — transport,
catalogue, payment authorisation, settlement rail — and a working system needs several of
them at once. What follows is each one, what it genuinely does, and the specific gap it
leaves.

### Model Context Protocol

*Steward: Anthropic · Open specification, November 2024*

An open standard for connecting a language model to external systems. It defines a JavaScript
Object Notation Remote Procedure Call (version 2.0) message format carried over standard
input/output streams or streamable Hypertext Transfer Protocol. A *server* exposes three kinds
of capability to a client: **tools** (callable functions with typed input schemas),
**resources** (readable data addressed by a Uniform Resource Identifier), and **prompts**
(reusable templates). It has become the de-facto way an agent reaches any external system at
all, commerce included.

> **Where it stops.** Model Context Protocol is a connection protocol with no commerce
> semantics whatsoever. It has no concept of money, price, inventory, identity, authorisation,
> consent, or proof. It tells you how to call a function; it says nothing about whether the
> caller was permitted to spend eight thousand rupees. Every commerce guarantee has to be built
> on top of it.

### Agent-to-Agent Protocol (published as "A2A")

*Steward: Google, contributed to the Linux Foundation*

A peer protocol for independent agents built by different vendors to discover and delegate
work to one another. Each agent publishes an **Agent Card** — a machine-readable document at a
well-known Uniform Resource Locator declaring its identity, skills, endpoints, and
authentication requirements. Work is then exchanged as a **task** with a defined lifecycle
(submitted, working, input-required, completed, failed) over JavaScript Object Notation Remote
Procedure Call.

> **Where it stops.** Like Model Context Protocol, it is transport and discovery for
> *capability*, not for *authority over money*. It is a necessary substrate for multi-party
> agent commerce and an insufficient one on its own; Google's own payments work sits as an
> extension on top of it, which is itself the admission.

### Agentic Commerce Protocol

*Steward: OpenAI with Stripe · Open specification, 2025*

The protocol behind Instant Checkout inside ChatGPT. It has three distinct parts.

1. **A product feed specification** — a structured catalogue the merchant publishes so items
   can be surfaced inside the assistant.
2. **A Checkout Session application programming interface** over Representational State
   Transfer — the agent creates a session, updates it with buyer address and selected
   fulfilment, and completes it, with the merchant returning authoritative pricing, tax, and
   availability at every step so the agent never invents a price.
3. **The Delegated Payment Specification** — the buyer's stored payment method is exchanged
   for a **Shared Payment Token**: a single-use, amount-scoped, merchant-scoped credential
   that the merchant charges through their own payment service provider. The merchant stays
   the merchant of record, keeps the customer relationship, and bears the settlement.

> **Where it stops.** The buyer-agent side is, in practice, one company's. Trust is bilateral
> and contractual: the merchant trusts OpenAI's assertion that a human approved this. Nothing
> in the protocol produces an artefact the merchant can hand to a third party months later to
> prove what the human agreed to. Payment is card-rail-shaped through a Shared Payment Token,
> which does not map onto the Unified Payments Interface, where there is no card to delegate
> and the authentication factor must be collected inside a certified application.
>
> And discovery remains the aggregator's to grant. Being in the catalogue is an onboarding
> decision made by the platform, not a property a merchant can establish independently.

### Universal Commerce Protocol

*Steward: Shopify, with Google and other launch partners · 2025*

An agent-facing surface over the commerce primitives Shopify already operates: catalogue and
product data, a cart that an agent can construct and mutate, and a checkout hand-off. Agents
authenticate as registered clients under OAuth 2.0, and merchants opt in to which agents may
transact against their store. Its great strength is that it is backed by real inventory —
millions of live storefronts with accurate stock, pricing, tax, and fulfilment already wired
up. That is not a specification exercise; it is a working commerce network.

> **Where it stops.** The claim that any buyer agent can participate is not yet true in
> practice. OAuth 2.0 clients must be registered in advance, so participation is a permissioned
> club with an application process — the same discovery gatekeeping in a more open-looking
> wrapper. Dynamic Client Registration (Internet Engineering Task Force Request for Comments
> 7591), which would make "any agent" literally true, is not the operative path.
>
> Completion is also not agent-native: the flow terminates in a redirect to a hosted checkout
> where the human taps, every single time. That is a defensible and possibly correct choice —
> but it means the protocol does not actually solve unattended purchase, and systems that build
> on it should say so rather than imply otherwise. Finally, its centre of gravity is the
> Shopify-hosted merchant; the WooCommerce store, the custom site, and the WhatsApp Business
> catalogue are not first-class citizens.

### Agent Payments Protocol (published as "AP2")

*Steward: Google, as an extension to the Agent-to-Agent Protocol · 2025*

The most conceptually advanced of the group, and the closest in spirit to what we are
building. Its central idea is the **mandate**: a cryptographically signed, verifiable
credential capturing authority, in three forms.

- **The Intent Mandate** — what the human authorised in the abstract, before the specific item
  is known ("buy these running shoes in size nine if they drop below six thousand rupees in
  the next thirty days"). This is what makes delegated, human-absent purchase expressible at
  all.
- **The Cart Mandate** — the exact basket, with exact line items and exact prices, signed by
  the human at the moment they are present. This is the non-repudiable record of "yes, this,
  at this price."
- **The Payment Mandate** — the artefact passed to the payment network, signalling that an
  agent was involved and whether a human was present, so issuers and networks can price risk
  accordingly.

Mandates are built on World Wide Web Consortium Verifiable Credentials, and the protocol is
deliberately rail-agnostic: it is designed to carry card payments, real-time
account-to-account payments, and stablecoin settlement equally.

> **Where it stops.** It is a specification and a reference sample, not a running network.
> There is no discovery layer, no merchant on-boarding path, no catalogue, and — critically —
> no verifier. The protocol describes proofs that could be checked but designates nobody to
> check them, and no merchant back-office in the world consumes a Cart Mandate today. It also
> leaves the merchant-side integration deliberately open, which is the hardest and least
> glamorous part of the problem.
>
> Its Indian story is likewise unwritten. Mapping an Intent Mandate onto a Unified Payments
> Interface mandate, a fund block, or an Additional Factor of Authentication event is exactly
> the work that has not been done.

### x402 (over Hypertext Transfer Protocol status 402)

*Steward: Coinbase · Open specification, 2025*

A revival of the long-reserved Hypertext Transfer Protocol status code 402 Payment Required. A
server responds to an unpaid request with 402 plus a machine-readable description of what
payment it wants; the client pays — typically in a dollar-denominated stablecoin on a low-fee
chain — and retries with a payment header. Settlement is near-instant, fees are fractions of a
cent, and no account or subscription exists. It is genuinely good for what it is built for:
machine-to-machine micropayments, paid application programming interface calls, per-inference
billing.

> **Where it stops.** It is not consumer retail commerce. There is no chargeback, no dispute
> process, no consumer-protection framework, and no Goods and Services Tax invoice. An Indian
> consumer buying groceries is not going to settle in stablecoin, and an Indian merchant cannot
> legally price in one. It solves the agent paying for a data feed, not the agent buying a
> kilogram of coffee.

### Unified Payments Interface

*Steward: National Payments Corporation of India · Live at national scale*

India's real-time account-to-account payment rail, and the rail that actually matters for this
project. Three facilities are directly relevant.

- **Unified Payments Interface Autopay** creates a recurring electronic mandate with a ceiling
  amount and a validity period, approved once by the payer and debited on schedule thereafter.
- **Unified Payments Interface Reserve Pay** — the single-block, multiple-debit facility — lets
  a payer block funds in their own account, with the money remaining theirs and earning
  interest until it is debited.
- **The Unified Payments Interface Personal Identification Number**, entered in a certified
  application, is the Additional Factor of Authentication.

Two details matter enormously and are widely misunderstood. Reserve Pay is *not* card-style
authorise-then-capture: the semantics, the reversal behaviour, and the permitted debit
patterns are different, and designing as if it were a hold on a card produces a system that
fails in production. And an Autopay mandate binds the payer to a payee with a cap — it does
not, by itself, encode *what* was being bought.

> **Where it stops.** Unified Payments Interface knows that money moved from one account to
> another. It knows nothing about carts, items, prices, agents, or consent. A mandate proves a
> payer permitted a payee to draw up to a limit; it does not prove the human agreed to the
> three specific items the agent actually bought. That gap between "payment was authorised" and
> "this purchase was authorised" is precisely the gap OpenStore fills.

### Web Authentication (World Wide Web Consortium "WebAuthn", part of FIDO2)

*Steward: World Wide Web Consortium and the FIDO Alliance · Universally deployed*

The standard behind passkeys. A private key is generated and held in device hardware and never
leaves it; the server issues a random challenge, the authenticator signs it after verifying
the user by biometric or device Personal Identification Number, and returns a signature bound
to the challenge and to the requesting origin. The signature carries a **user-verified** flag
distinguishing "a human actively confirmed this" from "a key was merely present." It is
phishing-resistant by construction, because a signature made for one origin is worthless at
another.

> **Where it stops.** Web Authentication proves a human approved *a challenge*. It says nothing
> about what that challenge meant. Making the signature meaningful requires binding it to a
> specific, canonically-serialised statement of purchase authority — which is an
> application-layer design decision the standard leaves entirely open, and which almost
> everybody gets wrong by signing an opaque nonce with no committed content.

### Verifiable Credentials and JSON Web Signature

*Steward: World Wide Web Consortium and the Internet Engineering Task Force*

The envelope formats. The Verifiable Credentials Data Model defines an issuer, a subject, a
claim set, and a cryptographic proof, such that a third party can check the credential without
contacting the issuer. JSON Web Signature and its detached form provide the concrete signing
container. These are what make a proof *portable*: checkable by anyone holding the public key,
months after the fact, offline.

> **Where it stops.** They are containers, not content. They define how to sign a claim; the
> entire question of *which* claims constitute sufficient authority for a purchase, and how a
> verifier decides, is left to the application.

### The landscape at a glance

| Protocol | Layer it occupies | Third-party verifiable proof | Open discovery | Works on Unified Payments Interface |
| --- | --- | --- | --- | --- |
| Model Context Protocol | Agent-to-system transport | None | n/a | n/a |
| Agent-to-Agent Protocol | Agent-to-agent transport | None | Yes | n/a |
| Agentic Commerce Protocol | Catalogue, cart, checkout, payment delegation | Platform assertion | Aggregator-granted | Card-shaped |
| Universal Commerce Protocol | Catalogue, cart, checkout hand-off | Platform assertion | Pre-registered | Via redirect |
| Agent Payments Protocol | Payment authorisation semantics | By design | No layer | Unmapped |
| x402 | Machine settlement rail | On-chain only | Yes | No |
| Unified Payments Interface | Consumer settlement rail | Payment only | n/a | Native |
| **OpenStore** | **Proof, verification, discovery** | **Core purpose** | **Permissionless** | **Native** |

---

## Part three — What we are building

### OpenStore is a proof layer and an index — deliberately not a marketplace

We are not building another demand platform, and we are not competing with the Agentic
Commerce Protocol or the Universal Commerce Protocol head-on. We speak them. What we add is
the thing none of them provides: an **independent, checkable record of authority**, plus a way
for any merchant — not just the pre-approved ones — to be reachable by any agent.

**Component one — The Intent Compiler.**
Takes a loose human instruction ("order the usual coffee if it's under nine hundred rupees")
and compiles it into a bounded, machine-checkable authorisation object: a spending ceiling, a
merchant allow-list, a category restriction, a quantity limit, a validity window, and a
revocation handle. The language model writes the policy; it never enforces it. The compiled
policy is a deterministic structure, reviewable by the human in plain words before they sign
it.

**Component two — The Proof of Authorised Intent bundle.**
The artefact that travels with every order. It contains the compiled intent policy signed by
the human's passkey over a server-issued, single-use challenge; a cart snapshot committing to
the exact line items, quantities, prices, currency, and merchant identity; the merchant's own
signed attestation that this catalogue entry at this price was genuinely offered; and the
payment authorisation reference. Every element is bound to the others by hash, so no part can
be swapped after the fact.

**Component three — Deterministic authorisation.**
The decision to permit a purchase is a pure function of the proof bundle and the policy. No
language model sits anywhere in the authorisation path. This is the single most important
architectural commitment we make: a model that can be persuaded cannot be the thing that
decides whether money moves. The model composes, negotiates, and explains; arithmetic and
signature checks authorise.

**Component four — An independent verifier.**
A standalone command-line tool and library that takes a proof bundle and returns a verdict,
using only public keys — no network call to us, no account, no trust in OpenStore. This is
what turns our claims into something a merchant's lawyer, a bank's risk team, or a sceptical
stakeholder can check for themselves. A protocol whose proofs only its own author can verify
is not a protocol.

**Component five — Merchant adapters.**
Connectors for storefronts that already exist — Shopify, WooCommerce, and a plain
comma-separated-values catalogue upload for merchants with no platform at all — so a merchant
becomes agent-reachable without rebuilding anything. Catalogue and stock are handled
separately, because a stale price is a bug and a stale stock count is a refund.

**Component six — Index, not mall.**
We never take custody of funds. Money settles merchant-direct over the Unified Payments
Interface. The merchant remains the merchant of record. This is a deliberate legal position as
much as a technical one: standing on the money path would make us an Electronic Commerce
Operator under Indian tax law, with Tax Collected at Source and Goods and Services Tax
obligations attached to every transaction — and would make us exactly the intermediary we
exist to make unnecessary.

### How a purchase actually flows

1. **The human states an intent.** In natural language, to whichever agent they already use.
   Nothing about the agent needs to be ours.
2. **The Intent Compiler produces a bounded policy.** Ceiling, allow-list, category, quantity,
   expiry. Shown back to the human in plain words for approval.
3. **The human signs it once, with a passkey.** Web Authentication signature over a single-use
   challenge that commits to the policy content. This is the root of all authority that
   follows.
4. **The agent shops against the index.** Reading catalogues over Model Context Protocol from
   merchants on any platform, including those no aggregator has on-boarded.
5. **The merchant attests the offer.** A signed statement that this item, at this price, with
   this availability, was genuinely offered — so "the agent hallucinated the price" becomes a
   checkable claim rather than an argument.
6. **Deterministic authorisation runs.** The cart is checked against the signed policy by
   arithmetic and signature verification. It passes or it does not. No judgement call.
7. **The human completes payment.** Today, with a Unified Payments Interface Personal
   Identification Number tap, because the Additional Factor of Authentication requires it. We
   state this limitation openly rather than implying autonomy we do not have.
8. **The proof bundle is sealed and handed to both parties.** Buyer and merchant each hold a
   complete, independently verifiable record. If a dispute follows, it is settled with evidence
   rather than with whichever platform has more lawyers.

---

## Part four — Why it matters

### What each party actually gets

**The buyer.** Agent spending that is bounded, revocable, and auditable. A ceiling that is
enforced by arithmetic rather than by a model's good judgement, and a receipt that proves what
you agreed to.

**The merchant.** Reachable by agents without surrendering the customer relationship, without
per-aggregator integration work, and without a platform standing between them and settlement.
They stay the merchant of record.

**The agent builder.** One proof format and one verification routine instead of a separate
commercial agreement, credential, and checkout integration for every demand platform in the
market.

**Banks and regulators.** A machine-checkable answer to "was a human present, and what exactly
did they approve?" — which is the input every risk model, dispute process, and liability
framework currently lacks for agent-initiated payments.

### The structural argument

Every credible agentic-commerce protocol today is authored by a party that also owns the
demand. That is a conflict, not a conspiracy: it simply means the verifier and the gatekeeper
are the same entity, and disputes resolve in the gatekeeper's favour by default. The web
solved this once before by separating the thing that indexes from the thing that transacts,
and by making the record checkable by anyone. OpenStore is that separation, applied to agentic
commerce, built for the rail three hundred million Indians already use.

---

## Part five — Honest limits

A briefing that only lists strengths is a sales document. These are the constraints a new
stakeholder should know before forming a view.

- **Not yet — fully unattended purchase.** The Additional Factor of Authentication means a
  human tap is required at completion today. Our conformance reporting states this deviation
  explicitly rather than hiding it behind the word "autonomous."
- **Not us — funds, escrow, or settlement.** We deliberately stay off the money path. That is a
  constraint on the business model, not just a legal convenience, and it rules out revenue
  designs that depend on holding float.
- **Hard — merchant-side adoption.** A proof is only as good as the merchant attestation inside
  it, which means merchants must sign things. Making that near-zero-effort is the central
  adoption problem, and it is unglamorous work.
- **Open — who else verifies.** Proofs become valuable when banks, insurers, and platforms
  check them independently. Until a second verifier exists, the portability is a property of
  the design rather than a fact of the market.
