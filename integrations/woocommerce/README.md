# OpenStore for WooCommerce

Serves the OpenStore nine-door trait from a WooCommerce store, so a sidecar can
make it transactable by any Buyer Agent.

**Why this exists.** The trait is ten HTTP doors with HMAC signing and per-door
idempotency (SPECS/PLAN.md §6.1). Implementing it by hand is a multi-week job,
which means no shop owner ever will — so "makes any Merchant site transactable"
stayed a claim with a sample size of one. This makes it an install.

**What it is not.** It is not a sidecar. It answers about Merchant truth and
decides nothing: no Gate, no Ledger, no Authority, no receipt. WooCommerce stays
the book of record for stock, orders and money (ADR-0001), and the sidecar runs
beside it.

## Install

1. Copy `openstore-sidecar/` into `wp-content/plugins/` and activate it.
2. **WooCommerce → OpenStore**: set the trait HMAC secret to the same value as
   the sidecar's `TRAIT_HMAC_SECRET`, and set your GST registration state.
3. Point the sidecar at the store:

   ```
   TRAIT_BASE_URL=https://shop.example/wp-json/openstore/v1
   TRAIT_HMAC_SECRET=<the same secret>
   ```

4. Prove it, before trusting it:

   ```
   openstore-conform https://shop.example/wp-json/openstore/v1
   ```

**Keep the trait routes off the public internet.** Door 2 answers with exact
stock integers and door 7 answers once with an order's salt. Put the sidecar and
the store on the same private network, or firewall
`/wp-json/openstore/v1/trait/*` to the sidecar's address. The signature is the
authentication; the private network is the second wall, and the design assumes
both.

## The mapping

| OpenStore | WooCommerce |
|---|---|
| Product Group | a variable product (a simple product is its own group of one) |
| Catalogue Item | a variation, or a simple product |
| Stock | the product's stock quantity, with **Manage stock** on |
| Order | a real WooCommerce order, `created_via: openstore-agent` |
| Quote | §16.11 computed from WooCommerce prices, shipping zones and coupons |

**Two WooCommerce habits are refused rather than accommodated**, and the admin
screen counts how many products each one is hiding:

- *An item with no SKU is not published.* The SKU is what a cart line, a
  reservation and an Attestation all refer to.
- *An item with stock management off is not published.* Stock is the present
  integer count, always set and never empty. "In stock" with no number is
  exactly the null the spec refuses, and treating it as infinite is how an agent
  sells something that does not exist.

### Order status

The trait has eight statuses; WooCommerce has its own, and two of the trait's —
`cancelled` and `expired` — land on the same WooCommerce status.

So **the WooCommerce status is authoritative** and the trait status is derived
from it. One meta value is consulted for exactly one thing: telling `expired`
from `cancelled`. A shopkeeper who marks an order complete in the admin has
changed the truth, and a plugin that answered from its own meta would tell the
sidecar something the shop had stopped believing.

| trait | WooCommerce |
|---|---|
| `pending` | `pending` |
| `confirmed` | `on-hold` |
| `paid` | `processing` |
| `completed` | `completed` |
| `cancelled` | `cancelled` |
| `expired` | `cancelled` (+ meta) |
| `failed` | `failed` |
| `refunded` | `refunded` |

### Tax

GST detail is per item, and WooCommerce has nowhere to put it, so the plugin
adds two fields to the product's General tab: **HSN/SAC** and **GST rate (basis
points)** — 1800 is 18%, in basis points so no rate is ever a float. A store-wide
default covers the rest.

Whether prices include tax is read from WooCommerce's own setting rather than
stored again here. Two settings for one fact drift, and this is the fact that
double-charges every order when it is wrong.

### Shipping

Costs come from WooCommerce's own shipping zones, so delivery is configured once
in the place a Merchant already knows. Flat rate, free shipping and local pickup
are priced; a method that needs a cart to price itself — a rate table, a live
carrier lookup, a flat rate written as a formula — is **refused rather than
guessed at**, because a shipping cost the shop would not honour is worse than a
refusal that names the method.

## What is checked, and how

Two things have to be exactly right before anything works at all, and both are
checked against vectors the sidecar itself generates:

```
# from the repository root
docker run --rm -v "$PWD:/src" php:8.2-cli php /src/integrations/woocommerce/tests/run-vectors.php
docker run --rm -v "$PWD:/src" php:8.2-cli php /src/integrations/woocommerce/tests/run-signing.php
```

- **`run-vectors.php`** — §16.11's arithmetic, to the paise. There are now four
  independent implementations of it (the Gate's check, the conformance fake, the
  demo storefront's TypeScript, and this), sharing no code on purpose. Two that
  round differently fire `quote-inconsistent` on a *correct* quote, which looks
  like a bug in the money core and is actually a bug in one of them. The cases
  sit on the edges: odd tax where SGST takes the spare paise, three-way
  apportionment that does not divide, an Add-on folding into its parent, a
  service line whose Place of Supply differs from the rest of the basket.
- **`run-signing.php`** — the HMAC preimage, byte for byte. Get any part of
  `path \n timestamp \n nonce \n body` wrong and you get a signature that is
  stable, plausible and wrong on every request.

Regenerate both with `uv run scripts/make_trait_vectors.py`; `--check` fails if
they have drifted from the sidecar.

The doors themselves are checked by the conformance CLI against a running store,
which is the only proof that matters: `openstore-conform <url>`.

## What this does not do yet

Named rather than discovered:

- **Add-ons need the `addon` tag set by hand** in the product's OpenStore tags
  field. WooCommerce has no concept of a line that folds into another.
- **Percentage coupons are resolved against item prices**, not against a
  WooCommerce cart, because there is no cart here. A coupon with product or
  category restrictions is applied as if it had none.
- **`expires_at` is always null.** The sidecar owns both expiry clocks, and this
  store never self-expires — a WooCommerce cron that cancelled a `pending` order
  on its own would race the tap.
- **Nonce replay protection is per-process** without a persistent object cache.
  The 60-second signature window is the outer bound either way; run Redis or
  Memcached for the stronger guarantee.

## Requirements

WordPress 6.4+, WooCommerce 8.0+, PHP 8.1+. MySQL 5.7+/MariaDB 10.3+ for the
conditional `UPDATE` the reserve depends on.
