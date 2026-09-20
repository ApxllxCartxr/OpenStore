# OpenStore

A sidecar that makes one merchant's site transactable by any buyer agent —
without giving the agent a way to spend money.

An agent can search the catalogue, build a basket, and start a checkout. It
cannot complete one. Every purchase ends with a human approving an exact amount
on the merchant's own domain, and the receipt says which kind of approval that
was rather than reporting an unqualified "verified".

```
make up
```

Then: the shop at `http://spoiledduckie.localhost`, the merchant's console at
`/agentic`, and a demo buyer agent at `http://chat.localhost`.

> `*.localhost` resolves to loopback in Chrome and Firefox. **A container does
> not resolve it.** That asymmetry is the single most likely way to lose an
> afternoon here, so it is written down in `SPECS/PLAN.md §10.1` and repeated by
> `make up`.

## What is actually in the box

Five services on one origin, behind one reverse proxy:

| Path | Served by | Auth |
|---|---|---|
| `/`, `/shop`, `/p/<slug>`, `/lookup` | merchant site | public |
| `/.well-known/*` | sidecar | **public, by design** |
| `/agent/*` | sidecar | agent token, rate-limited by tier |
| `/agentic/approve` | sidecar | **the one-time tap token, not the merchant session** |
| `/agentic/*` | sidecar | merchant session |
| `/receipt/<id>` | sidecar | **public** — the unguessable id is the only credential |
| `/trait/*`, `/admin*` | — | **refused at the edge**; private network only |

The two public routes inside an otherwise-authenticated prefix are deliberate. A
consumer approving a spend is not the merchant, and a receipt that opens by
unguessable id *with no login* cannot live behind the merchant's session — that
would mean no consumer could ever open their own.

## The three claims this repository is built to support

**An agent never holds spending authority.** Not "we validate carefully" — the
agent is given a scope that cannot reach money. `place-order` returns an approve
URL and never an order. A delegated payment credential is refused at the envelope
boundary with a named code, and the card says so before an integrator writes a
line.

**The merchant computes prices; the sidecar checks the arithmetic.** There are
three independent implementations of the GST and rounding rules — the merchant's
in TypeScript, a conformance fake's in Python, and the Gate's own check — and
none of them share a module. They agree on the pinned worked example to the
paise. Two implementations that round differently would fire `quote-inconsistent`
on a *correct* quote, and building them separately is the only way to know they
do not.

**Every refusal is a closed code, and the registry is generated from the enum.**
A code that appears in prose and not in `core/codes.py` fails the build. So does
a float in a money path, a naive datetime, and an import across surface
boundaries.

## Layout

```
src/openstore/sidecar/    the product — gate, ledger, evidence, protocols, console
demo/merchant-site/       SpoiledDuckie: a real shop, SvelteKit + Postgres
demo/buyer-chat/          an air-gapped stranger agent, SvelteKit + SQLite
design/                   the shared token layer — assets only, never importable
docs/adr/                 the decisions, numbered and dated
SPEC.md  CONTEXT.md       the spec and the glossary; both are law
SPECS/PLAN.md             the build plan this repository was produced from
```

The three surfaces never import each other. They talk over HTTP and signed
webhooks, and `scripts/lint_firewall.py` fails the build on any crossing —
runtime, types, or tests. The demo merchant and the demo agent exist to prove the
integration is real; sharing one module between them would quietly turn that
proof into a demonstration that two halves of one program can call each other.

## Working on it

```
make check        # guardrails, lint, types, and every suite
make test         # the three test suites
make guardrails   # firewall · registry · money lint · time lint
make demo         # the conformance suite against the running store
make down         # stop, and remove the volumes
```

`make check` is what CI runs. The guardrails are not advisory: each one has a
test that plants a violation and asserts the check catches it, because a guardrail
nobody has watched fail is a guardrail nobody knows works.

## Verifying a receipt

Receipts are sealed, hash-chained and signed, and they carry their own key
snapshot so verification needs no network and no cooperation from the merchant
whose behaviour is being checked.

```
uv run python -m openstore.sidecar.verify.cli receipt.json
```

Exit `0` valid, `1` tampered — naming the exact link — and `2` untrusted key. A
receipt signed *before* a key was revoked stays valid forever; one signed after
fails. The PII sections come back `unopened` rather than failing, because the
commitments cannot be opened without the merchant's salt and erasure must never
break verification.

## What this deliberately does not do

No hosted mall, no ranking, no multi-merchant tenancy, no rule engine, no
subscriptions, no agent-held payment credentials. The last one costs native
in-agent completion on the gated surfaces and is paid on purpose (ADR-0016):
their absence is the product, not a gap.

Reach — being findable by agents that exist today — is a real gap and is
specced as a phase-two track in `PLAN-distribution.md`, gated on install. A
conformant sidecar no agent can see is not a product; neither is reach earned
before anyone can install the thing.

## Status

Demo. Every receipt is marked as one, and the sidecar refuses to boot with live
payment keys while demo mode is on. The fonts in `design/` include one
commercial face — see `design/README.md` before recording, hosting, or sharing
anything built from this.
