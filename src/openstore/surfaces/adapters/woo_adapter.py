# OpenStore catalog adapter — WooCommerce (S25).
#
# /wp-json/wc/v3/products (+ /variations), consumer key/secret over HTTPS.
# Native stock_quantity/manage_stock -> real STOCK_WRITE. Native
# cross_sell_ids + upsell_ids -> RELATED_SKUS with true provenance (the
# first adapter that populates DEF-6 properly). Capabilities: all five
# (CATALOG_READ, STOCK_READ, STOCK_WRITE, ORDER_WRITE, RELATED_SKUS;
# VARIANTS via the variations endpoint).

from __future__ import annotations

import logging
from typing import Any

import httpx

from openstore.surfaces.adapters.base import (
    AdapterCapability,
    AdapterHealth,
    OrderStatusUpdate,
)
from openstore.surfaces.adapters.errors import (
    AdapterError,
    currency_mismatch,
    not_configured,
    page_cap,
    price_invalid,
    price_missing,
)
from openstore.surfaces.adapters.http import request_json
from openstore.surfaces.adapters.normalize import (
    clean_sku,
    normalize_tags,
    normalized_item,
    parse_stock,
    price_to_minor,
    strip_html,
)
from openstore.surfaces.adapters.registry import register_adapter

logger = logging.getLogger("openstore.adapters.woo")


