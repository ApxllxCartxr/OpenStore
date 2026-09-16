# OpenStore catalog adapter SDK — package surface (S25).

from openstore.surfaces.adapters.base import (
    RELATED_RULES,
    RELATED_YAML,
    AdapterCapability,
    AdapterHealth,
    CatalogAdapter,
    OrderStatusUpdate,
)
from openstore.surfaces.adapters.cache import AdapterCache
from openstore.surfaces.adapters.errors import AdapterError
from openstore.surfaces.adapters.normalize import (
    clean_sku,
    normalize_tags,
    normalized_item,
    parse_stock,
    price_to_minor,
    strip_html,
)
from openstore.surfaces.adapters.registry import (
    build_adapter,
    coerce_source,
    get_adapter,
    register_adapter,
    resolve_catalog_origin,
)

__all__ = [
    "RELATED_RULES",
    "RELATED_YAML",
    "AdapterCache",    "AdapterCapability",
    "AdapterError",
    "AdapterHealth",
    "CatalogAdapter",
    "OrderStatusUpdate",
    "build_adapter",
    "clean_sku",
    "coerce_source",
    "get_adapter",
    "normalize_tags",
    "normalized_item",
    "parse_stock",
    "price_to_minor",
    "register_adapter",
    "resolve_catalog_origin",
    "strip_html",
]
