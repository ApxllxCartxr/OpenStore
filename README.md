# OpenStore

A sidecar that makes one merchant's site transactable by any buyer agent. The agent never gets a way to spend money.

An agent can search the catalogue, build a basket, and start a checkout. It cannot complete one. Every purchase ends with a human approving an exact amount on the merchant's own domain. The receipt names which kind of approval that was. It never reports an unqualified "verified".

```
make up
```

Then you get three things: the shop at `http://spoiledduckie.localhost`, the merchant console at `/agentic`, and a demo buyer agent at `http://chat.localhost`.

> `*.localhost` resolves to loopback in Chrome and Firefox. A container does not resolve it. That asymmetry is the fastest way to lose an afternoon. It is written down in `docs/Build-Plan.md §10.1`, and `make up` repeats it.

## What is in the box

Five services share one origin behind one reverse proxy:

| Path | Served by | Access |
|---|---|---|
| `/`, `/shop`, `/p/<slug>`, `/lookup` | merchant site | public |
| `/.well-known/*` | sidecar | public, by design |
| `/agent/*` | sidecar | agent token, rate-limited by tier |
| `/agentic/approve` | sidecar | one-time tap token, not the merchant session |
| `/agentic/*` | sidecar | merchant session |
| `/receipt/<id>` | sidecar | public; the unguessable id is the only credential |
| `/trait/*`, `/admin*` | — | refused at the edge; private network only |

Two public routes sit inside otherwise-authenticated prefixes. Both are deliberate. A consumer approving a spend is not the merchant. A receipt that opens by unguessable id with no login cannot live behind the merchant's session. No consumer could ever open it there.

## Ten shops, one chat

`make up` seeds ten merchants. Each gets its own sidecar, database, and GST home state. One deploy serves one merchant domain (ADR-0007). Scale comes from repetition, not from a tenant column. `demo/buyer-chat` talks to all ten at once, plus any other MCP server a consumer pastes in. An unaddressed search fans out to every known shop. A write resolves to whichever shop it can only mean.

| Shop | Category | Storefront | Console (private network) |
|---|---|---|---|
| SpoiledDuckie | accessories | `spoiledduckie.localhost` | `127.0.0.1:3000/admin` |
| Dog-Eared | books | `dogeared.localhost` | `127.0.0.1:3010/admin` |
| CircuitYard | electronics | `circuityard.localhost` | `127.0.0.1:3020/admin` |
| IronList | hardware | `ironlist.localhost` | `127.0.0.1:3030/admin` |
| PantryLine | grocery | `pantryline.localhost` | `127.0.0.1:3040/admin` |
| Kettle & Grain | kitchenware | `kettleandgrain.localhost` | `127.0.0.1:3050/admin` |
| DeskField | stationery | `deskfield.localhost` | `127.0.0.1:3060/admin` |
| Root & Leaf | plants | `rootandleaf.localhost` | `127.0.0.1:3070/admin` |
| Playspool | toys | `playspool.localhost` | `127.0.0.1:3080/admin` |
| Furrow | pet supplies | `furrow.localhost` | `127.0.0.1:3090/admin` |

Four products overlap across two shops each (AA batteries, filter coffee, a sewn notebook, a tennis-ball 3-pack). Price and stock differ per shop. That is the ordinary case a multi-shop agent must reason about. Log into any console with `operator@<domain>` and `demo-operator-pw`. The admin path is refused at the edge on purpose, so reach it over the published loopback port, never through `*.localhost`.

The chat is not shop-shaped. Paste an `agent-commerce.json` URL and it connects as one of this repository's own shops (self-registration, TOFU key pinning, the closed tool set). Paste a bare MCP server URL instead and it connects the way Claude Desktop's own MCP connector does: `initialize` plus `tools/list` over the same SSRF-hardened fetch. No card, no pinned key, whatever tools that server names. Either way the model sees real tool schemas and real annotations (`readOnlyHint`, `moneyPathHint`). Standing "always allow" is available for everything short of the two scopes that move toward a spend.

## The three claims

An agent never holds spending authority. The agent gets a scope that cannot reach money. `place-order` returns an approve URL and never an order. A delegated payment credential is refused at the envelope boundary with a named code. The card says so before an integrator writes a line.

The merchant computes prices, and the sidecar checks the arithmetic. Three independent implementations cover the GST and rounding rules: the merchant's in TypeScript, a conformance fake's in Python, and the Gate's own check. They share no module. They agree on the pinned worked example to the paise. Two implementations that round differently would fire `quote-inconsistent` on a correct quote. Building them separately is the only way to know they do not.

Every refusal is a closed code, and the registry is generated from the enum. A code that appears in prose and not in `core/codes.py` fails the build. A float in a money path fails the build. A naive datetime fails the build. An import across surface boundaries fails the build.

## Layout

```
src/openstore/sidecar/    the product — gate, ledger, evidence, protocols, console
demo/merchant-site/       SpoiledDuckie: a real shop, SvelteKit + Postgres
demo/buyer-chat/          an air-gapped stranger agent, SvelteKit + SQLite
design/                   the shared token layer — assets only, never importable
docs/Architecture.md      the system overview for merchant, developer, and buyer
docs/Specification.md     the spec; law, with docs/Glossary.md and docs/adr/
docs/Closed-Sets.md       the generated closed-set registry; law (edit the enum)
```

The three surfaces never import each other. They talk over HTTP and signed webhooks. `scripts/lint_firewall.py` fails the build on any crossing: runtime, types, or tests. The demo merchant and the demo agent prove the integration is real. One shared module between them would turn that proof into a demonstration that two halves of one program can call each other.

## Working on it

```
make check        # guardrails, lint, types, and every suite
make test         # the three test suites
make guardrails   # firewall · registry · money lint · time lint
make demo         # the conformance suite against the running store
make down         # stop, and remove the volumes
```

`make check` is what CI runs. The guardrails are not advisory. Each one has a test that plants a violation and asserts the check catches it. A guardrail nobody has watched fail is a guardrail nobody knows works.

## Verify a receipt

Receipts are sealed, hash-chained, and signed. They carry their own key snapshot. Verification needs no network and no help from the merchant whose behaviour is under check.

```
uv run python -m openstore.sidecar.verify.cli receipt.json
```

Exit `0` means valid. Exit `1` means tampered, and it names the exact link. Exit `2` means untrusted key. A receipt signed before a key revocation stays valid forever. A receipt signed after one fails. The PII sections come back `unopened` rather than failing. The commitments cannot open without the merchant's salt, and erasure must never break verification.

## What this does not do

No hosted mall. No ranking. No multi-merchant tenancy. No rule engine. No subscriptions. No agent-held payment credentials. The last one costs native in-agent completion on the gated surfaces. It is paid on purpose (ADR-0016). Its absence is the product, not a gap.

Reach is a real gap. Being findable by agents that exist today is specced as a phase-two track in `docs/Plan-Distribution.md`, gated on install. A conformant sidecar no agent can see is not a product. Reach earned before anyone can install the thing is reach wasted.

## Status

Demo. Every receipt is marked as one. The sidecar refuses to boot with live payment keys while demo mode is on. The fonts in `design/` include one commercial face. Read `design/README.md` before you record, host, or share anything built from this.
