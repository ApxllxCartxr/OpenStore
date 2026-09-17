# Dual identity on every order

Every money decision records consumer identity (which passkey tapped which cart hash, when) plus agent identity (which OAuth client carried it, which scopes), each revocable without touching the other. The gate requires both valid.

Amended by ADR-0012: agent identity is the issued client ID for an allowlisted agent and the RFC 7638 JWK thumbprint of its Agent Profile key for a self-registered stranger. Both are recorded, both revocable, and the requirement that the Gate needs both identities valid is unchanged.
