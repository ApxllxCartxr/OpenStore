# OpenStore — 5-minute pitch to the Dean of Placements

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
