# AP2 mapping table (INTEROP_SPEC §6.3)

External field (left) → OpenStore core field (right). Every external field named
in `openstore/protocols/ap2/adapter.py` / `emit.py` appears here.

| AP2 field | Direction | Core field / type |
|---|---|---|
| `vct` | in/out | mandate schema version marker |
| `type` | in | selects `ap2_cart_mandate` vs `ap2_intent_mandate` |
| `constraints` | in | container for the mapped constraints below |
| `allowed_merchants` | in | `IntentPolicy.merchant_id` |
| `line_items` | in | cart contents (validated against catalog) |
| `budget` | in | `IntentPolicy.max_spend_total_minor` |
| `amount_range` | in | `IntentPolicy.max_spend_per_tx_minor` |
| `reference` | in | mandate reference (recorded in bundle) |
| `execution_date` | in | `IntentPolicy.not_before` / `expires_at` |
| `cart_hash_bound` | in | compared against `goods.cart_hash` (cart binding) |
| `signature` | in | verified per §4 to set predicate E2; `human_held_key_signature` raises AAL to 3 |
| (emitted) `dropped_fields` | out | fields AP2 cannot represent, MUST be surfaced (§6.3b) |
