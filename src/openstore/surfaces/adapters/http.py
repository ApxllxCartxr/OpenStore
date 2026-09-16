# OpenStore catalog adapter SDK — shared HTTP helper (S25).
#
# One error-mapping rule for all adapters: transport failure -> unreachable,
# 401/403 -> auth_failed, 429 -> rate_limited, other non-2xx -> unreachable
# with the status named (the platform refused; fail loud, never coerce an
# error page into items). Auth uses per-adapter headers/tokens supplied by
# the caller — this helper never mints credentials.

from __future__ import annotations

from typing import Any

import httpx

from openstore.surfaces.adapters.errors import (
    auth_failed,
    rate_limited,
    unreachable,
)


def request_json(
    client: httpx.Client,
    adapter: str,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> tuple[Any, httpx.Response]:
    """HTTP JSON round-trip with closed-set failure mapping. Returns
    (parsed_body, response) — callers needing headers (pagination Link,
    totals) read them off the response."""
    try:
        resp = client.request(
            method, url, headers=headers, params=params, json=json_body, timeout=30.0
        )
    except httpx.HTTPError as e:
        raise unreachable(adapter, f"{type(e).__name__}: {e}") from e
    if resp.status_code in (401, 403):
        raise auth_failed(adapter, f"HTTP {resp.status_code} for {method} {url}")
    if resp.status_code == 429:
        raise rate_limited(
            adapter, resp.headers.get("retry-after", f"HTTP 429 for {method} {url}")
        )
    if resp.status_code >= 400:
        raise unreachable(adapter, f"HTTP {resp.status_code} for {method} {url}")
    try:
        return resp.json(), resp
    except ValueError as e:
        raise unreachable(adapter, f"invalid JSON from {method} {url}") from e


def link_next(link_header: str) -> str | None:
    """Extract the rel="next" URL from an RFC 8288 Link header (or None)."""
    for part in (link_header or "").split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) == 2 and segments[1] == 'rel="next"':
            url = segments[0]
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None


def request_text(
    client: httpx.Client,
    adapter: str,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    try:
        resp = client.request(method, url, headers=headers, params=params, timeout=30.0)
    except httpx.HTTPError as e:
        raise unreachable(adapter, f"{type(e).__name__}: {e}") from e
    if resp.status_code in (401, 403):
        raise auth_failed(adapter, f"HTTP {resp.status_code} for {method} {url}")
    if resp.status_code == 429:
        raise rate_limited(adapter, f"HTTP 429 for {method} {url}")
    if resp.status_code >= 400:
        raise unreachable(adapter, f"HTTP {resp.status_code} for {method} {url}")
    return resp.text
