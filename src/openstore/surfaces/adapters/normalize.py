# OpenStore catalog adapter SDK — shared normalization (S25).
#
# Helpers lifted from the Shopify adapter so every adapter parses money,
# HTML, tags, and SKUs identically: one paise-exact rule, one tag rule,
# one skip rule. Fractional minor units are FATAL, never rounded (R0.5).

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any


def price_to_minor(price: object) -> int:
    """Decimal price string/number -> integer minor units, paise-exact."""
    try:
        minor = Decimal(str(price)) * 100
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"price is not a decimal: {price!r}") from e
    if minor != minor.to_integral_value():
        raise ValueError(f"price has fractional minor units: {price!r}")
    return int(minor)


class _TextStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(html_text: object, limit: int = 500) -> str:
    """Product body_html -> plain text (stdlib only, no new dependency)."""
    import html as _html

    stripper = _TextStripper()
    stripper.feed(str(html_text or ""))
    return _html.unescape(" ".join("".join(stripper.parts).split()))[:limit]


def normalize_tags(tags: object) -> list[str]:
    """Comma-string or list -> sorted unique tag list."""
    if tags is None:
        return []
    if isinstance(tags, str):
        raw = [t.strip() for t in tags.split(",")]
    elif isinstance(tags, list | tuple):
        raw = [str(t).strip() for t in tags]
    else:
        return []
    return sorted({t for t in raw if t})


def clean_sku(sku: object) -> str:
    """Merchant-authored SKU or "" — never synthesized (Q-037 precedent:
    the compiler and attestations join on merchant keys)."""
    return str(sku or "").strip()


def parse_stock(value: object) -> int | None:
    """Platform stock_quantity -> int, None when unmanaged/absent.

    None = unmanaged (made-to-order, services) and must NEVER become 0 —
    Stage 26 gates on None-vs-number explicitly. Negative platform stock
    is merchant data corruption: fail loud, do not clamp silently."""
    if value is None or value == "":
        return None
    try:
        qty = int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"stock is not an integer: {value!r}") from e
    if qty < 0:
        raise ValueError(f"stock is negative: {value!r}")
    return qty


def normalized_item(
    *,
    sku: str,
    name: str,
    unit_minor: int,
    tags: list[str] | None = None,
    related_skus: list[str] | None = None,
    related_source: str = "yaml",
    description: str = "",
    offers: list[Any] | None = None,
    stock: int | None = None,
) -> dict[str, Any]:
    """The frozen normalized shape every adapter returns byte-identically."""
    return {
        "sku": sku,
        "name": name,
        "unit_minor": unit_minor,
        "tags": sorted(tags or []),
        "related_skus": list(related_skus or []),
        "related_source": related_source,
        "description": description,
        "offers": list(offers or []),
        "stock": stock,
    }
