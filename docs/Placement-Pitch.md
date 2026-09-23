# Placement Pitch

Consolidated from two earlier scripts that covered the same ask and diverged:
a two-speaker 6-8 minute version and a single-speaker 5-minute version with a
question bank. Both are kept below as Part A and Part B; the delivery notes are
merged at the end. Historical record - edit this file going forward, not the two
originals.

---

## Part A - Two-speaker script, 6-8 minutes plus questions

Format: two speakers, 6-8 minutes spoken, plus roughly 5 minutes of questions.
Speakers A and B split the sections as marked. No slides needed; if one is used,
it carries only the protocol list. Pace is about 150 words per minute.

**Format:** two speakers, 6–8 minutes spoken, plus roughly 5 minutes of questions.
**Speakers:** A and B — swap in your own names. Both of you speak roughly equally.
**Delivery note:** no slides needed, but if you have one, put only the protocol list on it.
Pace is about 150 words per minute; do not rush the middle section, it is the one that
establishes that this is a real field and not a student idea.

---

## [0:00 — 0:50] Opening — A

Good morning, sir. We have built something over the last few months, and we would like
about six minutes to explain what it is and why we think it matters for placements.

Here is the one-sentence version. Artificial intelligence agents have started buying
things on behalf of people — booking, reordering, comparing and paying. Every large
technology company shipped a protocol for this in the last eighteen months. But none of
them answers the question a bank or a merchant will ask first: *can you prove a human
actually approved this specific purchase?* We built the layer that produces that proof.

We are calling it OpenStore.

## [0:50 — 2:10] Why this is needed — A

Sir, for thirty years online payments have rested on one assumption: a human is present at
the moment of purchase. You type the card number. You enter the One-Time Password. You
enter your Unified Payments Interface Personal Identification Number. Every chargeback
rule, every dispute process, every liability framework in Indian payments is built on that
one assumption.

An agent breaks it. The agent is not the human. It might act two weeks after you last
spoke to it, on an instruction as loose as "restock my kitchen under four thousand rupees."

So now ask the practical questions. The merchant ships goods — who authorised that? The
customer disputes the charge — what evidence exists? Today the only answer is "trust the
platform," because the record is a chat transcript inside a company's private product. The
merchant cannot verify it. The bank cannot verify it. Six months later in a dispute,
nobody can verify it.

And in India there is a second, harder constraint. The Reserve Bank of India requires an
Additional Factor of Authentication on digital payments. There is no approved path today
where an agent silently completes a purchase with no human factor at all. So the American
designs, which assume you can delegate a card credential, do not map onto our rails
cleanly. That gap is specifically an Indian engineering problem, and it is not solved.

## [2:10 — 3:40] Who is building towards this — B

Sir, we want to be clear that we did not invent this problem. We are working inside a
field that the largest companies in the world entered in the last year and a half. Let me
name them, because this is the part that matters for placements.

**Anthropic** published the **Model Context Protocol** in November 2024. That is the
standard that lets a language model call external systems — tools, resources, prompts,
over JavaScript Object Notation Remote Procedure Call. It has become the default way an
agent touches anything outside itself.

**OpenAI, together with Stripe**, published the **Agentic Commerce Protocol** in 2025.
That is what runs Instant Checkout inside ChatGPT. It defines a product feed, a Checkout
Session application programming interface, and a Delegated Payment Specification where the
buyer's card is exchanged for a single-use, amount-scoped Shared Payment Token that the
merchant charges through their own payment provider.

**Shopify, with Google and other launch partners**, published the **Universal Commerce
Protocol**, which exposes catalogue, cart and checkout to agents across millions of live
storefronts.

**Google** published the **Agent Payments Protocol**, as an extension to the
**Agent-to-Agent Protocol** that they contributed to the Linux Foundation. This one is the
closest to our thinking: it defines signed mandates — an Intent Mandate for what the human
authorised in the abstract, a Cart Mandate for the exact basket the human approved, and a
Payment Mandate that tells the payment network an agent was involved.

**Coinbase** published **x402**, which revives Hypertext Transfer Protocol status code 402,
Payment Required, for machine-to-machine settlement.

**Visa** and **Mastercard** have both announced agent-payment credential programmes.
**Cloudflare** is working on cryptographic identity for automated agents at the network
edge. And underneath all of it sits work that is already universal: **Web Authentication**
from the World Wide Web Consortium and the FIDO Alliance — that is passkeys — and the
**Verifiable Credentials** data model.

