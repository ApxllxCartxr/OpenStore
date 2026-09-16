# OpenStore catalog adapter — CSV / Google Sheets (S25).
#
# Highest reach for Indian SMBs on Dukaan/Instamojo/WhatsApp-only. No OAuth:
# a local file (csv) or a published-CSV URL (sheets, optional gid). Column
# mapping lives on the source config (explicit mapping UI in /merchant/setup,
# Stage 25). Capabilities: CATALOG_READ, STOCK_READ, RELATED_SKUS.

from __future__ import annotations

import csv
import io
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
    not_configured,
    price_invalid,
    price_missing,
    sku_missing,
)
from openstore.surfaces.adapters.http import request_text
from openstore.surfaces.adapters.normalize import (
    clean_sku,
    normalize_tags,
    normalized_item,
    parse_stock,
    price_to_minor,
)
from openstore.surfaces.adapters.registry import register_adapter

logger = logging.getLogger("openstore.adapters.csv")


def _columns(source: Any) -> dict[str, str]:
    def _get(name: str, default: str) -> str:
        value = getattr(source, name, None)
        return str(value) if value else default

    return {
        "sku": _get("sku_column", "sku"),
        "name": _get("name_column", "name"),
        "price": _get("price_column", "price"),
        "tags": _get("tags_column", "tags"),
        "stock": _get("stock_column", "stock"),
        "related": _get("related_column", "related_skus"),
        "description": _get("description_column", "description"),
    }


def _split_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if isinstance(raw, list | tuple):
        return [str(p).strip() for p in raw if str(p).strip()]
    return []


class _RowReader:
    """Typed cell accessor over a csv.DictReader row."""

    def __init__(self, row: dict[str, str | None]):
        self._row = row

    def get(self, column: str) -> str:
        return str(self._row.get(column) or "")


class CsvAdapter:
    """CSV file or published-CSV-URL catalog (covers `csv` + `sheets`)."""

    name = "csv"
    capabilities = frozenset(
        {
            AdapterCapability.CATALOG_READ,
            AdapterCapability.STOCK_READ,
            AdapterCapability.RELATED_SKUS,
        }
    )

    def __init__(self, source: Any, client: httpx.Client | None = None):
        self._source = source
        self._client = client or httpx.Client()
        kind = getattr(source, "type", "csv")
        self.name = "sheets" if kind == "sheets" else "csv"

    def _read_text(self) -> str:
        source = self._source
        path = getattr(source, "path", None)
        url = getattr(source, "url", None)
        if path:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    return f.read()
            except OSError as e:
                raise AdapterError(
                    "catalog.adapter_unreachable", f"csv: cannot read {path}: {e}", 502
                ) from e
        if url:
            gid = getattr(source, "gid", None)
            if gid and "gid=" not in url:
                url = url + ("&gid=" if "?" in url else "?gid=") + gid
            return request_text(self._client, self.name, "GET", url)
        raise not_configured(self.name, "csv needs a file path or a published-CSV url")

    def fetch_items(self) -> list[dict[str, Any]]:
        cols = _columns(self._source)
        text = self._read_text()
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise adapter_empty(self.name, "no data rows")
        items: list[dict[str, Any]] = []
        for lineno, row in enumerate(rows, start=2):
            get = _RowReader(row).get
            sku = clean_sku(get(cols["sku"]))
            if not sku:
                raise sku_missing(self.name, f"row {lineno} has no sku")
            raw_price = get(cols["price"])
            if raw_price == "":
                raise price_missing(sku, self.name)
            try:
                unit_minor = price_to_minor(raw_price)
            except ValueError as e:
                raise price_invalid(sku, self.name, raw_price) from e
            if unit_minor <= 0:
                raise price_invalid(sku, self.name, raw_price)
            try:
                stock = parse_stock(get(cols["stock"]) or None)
            except ValueError as e:
                raise price_invalid(sku, self.name, get(cols["stock"]), "stock") from e
            related = _split_list(row.get(cols["related"]))
            items.append(
                normalized_item(
                    sku=sku,
                    name=get(cols["name"]) or sku,
                    unit_minor=unit_minor,
                    tags=normalize_tags(_split_list(row.get(cols["tags"]))),
                    related_skus=related,
                    related_source=self.name,
                    description=get(cols["description"]),
                    stock=stock,
                )
            )
        if not items:
            raise adapter_empty(self.name, "zero usable rows")
        return items

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        return {
            sku: int(by_sku[sku]["stock"])
            for sku in skus
            if sku in by_sku and by_sku[sku].get("stock") is not None
        }

    def write_stock(self, deltas: dict[str, int]) -> None:
        raise not_configured(self.name, "stock write-back (flat file / published URL)")

    def push_order_status(self, order: OrderStatusUpdate) -> None:
        raise not_configured(self.name, "order write-back (no order API)")

    def health_check(self) -> AdapterHealth:
        try:
            items = self.fetch_items()
        except AdapterError as e:
            return AdapterHealth(ok=False, detail=f"{e.reason_code}: {e.message}")
        return AdapterHealth(ok=True, item_count=len(items))


def _build_csv(config: Any, source: Any, client: httpx.Client | None) -> CsvAdapter:
    return CsvAdapter(source, client)


register_adapter("csv", _build_csv)
register_adapter("sheets", _build_csv)
