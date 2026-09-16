# OpenStore catalog adapter — Zoho Commerce (S25).
#
# OAuth refresh-token dance against the region data centre (dc is an
# explicit config field — .in/.com/.eu — never inferred); access tokens
# minted at runtime and cached to expiry, mirroring the Shopify token
# discipline. Capabilities: CATALOG_READ, STOCK_READ, STOCK_WRITE.
#
# PROVISIONAL (R0.7): endpoint shapes below are fixture-defined and have NOT
# been verified against a live Zoho store (region routing and field names
# vary). The adapter fails loud on shape deviation; live verification is
# tracked for the 25b hardening pass.

from __future__ import annotations

import logging
import time
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
    auth_failed,
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

logger = logging.getLogger("openstore.adapters.zoho")


class ZohoAdapter:
    name = "zoho"
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
        dc = str(getattr(source, "dc", "in") or "in").strip().lstrip(".")
        if dc not in ("in", "com", "eu"):
            raise not_configured("zoho", f"dc must be one of in/com/eu, got {dc!r}")
        for field in ("client_id", "client_secret", "refresh_token"):
            if not getattr(source, field, ""):
                raise not_configured("zoho", field)
        self._accounts = f"https://accounts.zoho.{dc}/oauth/v2/token"
        self._api = f"https://commerce.zoho.{dc}/store/v1"
        self._page_size = int(getattr(source, "page_size", 100) or 100)
        self._max_pages = int(getattr(source, "max_pages", 40) or 40)
        self._merchant_currency = merchant_currency
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._ids: dict[str, str] = {}

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        source = self._source
        try:
            resp = self._client.post(
                self._accounts,
                params={
                    "refresh_token": getattr(source, "refresh_token"),
                    "client_id": getattr(source, "client_id"),
                    "client_secret": getattr(source, "client_secret"),
                    "grant_type": "refresh_token",
                },
                timeout=30.0,
            )
        except httpx.HTTPError as e:
            from openstore.surfaces.adapters.errors import unreachable

            raise unreachable("zoho", f"token refresh: {type(e).__name__}: {e}") from e
        if resp.status_code in (401, 403):
            raise auth_failed("zoho", f"token refresh HTTP {resp.status_code}")
        if resp.status_code >= 400:
            from openstore.surfaces.adapters.errors import unreachable

            raise unreachable("zoho", f"token refresh HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            from openstore.surfaces.adapters.errors import unreachable

            raise unreachable("zoho", "token refresh returned invalid JSON") from e
        token = str(body.get("access_token") or "")
        if not token:
            raise auth_failed("zoho", "token refresh returned no access_token")
        self._token = token
        self._token_expires_at = time.monotonic() + int(body.get("expires_in", 3600))
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Zoho-oauthtoken {self._access_token()}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, httpx.Response]:
        org = getattr(self._source, "organization_id", None)
        merged = dict(params or {})
        if org:
            merged["organization_id"] = org
        return request_json(
            self._client, "zoho", "GET", self._api + path,
            headers=self._headers(), params=merged,
        )

    def _normalize(self, product: dict[str, Any]) -> dict[str, Any] | None:
        sku = clean_sku(product.get("sku") or product.get("product_sku"))
        if not sku:
            return None
        raw_price = product.get("price") or product.get("rate")
        if raw_price in (None, ""):
            raise price_missing(sku, "zoho")
        try:
            unit_minor = price_to_minor(str(raw_price))
        except ValueError as e:
            raise price_invalid(sku, "zoho", raw_price) from e
        if unit_minor <= 0:
            raise price_invalid(sku, "zoho", raw_price)
        stock: int | None = None
        if "stock" in product or "quantity" in product:
            try:
                stock = parse_stock(product.get("stock", product.get("quantity")))
            except ValueError as e:
                raise price_invalid(sku, "zoho", product.get("stock"), "stock") from e
        code = product.get("currency") or product.get("currency_code")
        if code and code != self._merchant_currency:
            raise currency_mismatch("zoho", code, self._merchant_currency)
        return normalized_item(
            sku=sku,
            name=str(product.get("name") or sku),
            unit_minor=unit_minor,
            tags=normalize_tags(product.get("tags") or product.get("categories")),
            related_skus=[],
            related_source="zoho",
            description=strip_html(product.get("description") or ""),
            stock=stock,
        )

    def fetch_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        skipped = 0
        page = 1
        while True:
            if page > self._max_pages:
                raise page_cap("zoho", self._max_pages)
            body, _resp = self._get(
                "/products", {"page": page, "per_page": self._page_size}
            )
            products = (body or {}).get("products", []) if isinstance(body, dict) else []
            if not products:
                break
            for product in products:
                item = self._normalize(product)
                if item is None:
                    skipped += 1
                    continue
                items.append(item)
                pid = product.get("product_id") or product.get("id")
                if pid is not None:
                    self._ids[item["sku"]] = str(pid)
            if len(products) < self._page_size:
                break
            page += 1
        if not items:
            raise adapter_empty("zoho", f"zero usable products (skipped {skipped})")
        if skipped:
            logger.warning("zoho: skipped %d product(s) without SKUs", skipped)
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
                raise price_missing(sku, "zoho")
            request_json(
                self._client, "zoho", "PUT", f"{self._api}/products/{pid}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json_body={"stock": int(qty)},
            )

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        raise not_configured("zoho", "order write-back (no order API in scope)")

    def health_check(self) -> AdapterHealth:
        try:
            body, _resp = self._get("/products", {"page": 1, "per_page": 1})
            products = (body or {}).get("products", []) if isinstance(body, dict) else []
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=len(products))


def _build_zoho(config: Any, source: Any, client: httpx.Client | None) -> ZohoAdapter:
    currency = getattr(getattr(config, "merchant", None), "currency", "INR") or "INR"
    return ZohoAdapter(source, client, merchant_currency=currency)


register_adapter("zoho", _build_zoho)
