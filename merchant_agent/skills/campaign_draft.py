"""Campaign draft skill: draft a promotional message for an occasion tag.

Filters the catalog for products matching the given occasion_tag, then
produces a short promotional blurb. Purely generative — produces text
for a human to review/send elsewhere, initiates nothing.
"""

from merchant_agent.config import settings
from merchant.catalog.yaml_adapter import YAMLCatalogAdapter

_catalog = YAMLCatalogAdapter(settings.merchant_config_path)


def campaign_draft_skill(occasion_tag: str) -> dict:
    """Draft a promotional message for products matching the occasion tag.

    Args:
        occasion_tag: e.g. "occasion:birthday", "occasion:anniversary"

    Returns:
        {"draft_text": str, "referenced_skus": list[str]}
    """
    all_products = _catalog.list_all()
    matching = [
        p for p in all_products
        if occasion_tag in (p.tags or [])
    ]

    if not matching:
        return {
            "draft_text": f"No products found matching '{occasion_tag}'.",
            "referenced_skus": [],
        }

    names = [p.name for p in matching]
    skus = [p.sku for p in matching]
    product_list = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]

    occasion_label = occasion_tag.replace("occasion:", "").replace("-", " ").title()
    draft = (
        f"Celebrate with Gelateria Roma! For your {occasion_label.lower()} "
        f"occasion, try our {product_list} — crafted with authentic Italian "
        f"recipes and the finest ingredients. Order now for a sweet celebration!"
    )

    return {
        "draft_text": draft,
        "referenced_skus": skus,
    }
