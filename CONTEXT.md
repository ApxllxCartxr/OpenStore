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
The present integer count per SKU, always set and never empty, exposed as `available`.
_Avoid_: null, unmanaged, infinite

**Pending Cart**:
A Buyer Agent's basket stored with an expiry that holds no money until the Consumer taps. Distinct from a `pending` order row.
_Avoid_: hold, reservation, order, draft

**Order**:
A Merchant-book row tracking one basket to completion with 8 canonical statuses.
_Avoid_: cart, ticket, hold

**Catalogue Item**:
The sellable unit: one resolved combination of options — black tote in size M — owning its own SKU, price, stock, low-stock threshold, HSN/SAC, and GST rate. This is what a cart line, a reservation, and an Attestation all refer to. Truth lives with the Merchant.
_Avoid_: product, listing, style, parent

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
The hash of the canonical cart plus total plus expiry, bound to the Consumer tap.
_Avoid_: checksum, cart id

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

**Agent Profile**:
A Buyer Agent's self-published document at a well-known URL carrying its name, contact, and ES256 JWKS, used to verify its signed requests and to admit strangers without prior Merchant action. It admits; it never authorizes a spend.
_Avoid_: trust tier, verified agent, credential

**Reversal**:
A Ledger entry recording money taken back by someone other than the Merchant — a Provider-reported dispute or chargeback. Distinct from a Refund, which the Merchant chooses.
_Avoid_: chargeback fee, dispute case, clawback

**Discount Code**:
A Merchant-entered code that lowers a Quote, either `public` (advertised, carried by the Buyer Agent) or `private` (single-recipient, single-use by default, entered only on the approve page and never seen by an agent). Validated and consumed by the Merchant; never interpreted by the sidecar.
_Avoid_: coupon, promo engine, offer, campaign

**Place of Supply**:
The GST rule deciding whether a line is taxed CGST/SGST or IGST, evaluated per line — the Destination state for goods, the location where the service is performed for a service sold at the premises.
_Avoid_: tax zone, ship-to state, region

**Add-on**:
A sellable unit that only exists attached to a parent line — gift-wrap, an extra charm. It is a composite supply: its amount folds into the parent line's taxable value and inherits the parent's GST rate, HSN/SAC, and Place of Supply. An Add-on with no parent is refused, never sold alone.
_Avoid_: upsell, bundle, extra, variant

**Availability Bucket**:
What a Buyer Agent is told about stock — `in-stock`, `low-stock`, or `sold-out`, cut at the Merchant's own low-stock threshold. Exact counts stay inside the Merchant system and the sidecar.
_Avoid_: quantity, inventory level, count

**Agent Surface**:
A place a Consumer's agent already lives — an assistant with shopping built in, an MCP client, a feed-ingesting AI channel. Divided into *permissionless* surfaces reached by being ingestible (feeds, markup, an addable MCP endpoint) and *gated* surfaces reached only through a programme or partner agreement. Reach is a distribution problem, never a reason to weaken the Gate.
_Avoid_: channel, marketplace, platform, mall

**Product Feed**:
The read-only export of the exposed catalogue in Google Merchant Center product-data attribute names, one entry per Catalogue Item grouped by `item_group_id`, served at a stable public URL for a registry to fetch on a schedule. Advertised prices only — the Quote remains the authoritative number, and drift between them surfaces as `price-changed`.
_Avoid_: catalogue dump, sync, listing export
