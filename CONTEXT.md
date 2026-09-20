# OpenStore

Rebuild of the self-hosted agentic storefront sidecar with solid requirements and guardrails on from day one.

## Language

**Sidecar**:
Single-Merchant middleware that makes one Merchant site transactable by any Buyer Agent. One deploy serves one Merchant domain only.
_Avoid_: mall, marketplace, multi-tenant proxy

**Merchant**:
A human user who owns a store and its keys, catalog, and policies.
_Avoid_: seller, operator, tenant, client

**Consumer**:
A human user who wants to buy and who pays manually every time.
_Avoid_: buyer (human), customer, user, account

**Buyer Agent**:
Any external agent acting for the Consumer — ChatGPT, Claude, or the Consumer's own custom agent. Keyless and proposal-only.
_Avoid_: buyer bot, buyer process, assistant, LLM

**Payment Provider**:
The Merchant's money mover — e.g. Justpay, Airpay, Razorpay. Untrusted; never decides authority.
_Avoid_: PSP, gateway, rail, processor

**Stock**:
The present integer count per Catalogue Item, always set and never empty, carried as `available`. The integer crosses the private trait boundary only; everything agent-facing is an Availability Bucket.
_Avoid_: null, unmanaged, infinite

**Pending Cart**:
A Buyer Agent's basket stored with an expiry that holds no money until the Consumer taps. Distinct from a `pending` order row.
_Avoid_: hold, reservation, order, draft

**Order**:
A Merchant-book row tracking one basket to completion with 8 canonical statuses.
_Avoid_: cart, ticket, hold

**Catalogue Item**:
The sellable unit: one resolved combination of options — black tote in size M — owning its own SKU, price, stock, low-stock threshold, HSN/SAC, and GST rate. This is what a cart line, a reservation, and an Attestation all refer to. Truth lives with the Merchant.
_Avoid_: product, listing (a Listing is a directory record, never a Catalogue Item), style, parent

**Product Group**:
A presentation-only grouping of Catalogue Items sharing a page, gallery, copy, and option axes (colour, size). Never sellable, never reserved, never a cart line; Policy caps are evaluated at this level so two colours of one limited item cannot walk through a per-order cap.
_Avoid_: product (as a sellable thing), variant group, parent SKU

**Gate**:
The sidecar's deterministic check reading Merchant truth fresh before any money move.
_Avoid_: validator, checkout, middleware

**Policy**:
Merchant-owned limits enforced by the Gate (window/count/qty/blocked/tags/caps).
_Avoid_: rules, engine, campaign

**Transcript**:
The byte-stable record of Gate inputs plus per-check results stored with the decision.
_Avoid_: log, trace, history

**Cart Hash**:
The Merchant-side binding over the whole order as agreed, taken over canonical bytes of the sorted lines, the Quote, the commitments to Destination and Contact Point, the chosen Fulfillment Option, total, currency, Merchant domain, and expiry. The exact preimage is fixed in SPEC §4 and frozen before translators exist. Any change to what it covers produces a fresh Cart Hash; whether it also costs a fresh Authority depends on that Authority's Binding (ADR-0017).
_Avoid_: checksum, cart id

**Authority**:
The human permission a spend rests on, recorded as one member of a closed set — `upi-pin`, `passkey`, `confirmed-intent`, `mandate` (ADR-0017) — together with what that member actually bound. A Merchant enables which kinds it accepts. Never held by a Buyer Agent, never implied by a scope, and always named in the Transcript and the receipt, so a reader knows which claim they are being offered rather than assuming the strongest one.
_Avoid_: tap (as a synonym for the category), consent, approval, 2FA

**Binding**:
What an Authority actually covered, declared alongside it rather than inferred: *what* was bound (`cart` / `amount` / `none`), *by whom* (`payer-device` / `payer-bank` / `merchant`), and how. Recorded in the Transcript and the receipt and printed by the verifier, so no reader assumes the strongest claim on offer and two rails stay comparable.
_Avoid_: proof, guarantee, trust level, verification score

**Attestation**:
A Merchant-signed statement over `{sku, price, tags, digest}` pinned to the Order.
_Avoid_: certificate, badge

**Exposure**:
Which Merchant policies are visible to Buyer Agents.
_Avoid_: visibility, publishing

**Ledger**:
The sidecar's own append-only record of each order's holds and captures. Never edited.
_Avoid_: log, history, balance, notebook

**Quote**:
The Merchant's priced answer for one basket at one Destination: subtotal, discount lines, fulfillment options and the chosen cost, tax lines, total. Computed by the Merchant behind door 9, never by the sidecar, and re-verified byte-for-byte before money moves.
_Avoid_: pricing, estimate, totals engine, calculation

**Fulfillment Option**:
One Merchant-offered way to deliver a basket, with an id, a label, an ETA, and a cost in paise. The Consumer's chosen option id is bound into the Cart Hash.
_Avoid_: shipping method, rate, delivery rule

