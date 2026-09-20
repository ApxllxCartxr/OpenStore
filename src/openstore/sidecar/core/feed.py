"""`/agent/feed.json` — the exposed catalogue in Google Merchant Center names.

**Their attribute names, not ours.** Inventing a third private schema would
produce a file nobody ingests (SPEC §3). One item per Catalogue Item, grouped by
`item_group_id`, which is how variants are expressed in every feed that matters.

`availability` takes **the reader's published enum** and never the Availability
Bucket verbatim: `low-stock` is not a member of it and folds into `in_stock`. A
feed carrying an invented value is rejected by the only reader that matters, so
the mapping is a function with a test rather than a hopeful string.

A feed is the easiest place in the system to leak inventory, because it is
designed to be read by strangers. Two rules hold without exception: only exposed
items appear, and **no exact count ever does**.
"""

from __future__ import annotations

from typing import Any

from openstore.sidecar.core.codes import AvailabilityBucket
from openstore.sidecar.evidence.bundle import format_rupees
from openstore.sidecar.trait.buckets import bucket_for
from openstore.sidecar.trait.models import CatalogueItem, ProductGroup

#: Merchant Center's published enum. `low-stock` is deliberately absent — it is
#: ours, not theirs.
FEED_AVAILABILITY = {
    AvailabilityBucket.IN_STOCK: "in_stock",
    AvailabilityBucket.LOW_STOCK: "in_stock",
    AvailabilityBucket.SOLD_OUT: "out_of_stock",
}


def availability_for(bucket: AvailabilityBucket) -> str:
    return FEED_AVAILABILITY[bucket]


def feed_price(price_minor: int, currency: str = "INR") -> str:
    """Merchant Center wants `"899.00 INR"`.

    Built from paise by integer arithmetic, and it is the **tax-inclusive**
    shopper-facing price: India requires that in feeds, and an `Offer.price`
    excluding GST understates every listing.
    """
    return f"{format_rupees(price_minor).lstrip('₹').replace(',', '')} {currency}"


def feed_item(
    item: CatalogueItem,
    group: ProductGroup,
    *,
    available: int,
    base_url: str,
) -> dict[str, Any]:
    """One Catalogue Item. The count goes in and a bucket comes out."""
    bucket = bucket_for(available, item.low_stock_threshold)
    entry: dict[str, Any] = {
        "id": item.sku,
        "item_group_id": group.id,
        "title": item.name,
        "description": group.description or item.name,
        "link": f"{base_url.rstrip('/')}/p/{group.slug}?sku={item.sku}",
        "availability": availability_for(bucket),
        "price": feed_price(item.price_minor),
        "condition": "new",
    }
    if item.media:
        entry["image_link"] = item.media[0]
    for axis, attribute in (("colour", "color"), ("color", "color"), ("size", "size")):
        if axis in item.options:
            entry[attribute] = item.options[axis]
    return entry


def build_feed(
    groups: list[ProductGroup],
    items: list[CatalogueItem],
    stock: dict[str, int],
    *,
    base_url: str,
    exposed: set[str] | None = None,
) -> dict[str, Any]:
    """The whole feed.

    `exposed` is the Merchant's own exposure set. `None` means everything active,
    which is the demo's configuration — not a default that quietly publishes an
    unexposed item.
    """
    by_id = {g.id: g for g in groups}
    entries = []
    for item in items:
        if item.status != "active":
            continue
        if exposed is not None and item.sku not in exposed:
            continue
        group = by_id.get(item.group_id)
        if group is None:
            continue
        entries.append(feed_item(item, group, available=stock.get(item.sku, 0), base_url=base_url))
    return {"items": sorted(entries, key=lambda e: str(e["id"]))}
