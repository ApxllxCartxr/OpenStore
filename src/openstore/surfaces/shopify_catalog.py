# OpenStore surfaces — Shopify read-only catalog source (S18 / DECISION-037)
#
# When Settings.shopify is set, load_catalog reads this store instead of the
# YAML file and normalizes every variant to the exact YAML item shape, so the
# compiler, attestations, feeds and agents downstream never know the difference.
#
# Read-only by construction: this module only ever GETs. Admin tokens live
# 86399s, so they are minted at runtime from the stored credentials and cached
# to expiry-60s — never persisted anywhere (R0.10-adjacent: the only secret at
# rest is the client secret in .env, and error paths never print it).

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

import httpx

from openstore.config import Settings
from openstore.surfaces.adapters.base import AdapterCapability, AdapterHealth
from openstore.surfaces.adapters.errors import AdapterError, not_configured
from openstore.surfaces.adapters.registry import register_adapter

logger = logging.getLogger("openstore.shopify_catalog")

# Verified against the official Admin API docs 2026-09-14 (client-credentials
# grant + versioned REST pattern); pinned, never improvised.
SHOPIFY_API_VERSION = "2025-01"
PAGE_SIZE = 250
MAX_PAGES = 40  # ~10k products; past this the merchant needs a different plan
CACHE_TTL_SECONDS = 60
TOKEN_SKEW_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10.0

# domain -> (token, expires_at_monotonic)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
# domain -> (fetched_at_monotonic, items)
_CATALOG_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _http_client() -> httpx.Client:
    """Seam for tests (monkeypatch this, never httpx globally)."""
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)


def get_admin_token(store_domain: str, client_id: str, client_secret: str) -> str:
    """Mint (or reuse a cached) Admin API token via client-credentials grant."""
    now = time.monotonic()
    cached = _TOKEN_CACHE.get(store_domain)
    if cached is not None and now < cached[1]:
        return cached[0]
    resp = _http_client().post(
        f"https://{store_domain}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "Shopify token exchange failed: "
            f"HTTP {resp.status_code} (check app installation and credentials)"
        )
    body = resp.json()
    token = str(body.get("access_token", ""))
    scopes = str(body.get("scope", "")).split()
    if not token:
        raise RuntimeError("Shopify token exchange returned no access_token")
    if "read_products" not in scopes and "write_products" not in scopes:
        raise RuntimeError(
            "Shopify token lacks read_products "
            f"(granted scopes: {' '.join(sorted(scopes)) or 'none'})"
        )
    expires_in = int(body.get("expires_in", 86399))
    _TOKEN_CACHE[store_domain] = (token, now + max(expires_in - TOKEN_SKEW_SECONDS, 0))
    return token


def _link_next(link_header: str) -> str | None:
    """Extract the rel="next" URL from an RFC 8288 Link header (or None)."""
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) == 2 and segments[1] == 'rel="next"':
            url = segments[0]
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None


