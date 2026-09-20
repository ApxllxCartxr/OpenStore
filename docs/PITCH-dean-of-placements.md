# Pitch script — OpenStore, to the Dean of Placements

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
