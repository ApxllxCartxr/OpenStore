# OpenStore catalog adapter SDK — shared interface (S25 / Q-046).
#
# A CatalogAdapter is structural (typing.Protocol): no inheritance
# requirement, matching the repo's composition preference. Capability
# declaration is what lets inventory (S26) and the console UI degrade
# honestly ("WooCommerce: stock sync yes, order write-back yes" vs
# "CSV: read-only") instead of discovering gaps at runtime.
#
# Normalized item shape (frozen once, early — compute_catalog_digest, the
# ES256 attestations, and the conformance goldens all hash/serve it):
#   sku, name, unit_minor, tags (sorted), related_skus, related_source,
#   description, offers, stock (int | None — None = unmanaged, never 0).

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol


class AdapterCapability(str, enum.Enum):
    """Closed set (REGISTRY.json `adapter_capabilities`, exhaustiveness-tested
    alongside ledger_entries). A capability listed here is a promise the
    adapter keeps; unlisted means the caller must degrade, never probe."""

    CATALOG_READ = "CATALOG_READ"
    STOCK_READ = "STOCK_READ"
    STOCK_WRITE = "STOCK_WRITE"
    ORDER_WRITE = "ORDER_WRITE"
    RELATED_SKUS = "RELATED_SKUS"
    VARIANTS = "VARIANTS"


@dataclass(frozen=True)
class AdapterHealth:
    ok: bool
    detail: str = ""
    item_count: int | None = None


@dataclass(frozen=True)
class OrderStatusUpdate:
    order_id: str
    status: str
    updated_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# Provenance values for NormalizedItem["related_source"]: where THIS item's
# related_skus came from. Adapter items use their adapter name; YAML items
# use "yaml"; Stage 27 rules use "merchandising-rules".
RELATED_YAML = "yaml"
RELATED_RULES = "merchandising-rules"  # Stage 27 reserves this value.


class CatalogAdapter(Protocol):
    name: str
    capabilities: frozenset[AdapterCapability]

    def fetch_items(self) -> list[dict[str, Any]]: ...
    def fetch_stock(self, skus: list[str]) -> dict[str, int]: ...
    def write_stock(self, deltas: dict[str, int]) -> None: ...
    def push_order_status(self, order: OrderStatusUpdate) -> None: ...
    def health_check(self) -> AdapterHealth: ...
