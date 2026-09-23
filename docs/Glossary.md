# Glossary

> Normative vocabulary. Formerly root `CONTEXT.md`; renamed during the documentation reorganisation. Still law with `Specification.md` and `adr/`.

This file defines the domain language. Each term has one meaning. Use the term, not a synonym. The `_Avoid_` line lists words you must not use for it.

## Terms

### Sidecar

Middleware for a single merchant. It makes one merchant site transactable by any Buyer Agent. One deploy serves one merchant domain only.
_Avoid_: mall, marketplace, multi-tenant proxy

### Merchant

A human who owns a store and its keys, catalog, and policies.
_Avoid_: seller, operator, tenant, client

### Consumer

A human who wants to buy and who pays manually every time.
_Avoid_: buyer (human), customer, user, account

### Buyer Agent

Any external agent acting for the Consumer. Examples are ChatGPT, Claude, or the Consumer's own custom agent. Keyless and proposal-only.
_Avoid_: buyer bot, buyer process, assistant, LLM

### Payment Provider

The Merchant's money mover. Examples are Justpay, Airpay, and Razorpay. Untrusted. It never decides authority.
_Avoid_: PSP, gateway, rail, processor

### Stock

The present integer count per Catalogue Item. Always set, never empty, carried as `available`. The integer crosses the private trait boundary only. Everything agent-facing is an Availability Bucket.
_Avoid_: null, unmanaged, infinite

### Pending Cart

A Buyer Agent's basket, stored with an expiry. It holds no money until the Consumer taps. It is distinct from a `pending` order row.
_Avoid_: hold, reservation, order, draft

### Order

A Merchant-book row tracking one basket to completion. It uses 8 canonical statuses.
_Avoid_: cart, ticket, hold

### Catalogue Item

The sellable unit. It is one resolved combination of options, for example a black tote in size M. It owns its SKU, price, stock, low-stock threshold, HSN/SAC, and GST rate. A cart line, a reservation, and an Attestation all refer to it. Truth lives with the Merchant.
_Avoid_: product, listing (a Listing is a directory record, never a Catalogue Item), style, parent

### Product Group

A presentation-only grouping of Catalogue Items. They share a page, gallery, copy, and option axes (colour, size). Never sellable, never reserved, never a cart line. Policy caps are evaluated at this level. Two colours of one limited item cannot walk through a per-order cap that way.
_Avoid_: product (as a sellable thing), variant group, parent SKU

### Gate

The sidecar's deterministic check. It reads Merchant truth fresh before any money move.
_Avoid_: validator, checkout, middleware

### Policy

Merchant-owned limits, enforced by the Gate (window, count, qty, blocked, tags, caps).
_Avoid_: rules, engine, campaign

### Transcript

The byte-stable record of Gate inputs plus per-check results. It is stored with the decision.
_Avoid_: log, trace, history

### Cart Hash

The Merchant-side binding over the whole order as agreed. It covers canonical bytes of the sorted lines, the Quote, the commitments to Destination and Contact Point, the chosen Fulfillment Option, total, currency, Merchant domain, and expiry. The exact preimage is fixed in Specification §4. It froze before translators existed. Any change to what it covers produces a fresh Cart Hash. Whether it also costs a fresh Authority depends on that Authority's Binding (ADR-0017).
_Avoid_: checksum, cart id

### Authority

The human permission a spend rests on. It is recorded as one member of a closed set: `upi-pin`, `passkey`, `confirmed-intent`, `mandate` (ADR-0017). It travels with what that member actually bound. A Merchant enables which kinds it accepts. Never held by a Buyer Agent. Never implied by a scope. Always named in the Transcript and the receipt. A reader knows which claim they are offered. They never assume the strongest one.
_Avoid_: tap (as a synonym for the category), consent, approval, 2FA

### Binding

What an Authority actually covered, declared alongside it rather than inferred. It states what was bound (`cart`, `amount`, `none`), by whom (`payer-device`, `payer-bank`, `merchant`), and how. It is recorded in the Transcript and the receipt and printed by the verifier. No reader assumes the strongest claim on offer. Two rails stay comparable.
_Avoid_: proof, guarantee, trust level, verification score

### Attestation

A Merchant-signed statement over `{sku, price, tags, digest}`. It is pinned to the Order.
_Avoid_: certificate, badge

### Exposure

Which Merchant policies are visible to Buyer Agents.
_Avoid_: visibility, publishing

### Ledger

The sidecar's own append-only record of each order's holds and captures. Never edited.
_Avoid_: log, history, balance, notebook

### Quote

The Merchant's priced answer for one basket at one Destination. It carries subtotal, discount lines, fulfillment options and the chosen cost, tax lines, and total. The Merchant computes it behind door 9. The sidecar never computes it. The sidecar re-verifies it byte-for-byte before money moves.
_Avoid_: pricing, estimate, totals engine, calculation

### Fulfillment Option

One Merchant-offered way to deliver a basket. It carries an id, a label, an ETA, and a cost in paise. The Consumer's chosen option id is bound into the Cart Hash.
_Avoid_: shipping method, rate, delivery rule

