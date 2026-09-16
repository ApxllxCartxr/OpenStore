# OpenStore catalog adapter SDK — errors (S25 / DECISION-046).
#
# AdapterError subclasses CommerceError so scripts/registry_diff.py actually
# scans the reason codes (bare RuntimeError/ValueError are invisible to it —
# that is how the old shopify_catalog.py raised, and why its failures were
# unregistered). Status convention: 502 for upstream transport failures
# (auth/unreachable/rate-limit — the platform is at fault), 422 for data and
# config errors the merchant must fix.

from __future__ import annotations

from openstore.core.api import CommerceError


class AdapterError(CommerceError):
    """Closed-set catalog failure. First arg is always a `catalog.*` code."""

    def __init__(self, reason_code: str, message: str, status_code: int = 422):
        super().__init__(reason_code, message, status_code)


def auth_failed(adapter: str, message: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_auth_failed", f"{adapter}: authentication failed: {message}", 502
    )


def unreachable(adapter: str, message: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_unreachable", f"{adapter}: unreachable: {message}", 502
    )


def rate_limited(adapter: str, message: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_rate_limited", f"{adapter}: rate limited: {message}", 502
    )


def currency_mismatch(adapter: str, got: str, want: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_currency_mismatch",
        f"{adapter}: shop currency {got!r} != merchant currency {want!r}: "
        "refusing to sync (converting money invents money)",
    )


def price_missing(sku: str, adapter: str) -> AdapterError:
    return AdapterError(
        "catalog.price_missing",
        f"{adapter}: item {sku!r} has no price field — "
        "a store that cannot state a price must not serve a catalog",
    )


def price_invalid(sku: str, adapter: str, raw: object, field: str = "price") -> AdapterError:
    # field covers "price" and "stock": both are merchant-stated numerics and
    # a wrong-typed either must fail loud naming the SKU (no 12th code spent
    # on the distinction; the message names the field).
    return AdapterError(
        "catalog.price_invalid",
        f"{adapter}: item {sku!r} has unparseable {field} {raw!r}",
    )


def sku_missing(adapter: str, note: str) -> AdapterError:
    return AdapterError("catalog.sku_missing", f"{adapter}: {note}")


def adapter_empty(adapter: str, note: str) -> AdapterError:
    return AdapterError("catalog.adapter_empty", f"{adapter}: empty catalog: {note}")


def page_cap(adapter: str, max_pages: int) -> AdapterError:
    return AdapterError(
        "catalog.adapter_page_cap",
        f"{adapter}: pagination exceeds {max_pages} pages — refusing an unbounded sync",
    )


def not_configured(adapter: str, what: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_not_configured", f"{adapter}: not configured for {what}"
    )


def multiple_sources(detail: str) -> AdapterError:
    return AdapterError(
        "catalog.adapter_multiple_sources",
        f"exactly one catalog source must be configured: {detail}",
    )
