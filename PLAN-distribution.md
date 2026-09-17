# Plan — Distribution (phase two, starts after the install gate)

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

## D4 — The evidence model becomes legible

- A public verifier page: drop a receipt, see it verify, see exactly which claims are checked and which are `unopened` because the PII commitments cannot be opened without the Merchant's salt (ADR-0011). This is the demo that explains the product to people who will never read an ADR.
- Verification runs **client-side and offline** — the page must work with the network disabled after load, and must not transmit the receipt anywhere. A verifier that uploads the thing it verifies has quietly become a third party to the evidence.
- Same checker logic as the CLI, same golden vectors, or the page is a second implementation that can disagree with the real one.
- DONE WHEN: a receipt from the demo verifies in the page with networking off; a tampered receipt names the failing claim; a receipt whose PII was erased under DPDP verifies with `unopened` and no error; the page and the CLI agree byte-for-byte on every golden vector.

## D5 — Gated surfaces, only with merchants behind us

- The large assistant surfaces run merchant programmes and bilateral integrations. These are business development, not engineering, and they are approached only once D1–D3 have produced live merchants with real orders — an application backed by three stores is a different conversation from one backed by a specification.
- Terms are fixed in advance by ADR-0016: we integrate as **redirect completion**. We do not accept a delegated payment credential to obtain native in-agent completion, and the conformance badge keeps naming the deviation inline (SPEC §9). The Indian regulatory picture may make our tap the easier posture rather than the harder one — additional-factor requirements on card-not-present spend, tokenisation rules for stored credentials, and a UPI PIN that is by construction entered by the payer in the payer's own app. That argument needs an actual legal opinion before it is made to a counterparty, and getting one is a task in this phase, not a footnote.
- DONE WHEN: no integration in this phase introduced a credential an agent can spend without the Consumer present; any that demanded one was declined and the declination written down with its reason.
