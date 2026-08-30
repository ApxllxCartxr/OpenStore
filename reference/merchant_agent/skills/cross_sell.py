"""Cross-sell skill: suggest a complementary product given cart contents.

Reads related_skus/tags from the catalog (YAML config file, not DB),
optionally uses an LLM to phrase a natural suggestion, and returns
{'suggested_sku': str, 'reason': str}. Never touches Cart or Checkout
tables — pure suggestion, no side effect.
"""

import random

from reference.merchant_agent.config import settings
from reference.merchant.catalog.yaml_adapter import YAMLCatalogAdapter

_catalog = YAMLCatalogAdapter(settings.merchant_config_path)


def cross_sell_skill(cart_items: list[dict]) -> dict:
    """Suggest a complementary product for the given cart contents.

    Args:
        cart_items: list of {"sku": str, "qty": int, ...}

    Returns:
        {"suggested_sku": str, "reason": str, "product_name": str}
    """
    cart_skus = {item.get("sku", "") for item in cart_items}
    candidates: dict[str, str] = {}

    for item in cart_items:
        product = _catalog.get_product(item.get("sku", ""))
        if product is None:
            continue
        for related_sku in product.related_skus:
            if related_sku not in cart_skus:
                related_product = _catalog.get_product(related_sku)
                if related_product is not None:
                    candidates[related_sku] = related_product.name

    if not candidates:
        all_products = _catalog.list_all()
        for p in all_products:
            if p.sku not in cart_skus:
                candidates[p.sku] = p.name
                break

    if not candidates:
        return {
            "suggested_sku": "",
            "reason": "No additional products available.",
            "product_name": "",
        }

    suggested_sku = random.choice(list(candidates.keys()))
    suggested_name = candidates[suggested_sku]
    cart_names = []
    for item in cart_items:
        p = _catalog.get_product(item.get("sku", ""))
        if p:
            cart_names.append(p.name)

    if cart_names:
        reason = (
            f"Based on your cart ({', '.join(cart_names)}), "
            f"you might also enjoy our {suggested_name}."
        )
    else:
        reason = f"Our {suggested_name} is a great choice to try."

    return {
        "suggested_sku": suggested_sku,
        "reason": reason,
        "product_name": suggested_name,
    }