So: Anthropic, OpenAI, Stripe, Shopify, Google, Coinbase, Visa, Mastercard, Cloudflare.
Nine of the most significant companies in software and payments, all building in this
direction, all within eighteen months.

## [3:40 — 4:30] Where they all stop — B

And here is the opening, sir.

The Agentic Commerce Protocol and the Universal Commerce Protocol both solve catalogue and
checkout, but the trust in both is a *platform assertion* — the merchant is asked to
believe the demand platform's word that a human approved this. That is a contractual
assurance, not a cryptographic one.

Google's Agent Payments Protocol is the opposite problem. It specifies proofs beautifully
— and then designates nobody to check them. There is no verifier, no discovery layer, no
merchant onboarding path. It is a specification, not a running system.

And none of them maps onto the Unified Payments Interface, where there is no card to
delegate and the authentication factor must be collected inside a certified application.

So the gap is specific: **somebody has to actually produce and verify the proof, and
somebody has to do it on Indian rails.** That is what we built.

## [4:30 — 5:50] What we built — A

Four pieces, sir.

First, an **Intent Compiler**. It takes a loose instruction and compiles it into a bounded
authorisation object — a spending ceiling, a merchant allow-list, a category restriction, a
validity window, a revocation handle. The language model *writes* the policy. It never
*enforces* it.

Second, the human signs that policy once with a **passkey** — a Web Authentication
signature over a single-use challenge, with the user-verified flag set, bound to origin and
to the policy content. That signature is the root of all authority that follows.

Third, and this is the commitment we are most confident about: **the authorisation
decision is deterministic.** It is a pure function of the proof and the policy —
arithmetic and signature verification. There is no language model anywhere in the path
that decides whether money moves. A model that can be talked into things cannot be the
thing guarding the money.

Fourth, a **proof bundle** goes out with the order — the signed policy, the exact cart, the
merchant's own signed attestation that this item at this price was genuinely offered, and
the payment reference, all hash-bound together. Any third party can verify it offline with
a command-line tool, using only public keys. No account with us. No call to our servers.
No trust in us at all.

And one deliberate constraint: we never touch the money. Settlement is merchant-direct over
the Unified Payments Interface. Partly that is principle — we do not want to become the
intermediary we exist to remove. Partly it is law: standing on the money path would make us
an Electronic Commerce Operator under Indian tax law, with Tax Collected at Source
obligations on every transaction.

## [5:50 — 6:40] The placements argument — B

Sir, why we are bringing this to you specifically.

This field did not exist two years ago. The protocols we just listed were published within
the last eighteen months, which means nobody in the market has five years of experience in
it — the senior engineers at these companies are eighteen months in, same as us. That is a
narrow window where a student can be genuinely near the frontier rather than three years
behind it.

And the skills are not narrow. This work is cryptographic signing, protocol design, payment
systems, regulatory constraint modelling, and agent architecture in one project. Those are
exactly the requirements showing up in fintech and infrastructure roles right now, and they
are hard to demonstrate from coursework.

What we are asking for is not funding. We would like the department's backing to put this
in front of two groups: the fintech companies in our placement network — a payment
aggregator or a bank's innovation team would understand this in five minutes — and the
faculty who can tell us where the design is wrong.

## [6:40 — 7:20] Close — A

To summarise, sir. Agents are going to buy things; that argument is over, nine major
companies have already settled it. What has not been settled is how anyone proves the
purchase was authorised — and in India, how that works when the Reserve Bank requires a
human authentication factor.

We built a working answer to that, we built it to be verified by people who have no reason
to trust us, and we are honest about what it does not yet do — completion still needs a
human tap, and we say so rather than calling it autonomous.

Thank you. We are happy to take questions, and we can demonstrate a live purchase with a
verifiable proof in about two minutes if you would like to see it.

---

## Question preparation

Rehearse these. The Dean will ask at least two of them.

**"Is this not just a wrapper on ChatGPT?"**
No, sir. The authorisation path contains no language model at all — it is signature
verification and arithmetic. The model only composes the request. That separation is the
entire technical contribution.

**"What stops Google or OpenAI from doing this tomorrow?"**
Structurally, they are the wrong party to do it. Each of them owns the demand side, so they
would be both the gatekeeper and the verifier, and disputes resolve in their favour by
default. The web solved this once before by separating the thing that indexes from the
thing that transacts. Independence is the product.

**"Who pays for this?"**
Not transaction fees — we deliberately stay off the money path for the tax reason we
mentioned. The plausible models are merchant-side tooling and verification-as-a-service for
banks and insurers. We will be straight with you: this is the least settled part of our
thinking.