**Tax Line**:
One Merchant-computed tax entry inside a Quote, carrying its rate, its basis, and its GST kind (CGST/SGST or IGST by place of supply).
_Avoid_: tax engine, tax rule, GST module

**Destination**:
Where a basket is delivered. Plaintext lives only in the Merchant order row; evidence commits to a salted hash of it.
_Avoid_: shipping address, customer address, PII blob

**Contact Point**:
The single Consumer email or phone the Merchant notifies about this order. Same storage and hashing rule as Destination.
_Avoid_: account, customer record, profile

**Payer Handle**:
The Consumer's payment identifier as the Provider reports it — a UPI VPA in v1. Same storage rule as Destination and Contact Point: plaintext only in the Merchant order row, under the same retention window and erasure action. It reaches evidence only as the per-domain `consumer_id` pseudonym (ADR-0011), never as itself.
_Avoid_: VPA (in prose), customer id, wallet, account

**Agent Profile**:
A Buyer Agent's self-published document at a well-known URL carrying its name, contact, and ES256 JWKS, used to verify its signed requests and to admit strangers without prior Merchant action. It admits; it never authorizes a spend.
_Avoid_: trust tier, verified agent, credential

**Reversal**:
A Ledger entry recording money taken back by someone other than the Merchant, as the Provider reports it — an adjudicated UPI complaint, a post-settlement adjustment, a bank correction, or a card chargeback. Rail-agnostic by construction. Distinct from a Refund, which the Merchant chooses.
_Avoid_: chargeback fee, dispute case, clawback

**Discount Code**:
A Merchant-entered code that lowers a Quote, either `public` (advertised, carried by the Buyer Agent) or `private` (single-recipient, single-use by default, entered only on the approve page and never seen by an agent). Validated and consumed by the Merchant; never interpreted by the sidecar.
_Avoid_: coupon, promo engine, offer, campaign

**Place of Supply**:
The GST rule deciding whether a line is taxed CGST/SGST or IGST, evaluated per line — the Destination state for goods, the location where the service is performed for a service sold at the premises.
_Avoid_: tax zone, ship-to state, region

**Add-on**:
A sellable unit that only exists attached to a parent line — gift-wrap, an extra charm. It is a cart line like any other — own SKU, own price, own Attestation — and never a Quote Line: as a composite supply its amount folds into the parent's taxable value and inherits the parent's GST rate, HSN/SAC, and Place of Supply. An Add-on with no parent is refused, never sold alone.
_Avoid_: upsell, bundle, extra, variant

**Quote Line**:
One priced row inside a Quote, carrying its own taxable value, GST rate, HSN/SAC and Place of Supply. Not the same thing as a cart line: an Add-on is a cart line with its own SKU, price and Attestation but never a Quote Line, because its amount folds into its parent's. The Gate's subtotal identity is checked over cart lines and therefore holds across the fold.
_Avoid_: line item, row, basket line

**Availability Bucket**:
What a Buyer Agent is told about stock — `in-stock`, `low-stock`, or `sold-out`, cut at the Merchant's own low-stock threshold. Exact counts stay inside the Merchant system and the sidecar.
_Avoid_: quantity, inventory level, count

**Agent Surface**:
A place a Consumer's agent already lives — an assistant with shopping built in, an MCP client, a feed-ingesting AI channel. Divided into *permissionless* surfaces reached by being ingestible (feeds, markup, an addable MCP endpoint) and *gated* surfaces reached only through a programme or partner agreement. Reach is a distribution problem, never a reason to weaken the Gate.
_Avoid_: channel, marketplace, platform, mall

**Product Feed**:
The read-only export of the exposed catalogue in Google Merchant Center product-data attribute names, one entry per Catalogue Item grouped by `item_group_id`, served at a stable public URL for a registry to fetch on a schedule. Advertised prices only — the Quote remains the authoritative number, and drift between them surfaces as `price-changed`.
_Avoid_: catalogue dump, sync, listing export

**Listing**:
A Merchant's own signed, self-published directory record — name, category, region, sidecar URL, public key, protocols, `verified_as_of` — emitted by its sidecar and carried verbatim by any Index. A record of a *store*, never of a product: a Catalogue Item is never a Listing. Its `region` is where the store is, and is never a Place of Supply — the two words touch nothing in common.
_Avoid_: entry, profile, product listing, storefront record

**Index**:
A queryable store of Listings, filtered by category and region only and returned verbatim and unranked (ADR-0022). Ranking is the Directory Client's job, never the Index's, and no Index is privileged over any other.
_Avoid_: mall, marketplace, search engine, directory service

**Mirror**:
An independently-run Index built from the published Listing format and feed with no coordination with anyone. The Reference Index OpenStore runs is one Mirror among any number and holds no feature the rest cannot have.
_Avoid_: replica, cache, partner index, secondary

**Directory Client**:
Whoever queries an Index — a Buyer Agent or a human — and who does all the ranking the Index refuses to do.
_Avoid_: search client, index consumer, crawler
