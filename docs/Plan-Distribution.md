# Plan — Distribution (phase two, starts after the install gate)

> Historical phase-two plan. Formerly root `PLAN-distribution.md`. Frozen; internal references use pre-reorganisation filenames.

Not a fourth surface. A phase-two track that makes the three existing ones *reachable*, built on doors that need nobody's permission (ADR-0016). Every phase here obeys the same rails: no engine creep, no money-core change, no cross-root imports, DONE WHEN gates that only tighten.

**Entry condition: `PLAN.md` step 7 is green.** A clean machine must go `openstore up` → verified receipt before any of this starts. Sending agents at a store a merchant cannot install spends the only first impression available.

**Standing rule for the whole track:** the sidecar publishes and validates; it never crawls, ranks, submits on a Merchant's behalf, or becomes a mall. Reach is achieved by being ingestible, not by being an intermediary.

## D1 — The catalogue becomes ingestible

- Finish `/agent/feed.json` to the shape SPEC §3 already names — one item per Catalogue Item in Google Merchant Center product-data attribute names, variants grouped by `item_group_id` — and add the second format alongside it, because feeds and markup are ingested by different readers: the Merchant site emits `Product` / `ProductGroup` / `Offer` JSON-LD on group and item pages (M1 owns the markup; it belongs with the pages that own the SEO).
- Serve the feed at a stable, public, cacheable URL with correct `Last-Modified` / `ETag`, so a registry's **scheduled fetch** can pull it. Scheduled pull is what makes this permissionless: the Merchant registers one URL once, and nothing in the sidecar ever authenticates to, submits to, or is approved by a third party.
- Exposure rule holds without exception (SPEC §5): only exposed Catalogue Items appear, and availability is the Availability Bucket, never an exact count. A feed is the easiest place in the system to leak inventory, because it is designed to be read by strangers.
- Feed price is advertised, the Quote is authoritative, and they drift by design between a fetch and a purchase — that drift surfaces as `price-changed` at the Gate and is a correct refusal, not a bug. Document it where merchants read it, because "the agent quoted an old price" is the support ticket this phase generates.
- Both formats are validated in CI against the published attribute requirements, not against our own idea of them. An invented field passes our tests and fails the only reader that matters.
- DONE WHEN: a live feed validates clean in a real Merchant Center account; a scheduled fetch updates within its window with no manual step; the JSON-LD validates in Google's structured-data tooling on a group page and an item page; an unexposed item and an exact stock count each fail a build assertion if they reach either format.

## D2 — The sidecar is addable by any MCP client

- Expose the sidecar's existing MCP surface at a public URL a stranger pastes into any MCP client. No application, no allowlist, no partner agreement — this is ADR-0012 becoming distribution rather than a principle. The tool set is the closed action set the trait already implies; nothing new is invented for this door.
- Authority is unchanged and this is the whole point: reads are open, anything that builds a basket needs a token the agent obtains by self-registration, and spend needs the tap. A new front door must not become a new authority path.
- A public MCP endpoint is a genuinely new attack surface — unauthenticated strangers reaching tool execution. It inherits SPEC §12 rate limits per agent identity and per IP, response size caps, and the same uniform refusals (`code-invalid` stays uninformative here too). Budget for abuse before listing, not after.
- List in the public MCP registries and ship a one-paste install snippet in the docs. The buyer-chat demo is already a client of exactly this; the work is making the server side something a stranger adds in one line.
- DONE WHEN: a stock MCP client with no prior knowledge of the shop completes search → add → checkout-start → approve-URL handoff; every spend path still terminates in a tap; a load test at the published rate limits leaves the money core untouched and refuses with named codes.

## D3 — Meet merchants where they already install things

- A WooCommerce adapter implementing the 9-door trait, distributed through the WordPress plugin directory — a searchable channel into the installed base, entered without anyone's approval. The GitHub README is not a distribution channel; the plugin directory is.
- The adapter is a **fourth root** and the firewall extends to it unchanged: it implements the doors over HTTP with signed webhooks and imports nothing from `src/openstore/sidecar/`. It is a Merchant, not a part of the sidecar.
- It ships against the same trait conformance suite the demo store passes. A second implementation is the only real proof the trait is a contract and not a description of one codebase — expect it to find ambiguities in the door shapes, and fix them in the contract rather than in the adapter.
- Wedge honestly: the merchants this reaches are the ones the large platforms' agentic route does not serve — self-hosted stores, and Indian D2C with per-line place-of-supply GST, MRP-inclusive pricing, and composite supply, which SPEC §4 handles at a depth the alternatives do not. Lead with "correct GST on agent orders," which a merchant reacts to, not with "proof-carrying commerce," which they do not.
- DONE WHEN: a stock WooCommerce install reaches a verified receipt with no core file edits; the conformance suite passes against it unmodified; every ambiguity it exposed landed in the trait contract and in the demo store in the same pass (EXPANSION §3).

