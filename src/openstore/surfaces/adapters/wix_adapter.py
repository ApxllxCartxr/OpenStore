# OpenStore catalog adapter — Wix (S25).
#
# OAuth app-instance tokens (browser install flow in /merchant/setup — a
# pasted key alone cannot provision it); Stores Catalog V3 for products.
# No usable related-products concept -> the Stage-27 rules table supplies
# it. Capabilities: CATALOG_READ, STOCK_READ, STOCK_WRITE.
#
# PROVISIONAL (R0.7): the inventory write shape below is fixture-defined and
# has NOT been verified against a live Wix store. The adapter fails loud on
# any shape deviation rather than guessing, and live verification is tracked
# for the 25b hardening pass.

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

logger = logging.getLogger("openstore.adapters.wix")


class WixAdapter:
    name = "wix"
    capabilities = frozenset(
        {
            AdapterCapability.CATALOG_READ,
            AdapterCapability.STOCK_READ,
            AdapterCapability.STOCK_WRITE,
        }
    )

    def __init__(
        self, source: Any, client: httpx.Client | None = None, merchant_currency: str = "INR"
    ):
        self._source = source
        self._client = client or httpx.Client()
        token = str(getattr(source, "instance_token", "") or "")
        if not token:
            raise not_configured("wix", "instance_token (browser OAuth install flow)")
        self._headers = {"Authorization": token}
        self._page_size = int(getattr(source, "page_size", 100) or 100)
        self._max_pages = int(getattr(source, "max_pages", 40) or 40)
        self._merchant_currency = merchant_currency
        self._ids: dict[str, str] = {}  # sku -> wix product id

    def _query(self, offset: int) -> tuple[Any, httpx.Response]:
        return request_json(
            self._client, "wix", "POST", "https://www.wixapis.com/stores/v1/products/query",
            headers=self._headers,
            json_body={"query": {"paging": {"limit": self._page_size, "offset": offset}}},
        )

    def _check_currency(self, products: list[dict[str, Any]]) -> None:
        for product in products:
            code = ((product.get("priceData") or {}).get("currency") if isinstance(product.get("priceData"), dict) else None)
            if code and code != self._merchant_currency:
                raise currency_mismatch("wix", code, self._merchant_currency)
            break  # store-level currency: first product decides

    def _normalize(self, product: dict[str, Any]) -> dict[str, Any] | None:
        sku = clean_sku(product.get("sku"))
        if not sku:
            return None
        price_data = product.get("priceData") or {}
        raw_price = price_data.get("price")
        if raw_price in (None, ""):
            raise price_missing(sku, "wix")
        try:
            unit_minor = price_to_minor(str(raw_price))
        except ValueError as e:
            raise price_invalid(sku, "wix", raw_price) from e
        if unit_minor <= 0:
            raise price_invalid(sku, "wix", raw_price)
        stock: int | None = None
        inventory = product.get("stock") or {}
        if isinstance(inventory, dict) and inventory.get("trackInventory"):
            try:
                stock = parse_stock(inventory.get("quantity", None))
            except ValueError as e:
                raise price_invalid(sku, "wix", inventory.get("quantity"), "stock") from e
        return normalized_item(
            sku=sku,
            name=str(product.get("name") or sku),
            unit_minor=unit_minor,
            tags=normalize_tags(
                [(c.get("name", "") if isinstance(c, dict) else str(c)) for c in (product.get("collections") or [])]
            ),
            related_skus=[],
            related_source="wix",
            description=strip_html(product.get("description") or ""),
            stock=stock,
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        skipped = 0
        offset = 0
        pages = 0
        while True:
            if pages >= self._max_pages:
                raise page_cap("wix", self._max_pages)
            body, _resp = self._query(offset)
            products = (body or {}).get("products", []) if isinstance(body, dict) else []
            if pages == 0 and products:
                self._check_currency(products)
            if not products:
                break
            for product in products:
                item = self._normalize(product)
                if item is None:
                    skipped += 1
                    continue
                items.append(item)
                pid = product.get("id")
                if isinstance(pid, str):
                    self._ids[item["sku"]] = pid
            if len(products) < self._page_size:
                break
            offset += self._page_size
            pages += 1
        if not items:
            raise adapter_empty("wix", f"zero usable products (skipped {skipped})")
        if skipped:
            logger.warning("wix: skipped %d product(s) without SKUs", skipped)
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
            pid = self._ids.get(sku)
            if pid is None:
                raise price_missing(sku, "wix")
            request_json(
                self._client, "wix", "POST",
                "https://www.wixapis.com/stores/v2/inventoryItems/update",
                headers=self._headers,
                json_body={"productId": pid, "quantity": int(qty)},
            )

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        raise not_configured("wix", "order write-back (no order API in scope)")

    def health_check(self) -> AdapterHealth:
        try:
            body, _resp = self._query(0)
            products = (body or {}).get("products", []) if isinstance(body, dict) else []
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=len(products))


def _build_wix(config: Any, source: Any, client: httpx.Client | None) -> WixAdapter:
    currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return WixAdapter(source, client, merchant_currency=currency)


register_adapter("wix", _build_wix)
