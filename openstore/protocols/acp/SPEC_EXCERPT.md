# ACP — SPEC EXCERPT

Source: https://agenticcommerce.dev/docs/concepts/lifecycle
Fetched: 2026-08-28 (UTC)

The Agentic Commerce Protocol (ACP) specifies an agent-facing checkout
lifecycle. ChatGPT (or any agent) drives a four-endpoint flow:

1. **create checkout session** — called with cart contents and buyer context
   when the customer signals intent to buy.
2. **update session** — as the customer modifies their selection.
3. **complete checkout** — finalizes the order.
4. **cancel checkout** — may be called at any point to abort.

The checkout session progresses through states: `not_ready_for_payment`,
`ready_for_payment`, `completed`, `cancelled`, `expired`.

## Product feed
The product feed schema carries product ID, title, description, price, GTIN,
MPN, images, availability and shipping details. Prices are integer minor units.

## Delegated payments
The ACP payments layer supports a **delegated payment spec** (e.g. Stripe Agentic
Suite): a bearer credential issued by a PSP to the agent. It authorizes the PSP
to be charged; it is a payment credential, not a human-authorisation artifact.
This is why a delegated payment credential caps the transaction at AAL1 (§4).

## Authorization model
Sessions may be authenticated via OAuth 2.0 bearer tokens or an HTTP Message
Signature identifying the software agent at the edge.
