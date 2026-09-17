# OpenStore

Rebuild of the self-hosted agentic storefront sidecar with solid requirements and guardrails on from day one.

## Language

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
The per-merchant money mover — e.g. Justpay, Airpay, Razorpay. Untrusted; never decides authority.
_Avoid_: PSP, gateway, rail, processor

**Stock**:
The present integer count per SKU, always set and never empty.
_Avoid_: null, unmanaged, infinite, available

**Pending Cart**:
A Buyer Agent's basket stored with an expiry that holds no money until the Consumer taps.
_Avoid_: hold, reservation, order, draft

**Catalogue Item**:
A Merchant-owned sellable unit with SKU, price, and options. Truth lives with the Merchant.
_Avoid_: product, listing, variant

**Ledger**:
The sidecar's own money notebook mirroring each order's holds and captures. Append-only, never edited.
_Avoid_: log, history, balance