**"Is it actually working, or is it a document?"**
It runs. We can demonstrate an end-to-end purchase, and separately show the verifier
rejecting a bundle where a single price has been tampered with.

**"What is the biggest weakness?"**
Merchant adoption. A proof is only as strong as the merchant's attestation inside it, which
means merchants have to sign things. Making that effortless is unglamorous work and it is
the real risk to the project — not the cryptography.

---

## Part B - Single-speaker script, 5 minutes, with question bank

Ask: showcase slot plus industry connects. Every number below is verifiable in
the repo.

**Ask:** showcase slot + industry connects. Every number below is verifiable in the repo.

---

## The script (~750 words / 5 min)

### 0:00 — The problem (30s)

Sir, in the last year every major platform shipped an AI agent that can shop for
you. OpenAI shipped the Agentic Commerce Protocol, Google shipped AP2, and
there's a Universal Commerce Protocol behind both. They all solve the same
problem the same way: the agent gets a payment credential and completes the
purchase on your behalf.

That is a model where a probabilistic language model holds spending authority.
I think that's the wrong default, and I built the alternative.

### 0:30 — What it is (45s)

OpenStore is a *sidecar*. It's a service a merchant runs next to their existing
shop — they change nothing about their site — and it makes that shop
transactable by any AI agent.

The agent can search the catalogue, build a basket, and start a checkout. It
**cannot complete one**. Every purchase ends with a human approving an exact
amount on the merchant's own domain. And the receipt records *which kind* of
approval it was, instead of just saying "verified".

So the pitch in one line: agentic commerce where the agent never holds the money.

### 1:15 — How it actually works (2 min)

Four pieces.

**One — the Gate.** A deterministic authorizer. Twelve checks, in a fixed order,
and nothing happens between them. Cart hash matches, quote is fresh, amount is
within the merchant's policy cap, payment method is enabled, authority has
landed. No model output ever reaches it — whatever the agent proposes gets
re-validated server-side every single time.

**Two — authority is a closed set.** The system enumerates exactly what kinds of
human approval exist — a tap on the merchant's domain, a UPI PIN, a passkey —
and each kind declares what it binds and when it lands. The strongest one is
WebAuthn: the challenge isn't a random nonce, it's a SHA-256 over the cart hash,
the exact amount, the currency, the merchant domain and the expiry. The user's
device signs *that*. So a signature over a swapped basket is a signature over a
different challenge, and it fails — the binding is a property of the
cryptography, not of a database lookup somebody forgot to re-check.

**Three — receipts you can verify without us.** Every receipt is sealed,
hash-chained and signed, and it carries its own key snapshot. There's a CLI —
`openstore-verify` — that a consumer or an auditor runs offline. Exit code zero
means valid, one means tampered and it names the exact broken link, two means
untrusted key. No network call, no cooperation needed from the merchant whose
behaviour is being checked. Personal data lives outside the sealed part as
commitments, so deleting a customer's data never breaks verification — that's
the DPDP-Act-shaped problem solved structurally.

**Four — protocol conformance.** It speaks MCP, ACP, UCP and AP2. ACP's
`completeCheckoutSession` is the one operation that's *deliberately refused* —
that's the call where the agent hands over a delegated payment token. It refuses
with a named code and returns the approval URL instead. So the refusal is a
documented conformance position, not an unimplemented gap.

Stack: Python 3.12 with FastAPI, SQLModel and Postgres for the sidecar;
SvelteKit and TypeScript for the shop and the agent; Caddy in front; the whole
thing comes up with `make up` as ten separate merchants — ten sidecars, ten
databases — because "one deploy, one merchant" scales by repetition, not by a
tenant column.

### 3:15 — Why this is engineering, not a demo video (45s)

Eight hundred and sixty-seven automated tests — 638 Python, 229 TypeScript.
Twenty-six architecture decision records, each one written *before* the code.

And four build-breaking guardrails: you cannot merge a float in a money path,
you cannot merge a timezone-naive datetime, you cannot merge an import that
crosses a surface boundary, and you cannot merge an error code that appears in
the documentation but not in the enum — the documentation is *generated* from
the code. Each guardrail has a test that plants a violation and asserts the
check catches it, because a guardrail nobody has watched fail is a guardrail
nobody knows works.

I also run mutation testing on the authorizer — deliberately corrupt the logic
and check that a test screams. That's how I found two real bugs the passing test
suite had missed.

### 4:00 — The ask (45s)

Two things, sir.

