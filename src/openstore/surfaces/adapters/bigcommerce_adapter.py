# OpenStore catalog adapter — BigCommerce (S25).
#
# REST v3 with X-Auth-Token: catalog products (+variants include),
# inventory levels, native related_products. Capabilities: all five +
# VARIANTS. Currency gate: store profile currency vs merchant currency.

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
    adapter_empty,
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

logger = logging.getLogger("openstore.adapters.bigcommerce")


class BigCommerceAdapter:
    name = "bigcommerce"
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
        store_hash = str(getattr(source, "store_hash", "") or "")
        token = str(getattr(source, "access_token", "") or "")
        if not store_hash or not token:
            raise not_configured("bigcommerce", "store_hash/access_token")
        self._api = f"https://api.bigcommerce.com/stores/{store_hash}/v3"
        self._headers = {"X-Auth-Token": token, "Accept": "application/json"}
        self._page_size = int(getattr(source, "page_size", 250) or 250)
        self._max_pages = int(getattr(source, "max_pages", 40) or 40)
        self._merchant_currency = merchant_currency
        self._ids: dict[str, tuple[int, int | None]] = {}  # sku -> (product_id, variant_id?)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client, "bigcommerce", "GET", self._api + path,
            headers=self._headers, params=params,
        )

    def _mutate(self, method: str, path: str, body: Any) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client, "bigcommerce", method, self._api + path,
            headers={**self._headers, "Content-Type": "application/json"},
            json_body=body,
        )

    def _check_currency(self) -> None:
        body, _resp = self._get("/store/profile")
        data = (body or {}).get("data", {}) if isinstance(body, dict) else {}
        code = data.get("currency_code")
        if code and code != self._merchant_currency:
            raise currency_mismatch("bigcommerce", code, self._merchant_currency)

    def _pages(self, path: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > self._max_pages:
                raise page_cap("bigcommerce", self._max_pages)
            params = {"limit": self._page_size, "page": page, "include": "variants"}
            if extra:
                params.update(extra)
            body, _resp = self._get(path, params)
            data = (body or {}).get("data", []) if isinstance(body, dict) else []
            if not data:
                break
            out.extend(data)
            meta = (body or {}).get("meta", {}).get("pagination", {})
            if not meta or page >= int(meta.get("total_pages", page)):
                break
            page += 1
        return out

    def _normalize(
        self, product: dict[str, Any], variant: dict[str, Any] | None, id_to_sku: dict[int, str]
    ) -> dict[str, Any] | None:
        row = variant or product
        sku = clean_sku(row.get("sku"))
        if not sku:
            return None
        raw_price = row.get("price") or product.get("price")
        if raw_price in (None, ""):
            raise price_missing(sku, "bigcommerce")
        try:
            unit_minor = price_to_minor(str(raw_price))
        except ValueError as e:
            raise price_invalid(sku, "bigcommerce", raw_price) from e
        if unit_minor <= 0:
            raise price_invalid(sku, "bigcommerce", raw_price)
        stock: int | None = None
        level = (variant or {}).get("inventory_level", product.get("inventory_level", None))
        if product.get("inventory_tracking") not in (None, "none"):
            try:
                stock = parse_stock(level)
            except ValueError as e:
                raise price_invalid(sku, "bigcommerce", level, "stock") from e
        related = sorted(
            {id_to_sku[i] for i in (product.get("related_products") or []) if i in id_to_sku}
        )
        name = str(product.get("name") or sku)
        if variant and variant.get("label"):
            name = f"{name} — {variant['label']}"
        return normalized_item(
            sku=sku,
            name=name,
            unit_minor=unit_minor,
            tags=normalize_tags(product.get("categories") or []),
            related_skus=related,
            related_source="bigcommerce",
            description=strip_html(product.get("description") or ""),
            stock=stock,
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        self._check_currency()
        products = self._pages("/catalog/products")
        id_to_sku: dict[int, str] = {}
        for product in products:
            pid = product.get("id")
            if isinstance(pid, int) and clean_sku(product.get("sku")):
                id_to_sku[pid] = clean_sku(product.get("sku"))
            for variant in product.get("variants") or []:
                vid = variant.get("id")
                if isinstance(vid, int) and clean_sku(variant.get("sku")):
                    id_to_sku[vid] = clean_sku(variant.get("sku"))
        items: list[dict[str, Any]] = []
        skipped = 0
        for product in products:
            variants = product.get("variants") or []
            rows = variants if variants else [None]
            for variant in rows:
                item = self._normalize(product, variant, id_to_sku)
                if item is None:
                    skipped += 1
                    continue
                items.append(item)
                pid = product.get("id")
                vid = (variant or {}).get("id") if variant else None
                if isinstance(pid, int):
                    self._ids[item["sku"]] = (pid, vid if isinstance(vid, int) else None)
        if not items:
            raise adapter_empty("bigcommerce", f"zero usable products (skipped {skipped})")
        if skipped:
            logger.warning("bigcommerce: skipped %d variant(s) without SKUs", skipped)
        return items

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        return {
            sku: int(by_sku[sku]["stock"])
            for sku in skus
            if sku in by_sku and by_sku[sku].get("stock") is not None
        }

    def write_stock(self, quantities: dict[str, int]) -> None:
        self.fetch_items()
        for sku, qty in quantities.items():
            ids = self._ids.get(sku)
            if ids is None:
                raise price_missing(sku, "bigcommerce")
            pid, vid = ids
            if vid is not None:
                self._mutate("PUT", f"/catalog/products/{pid}/variants/{vid}", {"inventory_level": int(qty)})
            else:
                self._mutate("PUT", f"/catalog/products/{pid}", {"inventory_level": int(qty)})

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        # Order management stayed on v2 when BigCommerce introduced v3
        # catalog endpoints — self._api ends in /v3, so v2 needs its own
        # base rather than a path appended onto self._api (that produced a
        # malformed /v3/v2/orders/... URL, 404-ing on every real store).
        v2_base = self._api.removesuffix("/v3") + "/v2"
        request_json(
            self._client, "bigcommerce", "PUT", f"{v2_base}/orders/{order.order_id}",
            headers={**self._headers, "Content-Type": "application/json"},
            json_body={"status": order.status},
        )

    def health_check(self) -> AdapterHealth:
        try:
            body, _resp = self._get("/catalog/products", {"limit": 1})
            data = (body or {}).get("data", [])
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=len(data))


def _build_bc(config: Any, source: Any, client: httpx.Client | None) -> BigCommerceAdapter:
    currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return BigCommerceAdapter(source, client, merchant_currency=currency)


register_adapter("bigcommerce", _build_bc)