def fetch_shop_currency(store_domain: str, token: str) -> str:
    """Read the store's currency (one cheap call, fail loud on HTTP errors)."""
    resp = _http_client().get(
        f"https://{store_domain}/admin/api/{SHOPIFY_API_VERSION}/shop.json"
        "?fields=currency",
        headers={"X-Shopify-Access-Token": token},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Shopify shop read failed: HTTP {resp.status_code}")
    return str(resp.json()["shop"]["currency"])


def fetch_products(store_domain: str, token: str) -> list[dict[str, Any]]:
    """Read all products (cursor pagination, bounded by MAX_PAGES)."""
    client = _http_client()
    url: str | None = (
        f"https://{store_domain}/admin/api/{SHOPIFY_API_VERSION}/products.json"
        f"?limit={PAGE_SIZE}&fields=id,title,tags,body_html,variants"
    )
    products: list[dict[str, Any]] = []
    pages = 0
    while url is not None:
        resp = client.get(url, headers={"X-Shopify-Access-Token": token})
        if resp.status_code != 200:
            raise RuntimeError(f"Shopify products read failed: HTTP {resp.status_code}")
        products.extend(resp.json().get("products", []))
        pages += 1
        url = _link_next(resp.headers.get("Link", ""))
        if url is not None and pages >= MAX_PAGES:
            raise RuntimeError(
                f"Shopify catalog exceeds {MAX_PAGES} pages "
                f"(~{MAX_PAGES * PAGE_SIZE} products); refusing unbounded sync"
            )
    return products


def price_to_minor(price: str) -> int:
    """Shopify decimal price string → integer minor units, paise-exact.

    Fractional cents are a hard error, not a rounding decision (R0.5)."""
    try:
        minor = Decimal(str(price)) * 100
    except InvalidOperation as e:
        raise ValueError(f"Shopify price is not a decimal: {price!r}") from e
    if minor != minor.to_integral_value():
        raise ValueError(f"Shopify price has fractional cents: {price!r}")
    return int(minor)


class _TextStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(html: str, limit: int = 500) -> str:
    """product body_html → plain text (stdlib only, no new dependency)."""
    import html as _html

    stripper = _TextStripper()
    stripper.feed(html or "")
    return _html.unescape(" ".join("".join(stripper.parts).split()))[:limit]


def normalize_variant(
    product_title: str,
    product_tags: str,
    body_html: str,
    variant: dict[str, Any],
) -> dict[str, Any] | None:
    """One Shopify variant → one normalized catalog item, or None to skip.

    Variants without a merchant-authored SKU are skipped (never synthesized —
    Q-037): the compiler and attestations join on merchant keys.
    """
    sku = str(variant.get("sku") or "").strip()
    if not sku:
        return None
    variant_title = str(variant.get("title") or "")
    name = product_title
    if variant_title and variant_title != "Default Title":
        name = f"{product_title} — {variant_title}"
    tags = sorted({t.strip() for t in product_tags.split(",") if t.strip()})
    return {
        "sku": sku,
        "name": name,
        "unit_minor": price_to_minor(str(variant.get("price", "0"))),
        "tags": tags,
        "related_skus": [],  # no Shopify equivalent; deterministic cross-sell skips these
        "description": strip_html(str(body_html or "")),
        "offers": [],  # overlaid live by serve_catalog_feed, same as YAML items
    }


def load_shopify_catalog(config: Settings, max_pages: int | None = None) -> list[dict[str, Any]]:
    """Full read: token → currency gate → products → normalize (TTL-cached)."""
    shop = config.shopify
    if max_pages is None:
        max_pages = MAX_PAGES
    if shop is None:
        raise ValueError("load_shopify_catalog called without config.shopify")
    now = time.monotonic()
    cached = _CATALOG_CACHE.get(shop.store_domain)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    token = get_admin_token(shop.store_domain, shop.client_id, shop.client_secret)
    currency = fetch_shop_currency(shop.store_domain, token)
    if currency != config.merchant.currency:
        raise ValueError(
            f"Shopify shop currency {currency!r} != merchant currency "
            f"{config.merchant.currency!r}: refusing to sync (converting money "
            "invents money; change the store currency in Shopify admin)"
        )
    products = fetch_products(shop.store_domain, token)
    items: list[dict[str, Any]] = []
    skipped = 0
    for product in products:
        for variant in product.get("variants", []):
            item = normalize_variant(
                str(product.get("title", "")),
                str(product.get("tags", "")),
                str(product.get("body_html", "")),
                variant,
            )
            if item is None:
                skipped += 1
            else:
                items.append(item)
    if not items:
        raise ValueError(
            f"Shopify catalog has zero usable variants (skipped {skipped} "
            "without merchant-authored SKUs): every variant needs an SKU"
        )
    if skipped:
        logger.warning(
            "shopify catalog: skipped %d variant(s) without SKUs, %d usable",
            skipped,
            len(items),
        )
    _CATALOG_CACHE[shop.store_domain] = (now, items)
    return items


class ShopifyAdapter:
    """Shopify as an SDK adapter (read-only per DECISION-037)."""

    name = "shopify"
    capabilities = frozenset({AdapterCapability.CATALOG_READ, AdapterCapability.STOCK_READ})

    def __init__(self, source: Any, client: httpx.Client | None = None, merchant_currency: str = "INR", config: Settings | None = None):
        self._source = source
        self._client = client
        self._merchant_currency = merchant_currency
        self._config = config

    def _creds(self) -> tuple[str, str, str]:
        source = self._source
        return (
            str(getattr(source, "store_domain")),
            str(getattr(source, "client_id", "") or ""),
            str(getattr(source, "client_secret", "") or ""),
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        domain, cid, csec = self._creds()
        if not cid or not csec:
            raise not_configured("shopify", "client credentials")
        if self._config is None:
            raise not_configured("shopify", "settings (adapter built without config)")
        max_pages = getattr(self._source, "max_pages", None) or MAX_PAGES
        return load_shopify_catalog(self._config, max_pages=max_pages)

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        return {
            sku: int(by_sku[sku]["stock"])
            for sku in skus
            if sku in by_sku and by_sku[sku].get("stock") is not None
        }

    def write_stock(self, deltas: dict[str, int]) -> None:
        raise not_configured("shopify", "stock write-back (read-only adapter)")

    def push_order_status(self, order: Any) -> None:
        raise not_configured("shopify", "order write-back (read-only adapter)")

    def health_check(self) -> AdapterHealth:
        try:
            items = self.fetch_items()
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=len(items))


def _build_shopify(config: Any, source: Any, client: httpx.Client | None) -> ShopifyAdapter:
    if source is None:
        legacy = getattr(config, "shopify", None)
        if legacy is None:
            raise not_configured("shopify", "config.shopify block")
        source = legacy
    merchant_currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return ShopifyAdapter(source, client, merchant_currency=merchant_currency, config=config)


register_adapter("shopify", _build_shopify)

