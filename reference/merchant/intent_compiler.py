import time
import uuid

from reference.merchant.models import IntentPolicy
from reference.merchant.trace import emit


def verify_cart_against_policy(
    cart_items: list[dict],
    policy: dict,
    merchant_id: str,
    product_lookup: callable,
) -> tuple[bool, str]:
    """The Intent Compiler: mathematically verify a cart against a signed policy.

    Args:
        cart_items: list of {"sku": str, "qty": int, "unit_minor": int}
        policy: the IntentPolicy dict from the signed WebAuthn credential
        merchant_id: the merchant attempting checkout
        product_lookup: callable(sku) -> Product | None, for tag lookups

    Returns:
        (True, "ok") if the cart complies with the policy,
        (False, reason_string) if any item violates the policy.
    """
    intent = IntentPolicy(**policy)
    trace_id = str(uuid.uuid4())

    if intent.merchant_id != merchant_id:
        emit("merchant-server", "Intent Compiler: wrong merchant",
             {"expected": intent.merchant_id, "got": merchant_id},
             trace_id, "blocked")
        return False, f"Policy bound to merchant '{intent.merchant_id}', not '{merchant_id}'"

    if intent.expires_at < time.time():
        emit("merchant-server", "Intent Compiler: policy expired",
             {"expires_at": intent.expires_at}, trace_id, "blocked")
        return False, "Intent policy has expired"

    for item in cart_items:
        sku = item["sku"]

        if sku in intent.blocked_skus:
            emit("merchant-server", "Intent Compiler: blocked SKU",
                 {"sku": sku}, trace_id, "blocked")
            return False, f"Item '{sku}' is explicitly blocked by policy"

        product = product_lookup(sku)
        if product is None:
            emit("merchant-server", "Intent Compiler: unknown SKU",
                 {"sku": sku}, trace_id, "blocked")
            return False, f"Unknown product '{sku}'"

        if intent.allowed_tags:
            item_tags = set(product.tags or [])
            policy_tags = set(intent.allowed_tags)
            if not item_tags.intersection(policy_tags):
                emit("merchant-server", "Intent Compiler: tag violation",
                     {"sku": sku, "item_tags": list(item_tags),
                      "allowed": list(policy_tags)},
                     trace_id, "blocked")
                return False, (
                    f"Item '{sku}' has tags {list(item_tags)} but policy "
                    f"only allows {list(policy_tags)}"
                )

    total = sum(item["unit_minor"] * item["qty"] for item in cart_items)
    if total > intent.max_spend_minor:
        emit("merchant-server", "Intent Compiler: over spend limit",
             {"total": total, "limit": intent.max_spend_minor},
             trace_id, "blocked")
        return False, (
            f"Cart total {total/100:.2f} exceeds policy limit "
            f"{intent.max_spend_minor/100:.2f}"
        )

    emit("merchant-server", "Intent Compiler: cart complies with policy",
         {"total": total, "items": len(cart_items)}, trace_id, "executed")
    return True, "ok"
