# Dual identity on every order

Every money decision records consumer identity (which passkey tapped which cart hash, when) plus agent identity (which OAuth client carried it, which scopes), each revocable without touching the other. The gate requires both valid.

Amended by ADR-0017: consumer identity is no longer always a passkey, so "revocable" needed splitting. Agent identity is a standing credential and revocation means what it always meant. Consumer authority under `upi-pin` is per-order and non-replayable — there is no standing credential to revoke, and needing none is a stronger position than being able to. Revocation therefore applies to standing Consumer credentials where they exist, which is enrolled passkeys. The requirement that the Gate needs both identities valid is unchanged, and `consumer_id` is now a per-Merchant-domain pseudonym for every authority kind (ADR-0011).

Amended by ADR-0012: agent identity is the issued client ID for an allowlisted agent and the RFC 7638 JWK thumbprint of its Agent Profile key for a self-registered stranger. Both are recorded, both revocable, and the requirement that the Gate needs both identities valid is unchanged.