### Tax Line

One Merchant-computed tax entry inside a Quote. It carries its rate, its basis, and its GST kind (CGST/SGST or IGST by place of supply).
_Avoid_: tax engine, tax rule, GST module

### Destination

Where a basket is delivered. Plaintext lives only in the Merchant order row. Evidence commits to a salted hash of it.
_Avoid_: shipping address, customer address, PII blob

### Contact Point

The single Consumer email or phone the Merchant notifies about this order. It follows the same storage and hashing rule as Destination.
_Avoid_: account, customer record, profile

### Payer Handle

The Consumer's payment identifier as the Provider reports it. In v1 it is a UPI VPA. It follows the same storage rule as Destination and Contact Point. Plaintext lives only in the Merchant order row, under the same retention window and erasure action. It reaches evidence only as the per-domain `consumer_id` pseudonym (ADR-0011). Never as itself.
_Avoid_: VPA (in prose), customer id, wallet, account

### Agent Profile

A Buyer Agent's self-published document at a well-known URL. It carries its name, contact, and ES256 JWKS. It verifies the agent's signed requests. It admits strangers without prior Merchant action. It admits. It never authorizes a spend.
_Avoid_: trust tier, verified agent, credential

### Reversal

A Ledger entry recording money taken back by someone other than the Merchant, as the Provider reports it. Examples are an adjudicated UPI complaint, a post-settlement adjustment, a bank correction, or a card chargeback. Rail-agnostic by construction. Distinct from a Refund, which the Merchant chooses.
_Avoid_: chargeback fee, dispute case, clawback

### Discount Code

A Merchant-entered code that lowers a Quote. It is either `public` (advertised, carried by the Buyer Agent) or `private` (single-recipient, single-use by default, entered only on the approve page and never seen by an agent). The Merchant validates and consumes it. The sidecar never interprets it.
_Avoid_: coupon, promo engine, offer, campaign

### Place of Supply

The GST rule deciding whether a line is taxed CGST/SGST or IGST. Evaluated per line. Goods take the Destination state. A service sold at the premises takes the location where it is performed.
_Avoid_: tax zone, ship-to state, region

### Add-on

A sellable unit that exists only attached to a parent line. Examples are gift-wrap and an extra charm. It is a cart line like any other, with its own SKU, price, and Attestation. It is never a Quote Line. As a composite supply its amount folds into the parent's taxable value. It inherits the parent's GST rate, HSN/SAC, and Place of Supply. An Add-on with no parent is refused. Never sold alone.
_Avoid_: upsell, bundle, extra, variant

### Quote Line

One priced row inside a Quote. It carries its own taxable value, GST rate, HSN/SAC, and Place of Supply. It is not a cart line. An Add-on is a cart line with its own SKU, price, and Attestation but never a Quote Line, because its amount folds into its parent's. The Gate checks the subtotal identity over cart lines. It therefore holds across the fold.
_Avoid_: line item, row, basket line

### Availability Bucket

What a Buyer Agent is told about stock: `in-stock`, `low-stock`, or `sold-out`. Cut at the Merchant's own low-stock threshold. Exact counts stay inside the Merchant system and the sidecar.
_Avoid_: quantity, inventory level, count

### Agent Surface

A place a Consumer's agent already lives. Examples are an assistant with shopping built in, an MCP client, or a feed-ingesting AI channel. Two kinds exist. Permissionless surfaces are reached by being ingestible (feeds, markup, an addable MCP endpoint). Gated surfaces are reached only through a programme or partner agreement. Reach is a distribution problem. Never a reason to weaken the Gate.
_Avoid_: channel, marketplace, platform, mall

### Product Feed

The read-only export of the exposed catalogue in Google Merchant Center product-data attribute names. One entry per Catalogue Item, grouped by `item_group_id`. Served at a stable public URL for a registry to fetch on a schedule. Advertised prices only. The Quote remains the authoritative number. Drift between them surfaces as `price-changed`.
_Avoid_: catalogue dump, sync, listing export

### Listing

A Merchant's own signed, self-published directory record. It carries name, category, region, sidecar URL, public key, protocols, and `verified_as_of`. Emitted by its sidecar and carried verbatim by any Index. A record of a store, never of a product. A Catalogue Item is never a Listing. Its `region` is where the store is. Never a Place of Supply. The two words touch nothing in common.
_Avoid_: entry, profile, product listing, storefront record

### Index

A queryable store of Listings. Filtered by category and region only. Returned verbatim and unranked (ADR-0022). Ranking is the Directory Client's job. Never the Index's. No Index is privileged over any other.
_Avoid_: mall, marketplace, search engine, directory service

### Mirror

An independently-run Index. Built from the published Listing format and feed with no coordination with anyone. The Reference Index OpenStore runs is one Mirror among any number. It holds no feature the rest cannot have.
_Avoid_: replica, cache, partner index, secondary

### Directory Client

Whoever queries an Index. A Buyer Agent or a human. They do all the ranking the Index refuses to do.
_Avoid_: search client, index consumer, crawler
