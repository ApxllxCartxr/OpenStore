# DRAFT — Discovery/registry mechanics (superseded)

Reviewed 2026-09-19. Written up as `docs/adr/0022-index-vs-mall.md` and `PLAN-distribution.md` D4. This file stays as the reasoning trail behind those two; edit them, not this, going forward.

## Where this came from

Grilled the project's purpose/end goal (2026-09-18/19). Resolved positions:

- **Bet**: agentic commerce discovery is where the puck goes, but the bet is unvalidated — this was built before that was tested. Treat the registry sketch below as the actual test.
- **Self-host demo = proof, not destination.** The 9-door trait already makes the sidecar Merchant-provider-agnostic; SpoiledDuckie is trait-conformance fixture #1, WooCommerce (`PLAN-distribution.md` D3) is fixture #2. Stop over-investing in the demo store past what proves the contract.
- **v1 done = one working discovery path**, not the S1–S8 correctness gates. Those gates are necessary, not sufficient.
- **Not competing with Shopify/Stripe** — serving merchants who don't use them. Free, open-source, no lock-in migration required.
- **India-first is deliberate** (founder is India-based), global is a later goal. Architecture is mostly already portable: tax computation lives Merchant-side behind door 9 (ADR-0010), sidecar only verifies arithmetic. The one India-coupled piece is the Authority closed set (`upi-pin`/`upi-verify`, ADR-0017; `upi-block` was retired by ADR-0018 and was never an Authority member) — internationalizing means adding Authority members, not rearchitecting.
- **Liability**: self-hosted OSS posture (AS-IS, no warranty, Merchant owns keys per ADR-0014's custody model) vs. any future *hosted* offering being a completely different liability class (closer to ADR-0021's ECO/TCS question). Keep the two paths separate for as long as possible.
- **ACP (OpenAI+Stripe) is bilateral/gated**, not a general threat: it serves merchants already on Shopify/Stripe. Doesn't compete for this project's segment — but sharpens the real risk, which is every agent platform building its own narrow, vendor-gated discovery pipe, serving nobody in the open long tail. Being protocol-agnostic (S6) *and* not requiring a merchant to join anyone's commercial pipeline is the actual differentiation.
- **Startup viability**: real technical moat (proof-carrying-commerce + India regulatory depth). Risk is structural — the query surface ("find me phone cases") is controlled by Anthropic/OpenAI/Google, none of which this project controls. Converts to a startup only via (a) getting pulled into a platform partnership as the rigorous open reference implementation, or (b) a monetizable layer that doesn't depend on being the primary discovery channel (compliance-as-a-service, hosted registry w/ paid tier, enterprise support). Free+OSS is distribution, not a business model on its own.

New ADRs written this session: `0020-sequential-tax-invoice-number.md`, `0021-eco-tcs-boundary.md`. New SPEC/plan touches: invoice_number + tracking_number/carrier fields, shipped notification, §13 gained preorder/backorder reasoning + named-but-deferred staff roles.

## The actual gap

`PLAN-distribution.md` already covers D1 (feed/JSON-LD ingested by third parties), D2 (public MCP endpoint listed in *someone else's* registry), D3 (WooCommerce plugin). Missing: an **OpenStore-native index of Listings**, not owned by any single AI vendor — the thing that answers "find me a good jewellery store."

Constraint already stated twice in the docs and non-negotiable: ADR-0005 ("no crawler, ranking, or hosted search in v1"), `PLAN-distribution.md`'s standing rule ("never crawls, ranks, submits — reach is achieved by being ingestible, not by being an intermediary"). Design must be a **phonebook, not a mall** — filtered lookup, no relevance ranking, federatable so no single Index (including OpenStore's own) is load-bearing.

## Legends (sketch)

| Actor | Definition |
|---|---|
| Listing | Signed, self-published record a Merchant's own sidecar emits (category, region, protocols, pubkey, sidecar URL, `verified_as_of`). |
| Index | Queryable store of Listings. Filtered lookup only, never ranked — ranking left to the querying client. Anyone can run one. |
| Reference Index | The instance OpenStore runs to bootstrap the format. Not privileged — a mirror, not an authority. |
| Mirror / Federator | An independently-run Index off the same open Listing feed. Proves no chokepoint. |
| Directory Client | Anything querying an Index: LLM tool call, another MCP server, a search crawler reading sitemap/JSON-LD independent of any Index, a human. |
| Verification pass | Index periodically re-fetches a *registered* sidecar's well-known manifest to confirm the Listing still matches reality — opt-in re-check of a submitted URL, not an open crawl. |

## User stories (sketch, condensed — expand before writing into a plan)

- Merchant: publish a signed Listing from `/agentic`; periodic re-affirmation (~30d) instead of living forever; query volume/referrals in the attribution panel; instant withdrawal that propagates on next re-verification cycle across conformant Indexes.
- Directory Client: filter-query an Index by category+region, get real verified URLs back; enough metadata per Listing to rank client-side; a search crawler finds the same Merchant via sitemap/JSON-LD with zero Index dependency.
- Index/Mirror operator: published + versioned Listing format and query API so a second Index needs zero coordination with OpenStore; domain-ownership proof at registration (DNS TXT or well-known challenge, same shape as ADR-0012's agent profile fetch hardening); stale Listings decay via re-verification sweep, not manual curation (manual curation is where "index" slides into "mall").
- Consumer: natural-language query through an assistant resolves through a real Index query into a real, checkout-capable sidecar, not a hallucinated brand guess.

## Resolved (2026-09-19)

- Listing format: extends `lists/seed.json` (`verified_as_of` + signature added), no new shape.
- Query API: MCP tool, same shape a Directory Client already uses against a sidecar.
- Abuse model: domain-ownership proof (ADR-0012 pattern) + minimum listing age + per-Index rate limits. No heavier model for v1.
- D1–D3 unchanged; D0 inserted before D1, since a Listing depends only on ADR-0012's existing self-registration/well-known infra, not on feed.json or public MCP being done.
