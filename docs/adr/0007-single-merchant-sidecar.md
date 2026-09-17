# One sidecar serves one Merchant

A Merchant deploys a sidecar for themselves on their own domain (`/`, `/.well-known`, `/agent`, `/agentic` behind one reverse proxy). We chose 1:1 over multi-tenant because passkey RP ID/origin, JWKS, webhook secrets, and ledger scoping all collapse to env config for one domain.

Consequences: no per-Merchant tables or routing in v1; "per-Merchant" wording in older docs means "this Merchant's". A mall later runs N sidecars or a new router with zero money-core changes.
