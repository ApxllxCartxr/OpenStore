# ACP mapping table (INTEROP_SPEC §6.2)

External field (left) → OpenStore core field (right). Every external field named
in `openstore/protocols/acp/adapter.py` appears here.

| ACP field | Direction | Core field / type |
|---|---|---|
| `checkout_session` | in/out | `Actor.protocol_session_id` |
| `cart_items` | in | `LineItemRequest.sku` / `LineItemRequest.qty` |
| `buyer_context` | in | `Actor` (subject `acp:<agent_id>`) |
| `delegated_payment_credential` | in | `AuthorityPresentation.raw` (scheme `acp_delegated_token`) |
| `delivery` | in | `DeliveryAddress.raw` |
| `status` | out | mapped via `ACP_STATE_BY_CHECKOUT_STATUS` |
| `amount` | out | `CheckoutView.total_minor` (via `to_acp_amount`) |
| `currency` | out | `to_acp_amount` value carrier (always `"INR"`) |
| `complete_checkout` | in | `CommerceCore.confirm_checkout` |
| `cancel_checkout` | in | (session cancel; not promoted to core) |