## D4 — An OpenStore-native Index of Listings exists

**Why here and not first.** An Index with nothing in it is a directory of one demo store, and building it before D1–D3 have produced live merchants is the reach-before-install mistake this track's entry condition already refuses one level up. D1–D3 make merchants exist and reachable; D4 makes them findable. It sits before D6 because a gated-surface conversation goes better when there is a directory to point at.

- Extend `lists/seed.json` (SPEC §3, PLAN-sidecar.md S2) with `verified_as_of` and a signature over the record — it already carries name/category/region/sidecar URL/pubkey/protocols, and a Listing is that record plus proof it is current and self-published. The format is fixed once, in S2, and read unchanged here; no second format.
- Query is category + region, which is exactly the two axes the format carries — the Index has no third axis to filter on and inventing one is how filtering becomes ranking. ADR-0022 governs what the Index is and is not allowed to do; this phase is that ADR becoming code.
- Query surface is an MCP tool — the same shape a Directory Client already uses to query a sidecar — returning matching Listings verbatim, unranked, in an order derived from the query alone.
- Domain-ownership proof at registration reuses ADR-0012's well-known-challenge pattern unchanged: a Merchant proves control of the sidecar's host before its Listing appears in any query result. A periodic re-verification sweep (~30d) re-fetches the registered sidecar's well-known manifest and drops a Listing that no longer matches; this is the only re-fetch that happens, and it targets a URL the Merchant registered, not an open crawl.
- **Withdrawal is an action, not decay.** A Merchant publishes a signed withdrawal that removes the Listing from results on receipt, and an Index that has stopped serving a withdrawn Listing within its own rate-limit window is the gate. Decay by sweep is the backstop for a sidecar that went dark without withdrawing, not the mechanism — a closed shop staying listed for thirty days is a directory that lies about who is open, and any Mirror must honour the withdrawal from the same open feed or it is a fork rather than a mirror.
- Abuse controls before any Listing appears anywhere: domain-ownership proof, a minimum listing age, per-Index rate limits on registration and on withdrawal. Budget for spam before the Index is queryable, not after — the same discipline D2 applied to the public MCP surface.
- The Reference Index OpenStore runs ships with a published, versioned Listing format and query API so a second Index (Mirror) needs zero coordination to stand up against the same open Listing feed. Building a Mirror from the published spec is the actual proof the Reference Index isn't privileged.
- The Index is not a Registrar and must not quietly become one: it holds no funds, no keys, no consumer data, and no accept/reject lever beyond the automated checks above (ADR-0022, ADR-0025). Any future rail-enrolment work is a separate, separably-operated thing, and the Index must stay independently mirrorable so a load-bearing Registrar never makes the directory load-bearing with it.
- DONE WHEN: a Merchant's sidecar publishes a signed Listing and it appears in a category+region query within one registration cycle; a signed withdrawal removes it from results within the Index's own rate-limit window, without waiting for a sweep, and the sweep independently drops a Listing whose sidecar has gone dark; an independently-run Mirror built only from the published format and feed returns the same result set as the Reference Index for the same query, withdrawals included; no code path in the Index sorts, scores, or gates a Listing beyond the abuse controls named above, asserted by a test that plants a popularity field and fails the build.

## D5 — The evidence model becomes legible

- A public verifier page: drop a receipt, see it verify, see exactly which claims are checked and which are `unopened` because the PII commitments cannot be opened without the Merchant's salt (ADR-0011). This is the demo that explains the product to people who will never read an ADR.
- Verification runs **client-side and offline** — the page must work with the network disabled after load, and must not transmit the receipt anywhere. A verifier that uploads the thing it verifies has quietly become a third party to the evidence.
- Same checker logic as the CLI, same golden vectors, or the page is a second implementation that can disagree with the real one.
- DONE WHEN: a receipt from the demo verifies in the page with networking off; a tampered receipt names the failing claim; a receipt whose PII was erased under DPDP verifies with `unopened` and no error; the page and the CLI agree byte-for-byte on every golden vector.

## D6 — Gated surfaces, only with merchants behind us

- The large assistant surfaces run merchant programmes and bilateral integrations. These are business development, not engineering, and they are approached only once D1–D3 have produced live merchants with real orders — an application backed by three stores is a different conversation from one backed by a specification.
- Terms are fixed in advance by ADR-0016: we integrate as **redirect completion**. We do not accept a delegated payment credential to obtain native in-agent completion, and the conformance badge keeps naming the deviation inline (SPEC §9). The Indian regulatory picture may make our tap the easier posture rather than the harder one — additional-factor requirements on card-not-present spend, tokenisation rules for stored credentials, and a UPI PIN that is by construction entered by the payer in the payer's own app. That argument needs an actual legal opinion before it is made to a counterparty, and getting one is a task in this phase, not a footnote.
- DONE WHEN: no integration in this phase introduced a credential an agent can spend without the Consumer present; any that demanded one was declined and the declination written down with its reason.
