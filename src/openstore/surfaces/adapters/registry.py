# OpenStore catalog adapter SDK — provider registry (S25 / DECISION-046).
#
# register_adapter(name, builder) / get_adapter(config, purpose=...) mirrors
# agents/llm.py::register_provider, the in-repo precedent for a provider
# registry. Source resolution (Q-046 independent-resolution rule):
#   - purpose="catalog": config.catalog_source, else legacy config.shopify,
#     else legacy explicit config.catalog_path, else empty YAML (today's
#     missing-file -> [] behavior, preserved so directly-built Settings
#     without any source keep working).
#   - purpose="stock": config.stock_source, else the catalog adapter itself
#     (D2 overlay reads platform stock through the same adapter; Stage 26
#     consumes this). A stock purpose on an adapter without STOCK_READ fails
#     loud with catalog.adapter_not_configured.
# DEF-11: two legacy/new catalog origins at once fail loud with
# catalog.adapter_multiple_sources. catalog_path counts when set:
# load_config only defaults it when NO other source is configured, so a set
# value is always an explicit choice (assignment and YAML alike).

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from openstore.surfaces.adapters.base import AdapterCapability, CatalogAdapter
from openstore.surfaces.adapters.errors import AdapterError, multiple_sources, not_configured

Builder = Callable[[Any, Any, httpx.Client | None], CatalogAdapter]
# Builder signature: (config, source_settings, client) -> adapter. Most
# adapters only need their source block; YAML also reads
# config.catalog_path, so the full Settings travels along.

_BUILDERS: dict[str, Builder] = {}
_IMPORTED = False


def register_adapter(name: str, builder: Builder) -> None:
    _BUILDERS[name] = builder


def _ensure_imported() -> None:
    global _IMPORTED
    if _IMPORTED:
        return
    # Lazy: keeps import cost off the money path and dodges any import cycle
    # (each module self-registers on import).
    from openstore.surfaces import catalog as _yaml_mod  # noqa: F401
    from openstore.surfaces import shopify_catalog as _shopify_mod  # noqa: F401
    from openstore.surfaces.adapters import (  # noqa: F401
        bigcommerce_adapter,
        csv_adapter,
        magento_adapter,
        wix_adapter,
        woo_adapter,
        zoho_adapter,
    )
    _IMPORTED = True


def _source_type(source: Any) -> str | None:
    if source is None:
        return None
    kind = getattr(source, "type", None)
    if isinstance(kind, str):
        return kind
    if isinstance(source, dict):
        kind = source.get("type")
        return kind if isinstance(kind, str) else None
    return None


def resolve_catalog_origin(config: Any) -> tuple[str, Any]:
    """(kind, source-settings) for the catalog purpose. Raises AdapterError
    on multiple configured origins (DEF-11). Never raises on zero origins
    (empty-YAML fallback preserves load_catalog's missing-file -> [])."""
    union = getattr(config, "catalog_source", None)
    union_kind = _source_type(union)
    legacy_shopify = getattr(config, "shopify", None)
    yaml_path = getattr(config, "catalog_path", None)
    origins = [
        label
        for label, present in (
            ("catalog_source", union_kind is not None),
            ("shopify", legacy_shopify is not None),
            ("catalog_path", yaml_path is not None),
        )
        if present
    ]
    if len(origins) > 1:
        raise multiple_sources(f"catalog origins configured at once: {', '.join(origins)}")
    if union_kind is not None:
        return union_kind, union
    if legacy_shopify is not None:
        return "shopify", legacy_shopify
    return "yaml", None


def coerce_source(raw: Any) -> Any:
    """Validate a raw source mapping into its discriminated config model.

    Used by /merchant/setup connect-and-test, where the merchant submits a
    candidate source that is NOT yet their configured one. Pydantic
    rejections wrap into catalog.adapter_not_configured (fail loud, closed
    set — the raw ValidationError is detail, never a new code)."""
    from openstore import config as _config

    models = {
        "yaml": _config.YamlSource,
        "csv": _config.CsvSource,
        "sheets": _config.SheetsSource,
        "shopify": _config.ShopifySource,
        "woocommerce": _config.WooCommerceSource,
        "bigcommerce": _config.BigCommerceSource,
        "magento": _config.MagentoSource,
        "wix": _config.WixSource,
        "zoho": _config.ZohoSource,
    }
    kind = raw.get("type") if isinstance(raw, dict) else _source_type(raw)
    model: Any = models.get(kind or "")
    if model is None:
        raise not_configured(str(kind), "source type (unknown catalog_source.type)")
    if not isinstance(raw, dict):
        return raw
    try:
        return model.model_validate(raw)
    except Exception as e:
        raise not_configured(kind or "unknown", f"source fields invalid: {e}") from e


def build_adapter(source: Any, client: httpx.Client | None = None) -> CatalogAdapter:
    """Build an adapter from an explicit source (validated model or raw
    mapping) without touching Settings — the setup connect-and-test path."""
    _ensure_imported()
    if isinstance(source, dict):
        source = coerce_source(source)
    kind = _source_type(source)
    if kind is None:
        raise not_configured("unknown", "source type")
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise not_configured(kind, "source type")
    return builder(None, source, client)


def get_adapter(config: Any, purpose: str = "catalog") -> CatalogAdapter:
    """Resolve the adapter for `purpose` ("catalog" | "stock")."""
    _ensure_imported()
    if purpose == "stock":
        union = getattr(config, "stock_source", None)
        union_kind = _source_type(union)
        if union_kind is not None:
            kind, source = union_kind, union
        else:
            kind, source = resolve_catalog_origin(config)
    elif purpose == "catalog":
        kind, source = resolve_catalog_origin(config)
    else:
        raise AdapterError(
            "catalog.adapter_not_configured", f"unknown adapter purpose {purpose!r}"
        )
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise not_configured(kind, f"purpose {purpose} (unknown source type)")
    adapter = builder(config, source, None)
    if purpose == "stock" and AdapterCapability.STOCK_READ not in adapter.capabilities:
        raise not_configured(
            adapter.name, "stock sync (no STOCK_READ capability)"
        )
    return adapter
