# AP2 — SPEC EXCERPT

Source: https://ap2-protocol.org/overview/
Fetched: 2026-08-28 (UTC)

AP2 (Agent Payments Protocol, Google) secures agent-performed payments using
verifiable digital credentials (VDCs), specifically SD-JWTs. There are two
mandate types: **Checkout Mandate** and **Payment Mandate**.

A Checkout Mandate provides the Merchant cryptographic proof that the Shopping
Agent is authorized to purchase the cart it assembled. It comes in two stages:
- **Open** — captures the user's constraints/goals before a specific cart is finalized.
- **Closed** — captures the user's (or agent's) authorization for a specific, finalized checkout.

The Merchant MUST provide a merchant-signed JWT containing the Checkout; the
closed Checkout Mandate is bound to this Checkout JWT using a cryptographic hash.
In the human-present flow the signature on the closed mandate is validated as
coming from a User directly; in the human-not-present (autonomous) flow it is
signed by an Agent key, trusted via open mandates signed by the User.

## Constraints a Checkout Mandate may carry
- Allowed Merchants
- Line Items
- Budget (cumulative limit)
- Amount Range (per-transaction limit)
- Reference
- Execution Date

## Cart binding
The closed Checkout Mandate is bound to the Checkout via a hash of the
`checkout_jwt`. This is the field that, when signed by a user-held key and
verifiable offline, makes a Cart Mandate eligible for AAL3 (§4). Without it (or
with only an open/intent mandate), the cap is AAL2.

## Schemes
- `ap2_cart_mandate` — closed Checkout Mandate binding a specific cart.
- `ap2_intent_mandate` — open/intent mandate expressing standing authority.