class WooCommerceAdapter:
    name = "woocommerce"
    capabilities = frozenset(
        {
            AdapterCapability.CATALOG_READ,
            AdapterCapability.STOCK_READ,
            AdapterCapability.STOCK_WRITE,
            AdapterCapability.ORDER_WRITE,
            AdapterCapability.RELATED_SKUS,
            AdapterCapability.VARIANTS,
        }
    )

    def __init__(
        self, source: Any, client: httpx.Client | None = None, merchant_currency: str = "INR"
    ):
        self._source = source
        self._client = client or httpx.Client()
        base = str(getattr(source, "base_url", "") or "").rstrip("/")
        if not base:
            raise not_configured("woocommerce", "base_url")
        key = getattr(source, "consumer_key", "") or ""
        secret = getattr(source, "consumer_secret", "") or ""
        if not key or not secret:
            raise not_configured("woocommerce", "consumer_key/consumer_secret")
        self._api = base + "/wp-json/wc/v3"
        self._auth = (key, secret)
        self._page_size = int(getattr(source, "page_size", 100) or 100)
        self._max_pages = int(getattr(source, "max_pages", 40) or 40)
        self._merchant_currency = merchant_currency
        self._id_by_sku: dict[str, int] = {}
        self._kind_by_id: dict[int, str] = {}  # product id -> "product"|"variation"
        self._parent_by_variation: dict[int, int] = {}

    def _get_auth(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, httpx.Response]:
        # Basic auth over HTTPS (consumer key/secret as username/password).
        return request_json(
            self._client,
            "woocommerce",
            "GET",
            self._api + path,
            headers={"Authorization": self._basic()},
            params=params,
        )

    def _request_auth(
        self, method: str, path: str, body: Any = None, params: dict[str, Any] | None = None
    ) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client,
            "woocommerce",
            method,
            self._api + path,
            headers={"Authorization": self._basic()},
            params=params,
            json_body=body,
        )

    def _check_currency(self, merchant_currency: str) -> None:
        body, _resp = self._get_auth("/data/currencies/current")
        code = (body or {}).get("code") or (body or {}).get("currency")
        if code and code != merchant_currency:
            raise currency_mismatch("woocommerce", code, merchant_currency)

    def _products(self) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """All products across pages: first page by params, then absolute
        rel="next" URLs. Terminates when no next link is served (a short
        page alone does NOT terminate — the cursor is authoritative)."""
        from openstore.surfaces.adapters.http import link_next

        products: list[dict[str, Any]] = []
        kinds: dict[int, str] = {}
        page = 1
        pending: tuple[str, dict[str, Any] | None] | None = (
            "/products",
            {"per_page": self._page_size, "page": 1, "status": "publish"},
        )
        while pending is not None:
            if page > self._max_pages:
                raise page_cap("woocommerce", self._max_pages)
            path, params = pending
            if path.startswith("http"):
                body, resp = request_json(
                    self._client, "woocommerce", "GET", path,
                    headers={"Authorization": self._basic()},
                )
            else:
                body, resp = self._get_auth(path, params)
            if not isinstance(body, list):
                from openstore.surfaces.adapters.errors import unreachable

                raise unreachable("woocommerce", "products response is not a list")
            if not body:
                break
            for product in body:
                pid = product.get("id")
                products.append(product)
                if isinstance(pid, int):
                    kinds[pid] = "product"
                if product.get("type") == "variable" and isinstance(pid, int):
                    for variation in self._variations(pid):
                        variation["_parent"] = product
                        products.append(variation)
                        vid = variation.get("id")
                        if isinstance(vid, int):
                            kinds[vid] = "variation"
                            self._parent_by_variation[vid] = pid
            nxt = link_next(resp.headers.get("Link", ""))
            pending = (nxt, None) if nxt else None
            page += 1
        return products, kinds

    def _basic(self) -> str:
        import base64 as _b64

        key, secret = self._auth
        return "Basic " + _b64.b64encode(f"{key}:{secret}".encode()).decode()

    def _variations(self, product_id: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > self._max_pages:
                raise page_cap("woocommerce", self._max_pages)
            body, _resp = self._get_auth(
                f"/products/{product_id}/variations",
                {"per_page": self._page_size, "page": page},
            )
            if not isinstance(body, list) or not body:
                break
            out.extend(body)
            if len(body) < self._page_size:
                break
            page += 1
        return out

    def _normalize(
        self, product: dict[str, Any], id_to_sku: dict[int, str]
    ) -> dict[str, Any] | None:
        sku = clean_sku(product.get("sku"))
        if not sku:
            return None  # skip-and-count (third-party data must not kill sync)
        raw_price = product.get("price") or product.get("regular_price")
        if raw_price in (None, ""):
            raise price_missing(sku, "woocommerce")
        try:
            unit_minor = price_to_minor(str(raw_price))
        except ValueError as e:
            raise price_invalid(sku, "woocommerce", raw_price) from e
        if unit_minor <= 0:
            raise price_invalid(sku, "woocommerce", raw_price)
        stock: int | None = None
        if product.get("manage_stock"):
            try:
                stock = parse_stock(product.get("stock_quantity", None))
            except ValueError as e:
                raise price_invalid(sku, "woocommerce", product.get("stock_quantity"), "stock") from e
        related_ids = list(product.get("cross_sell_ids", []) or []) + list(
            product.get("upsell_ids", []) or []
        )
        related = sorted({id_to_sku[i] for i in related_ids if i in id_to_sku})
        parent = product.get("_parent") or {}
        name = str(product.get("name") or parent.get("name") or sku)
        tags = normalize_tags([t.get("name", "") for t in product.get("tags", []) or []])
        description = strip_html(product.get("short_description") or product.get("description") or "")
        return normalized_item(
            sku=sku,
            name=name,
            unit_minor=unit_minor,
            tags=tags,
            related_skus=related,
            related_source="woocommerce",
            description=description,
            stock=stock,
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        from openstore.surfaces.adapters.errors import adapter_empty as _empty

        self._check_currency(self._merchant_currency)
        products, kinds = self._products()
        id_to_sku = {
            int(p["id"]): clean_sku(p.get("sku"))
            for p in products
            if isinstance(p.get("id"), int) and clean_sku(p.get("sku"))
        }
        items: list[dict[str, Any]] = []
        skipped = 0
        for product in products:
            item = self._normalize(product, id_to_sku)
            if item is None:
                skipped += 1
            else:
                items.append(item)
                pid = product.get("id")
                if isinstance(pid, int):
                    self._id_by_sku[item["sku"]] = pid
        self._kind_by_id = kinds
        if not items:
            raise _empty("woocommerce", f"zero usable products (skipped {skipped} without SKUs)")
        if skipped:
            logger.warning("woocommerce: skipped %d product(s) without SKUs", skipped)
        return items

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        return {
            sku: int(by_sku[sku]["stock"])
            for sku in skus
            if sku in by_sku and by_sku[sku].get("stock") is not None
        }

    def write_stock(self, quantities: dict[str, int]) -> None:
        """Absolute stock write-back (idempotent by construction)."""
        self.fetch_items()  # refresh sku -> id map
        for sku, qty in quantities.items():
            pid = self._id_by_sku.get(sku)
            if pid is None:
                raise price_missing(sku, "woocommerce")
            body: dict[str, Any] = {"manage_stock": True, "stock_quantity": int(qty)}
            if self._kind_by_id.get(pid) == "variation":
                parent = self._parent_by_variation.get(pid)
                if parent is None:
                    raise not_configured("woocommerce", f"variation {pid} parent resolution")
                self._request_auth("PUT", f"/products/{parent}/variations/{pid}", body)
            else:
                self._request_auth("PUT", f"/products/{pid}", body)

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        self._request_auth("PUT", f"/orders/{order.order_id}", {"status": order.status})

    def health_check(self) -> AdapterHealth:
        try:
            body, resp = self._get_auth("/products", {"per_page": 1})
            total = resp.headers.get("X-WP-Total")
            count = int(total) if total and total.isdigit() else (len(body) if isinstance(body, list) else 0)
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=count)


def _build_woo(config: Any, source: Any, client: httpx.Client | None) -> WooCommerceAdapter:
    currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return WooCommerceAdapter(source, client, merchant_currency=currency)


register_adapter("woocommerce", _build_woo)
