# OpenStore catalog adapter — Magento 2 (S25).
#
# Integration-token auth; searchCriteria paging (fiddly by reputation, hence
# the explicit page cap); stockItem for inventory; native related links
# (GET /V1/products/:sku/links/related). Capabilities: all five + VARIANTS
# (configurables explode into simple-product rows).

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

logger = logging.getLogger("openstore.adapters.magento")


class MagentoAdapter:
    name = "magento"
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
        token = str(getattr(source, "access_token", "") or "")
        if not base or not token:
            raise not_configured("magento", "base_url/access_token")
        self._api = base + "/rest/V1"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._page_size = int(getattr(source, "page_size", 100) or 100)
        self._max_pages = int(getattr(source, "max_pages", 40) or 40)
        self._merchant_currency = merchant_currency

    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client, "magento", "GET", self._api + path,
            headers=self._headers, params=params,
        )

    def _mutate(self, method: str, path: str, body: Any) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client, "magento", method, self._api + path,
            headers={**self._headers, "Content-Type": "application/json"},
            json_body=body,
        )

    def _check_currency(self) -> None:
        body, _resp = self._get("/store/storeViews/default")
        code = (body or {}).get("base_currency_code") if isinstance(body, dict) else None
        if code and code != self._merchant_currency:
            raise currency_mismatch("magento", code, self._merchant_currency)

    def _products(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > self._max_pages:
                raise page_cap("magento", self._max_pages)
            body, _resp = self._get(
                "/products",
                {
                    "searchCriteria[pageSize]": self._page_size,
                    "searchCriteria[currentPage]": page,
                },
            )
            items = (body or {}).get("items", []) if isinstance(body, dict) else []
            if not items:
                break
            out.extend(items)
            total = (body or {}).get("total_count", len(out))
            if len(out) >= int(total or 0):
                break
            page += 1
        return out

    def _related(self, sku: str) -> list[str]:
        body, _resp = self._get(f"/products/{sku}/links/related")
        if not isinstance(body, list):
            return []
        return sorted({str(link.get("linked_product_sku", "")) for link in body if link.get("linked_product_sku")})

    def _normalize(self, product: dict[str, Any]) -> dict[str, Any] | None:
        sku = clean_sku(product.get("sku"))
        if not sku:
            return None
        raw_price = product.get("price")
        if raw_price in (None, ""):
            raise price_missing(sku, "magento")
        try:
            unit_minor = price_to_minor(str(raw_price))
        except ValueError as e:
            raise price_invalid(sku, "magento", raw_price) from e
        if unit_minor <= 0:
            raise price_invalid(sku, "magento", raw_price)
        stock: int | None = None
        stock_item = ((product.get("extension_attributes") or {}).get("stock_item")) or {}
        if stock_item.get("manage_stock"):
            try:
                stock = parse_stock(stock_item.get("qty", None))
            except ValueError as e:
                raise price_invalid(sku, "magento", stock_item.get("qty"), "stock") from e
        tags = normalize_tags(
            [a.get("value", "") for a in (product.get("custom_attributes") or []) if a.get("attribute_code") == "category_ids"]
            + str(product.get("type_id") or "").split()
        )
        description = ""
        for attr in product.get("custom_attributes") or []:
            if attr.get("attribute_code") in ("description", "short_description") and attr.get("value"):
                description = strip_html(attr["value"])
                break
        try:
            related = self._related(sku)
        except AdapterError:
            related = []  # links endpoint missing/blocked: degrade, don't fail sync
        return normalized_item(
            sku=sku,
            name=str(product.get("name") or sku),
            unit_minor=unit_minor,
            tags=tags,
            related_skus=related,
            related_source="magento",
            description=description,
            stock=stock,
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        self._check_currency()
        items: list[dict[str, Any]] = []
        skipped = 0
        for product in self._products():
            item = self._normalize(product)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        if not items:
            raise adapter_empty("magento", f"zero usable products (skipped {skipped})")
        if skipped:
            logger.warning("magento: skipped %d product(s) without SKUs", skipped)
        return items

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        return {
            sku: int(by_sku[sku]["stock"])
            for sku in skus
            if sku in by_sku and by_sku[sku].get("stock") is not None
        }

    def write_stock(self, quantities: dict[str, int]) -> None:
        for sku, qty in quantities.items():
            self._mutate(
                "PUT",
                f"/products/{sku}/stockItems/1",
                {"stockItem": {"qty": int(qty), "is_in_stock": bool(int(qty) > 0)}},
            )

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        # Magento order status transitions go through comments/invoices in
        # full; the adapter records a visible status comment (bounded scope).
        self._mutate(
            "POST",
            f"/orders/{order.order_id}/comments",
            {"statusHistory": {"comment": f"OpenStore: {order.status}", "is_visible_on_front": 0}},
        )

    def health_check(self) -> AdapterHealth:
        try:
            body, _resp = self._get(
                "/products", {"searchCriteria[pageSize]": 1, "searchCriteria[currentPage]": 1}
            )
            count = int((body or {}).get("total_count", 0)) if isinstance(body, dict) else 0
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=count)


def _build_magento(config: Any, source: Any, client: httpx.Client | None) -> MagentoAdapter:
    currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return MagentoAdapter(source, client, merchant_currency=currency)


register_adapter("magento", _build_magento)
