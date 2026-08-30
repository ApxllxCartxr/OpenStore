# MCP mapping table (INTEROP_SPEC §6.1)

External field (left) → OpenStore core field (right). Every external field named
in `openstore/protocols/mcp/adapter.py` appears here.

| MCP field | Direction | Core field / type |
|---|---|---|
| `sku` | in | `LineItemRequest.sku` |
| `qty` | in | `LineItemRequest.qty` |
| `query` | in | `SearchQuery.query` |
| `limit` | in | `SearchQuery.limit` |
| `delivery` | in | `DeliveryAddress.raw` |
| `jws` | in | legacy mandate path, NOT promoted to core (§6.1a) |
| `policy` | in | `AuthorityPresentation.raw` (scheme `native_webauthn`) |
| `idempotency_key` | in | `CommerceCore.confirm_checkout(idempotency_key)` |
| (tool result) | out | `ProductView` / `CartView` / `CheckoutView` / `ConfirmResult` |