First, a showcase slot. This is a full system, running, that speaks four
protocols the biggest companies in the world shipped this year — it's a concrete
answer to "what can your students actually build".

Second, and more useful: introductions. Any recruiter in commerce, payments or
fintech, and any local merchant willing to run a pilot. The code is ready to be
read by an engineer, and I'd rather be judged on it than on a resume line.

Happy to open the terminal right now if you want to see it run.

---

## Q&A bank

**"Is this original, or a wrapper on an existing product?"**
The sidecar is original. It implements four published *protocols* — MCP, ACP,
UCP, AP2 — the way a browser implements HTTP. Fifteen thousand lines of my own
source, ten thousand lines of my own tests.

**"What's the business model?"**
Merchant-side software. A merchant installs it to become agent-reachable without
handing purchase authority to somebody else's model. I have not built billing —
it's a working demo, and I'd rather say that plainly.

**"Has anyone used it in production?"**
No. It's explicitly demo-status: the sidecar refuses to boot with live payment
keys while demo mode is on, and every receipt is stamped as a demo. That refusal
is a feature — I'd rather ship a safe demo than an unsafe pilot. A merchant
pilot is exactly what I'm asking for.

**"Why not just let the agent pay? Everyone else does."**
Because a language model is probabilistic and a payment is not. The cost of my
choice is real — I give up native in-agent completion, and it's written down as
ADR-0016. The benefit is that a compromised or confused agent cannot spend
money, structurally, rather than because the prompt asked it nicely.

**"How do you know your prices are right?"**
Three independent implementations of GST and rounding — the merchant's in
TypeScript, a conformance fake's in Python, and the Gate's own check — and none
of them share a module. They agree on a pinned worked example to the paisa. If
two of them rounded differently, the system would fire `quote-inconsistent` on a
correct quote, and building them separately is the only way to know they don't.

**"What's the hardest thing you solved?"**
Binding approval to a specific basket. The easy version is: generate a random
challenge, store it in a table, look it up later. That binds nothing — swap the
cart between challenge and signature and the lookup still passes. Making the
challenge *be* the hash of the cart, amount, currency, domain and expiry means
the authenticator's signature is only valid for that exact purchase.

**"What happens if your service goes down mid-checkout?"**
The sidecar owns both expiry clocks and the merchant never self-expires. One
column carries three deadlines — a 24-hour quote validity, a 15-minute payment
window, a 7-day COD delivery window. A stopped sidecar holds stock, so the
merchant console lists overdue holds where a human can see them. The late-COD
case alerts instead of auto-cancelling, because a parcel that is late is not a
parcel that is lost, and only the merchant knows which.

**"Security?"**
Agents self-register with trust-on-first-use key pinning and are rate-limited by
tier. The admin surface is refused at the reverse proxy — private network only.
Outbound fetches are SSRF-hardened. The one public route inside an
authenticated prefix is the receipt, and its unguessable ID is the only
credential, deliberately: a consumer is not the merchant, so a receipt behind
the merchant's session is a receipt no consumer could ever open.

**"How long did it take, and did you use AI to write it?"**
About a month of sustained work. I use AI tooling heavily — and the guardrails
exist partly for that reason. Generated code that violates the money rule, the
time rule, the import rule or the error registry does not compile in CI. I can
walk through any decision in the twenty-six ADRs and tell you what I rejected
and why.

**"What's missing?"**
Reach. A conformant sidecar no agent can discover isn't a product yet — that's
specced as a phase-two track and gated on install. Also no hosted mall, no
ranking, no multi-merchant tenancy, no subscriptions. Those are scope decisions
with ADRs behind them, not a backlog.

**"What would you do with an industry connect?"**
Two calls. One with a payments or commerce engineer to pressure-test the
authority model. One with a merchant willing to run the sidecar next to a real
catalogue for two weeks. The second one turns a demo into evidence.

**"Can other students work on it?"**
Yes — that's the strongest version of a showcase. The spec, the glossary and the
build plan are all in the repo, and the guardrails mean a new contributor gets
told by CI when they break an invariant instead of finding out in review.

---

## Delivery notes

- Rehearse the 1:15–3:15 block hardest. That's where the trainer's "know your
  tech" note lands.
- Don't say "revolutionary", "next-gen", or "disrupting". Say the numbers.
- Have `make up` already running on the laptop before you walk in. The offer to
  demo only works if it's instant.
- If he asks a question you don't know: "I don't know — it's in ADR-00XX
  territory, let me check and send it today." That answer scores higher than a
  guess, especially with a technical listener in the room.
