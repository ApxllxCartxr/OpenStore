# OpenStore catalog adapter SDK — one cache for all adapters (S25).
#
# Replaces today's two unrelated models: the mtime-keyed _CATALOG_BY_PATH in
# catalog.py and the module-global TTL dict keyed by store_domain in
# shopify_catalog.py. TTL + optional ETag (conditional refresh where the
# platform supports it); monotonic clock so wall jumps never resurrect
# stale entries. Dict-compatible surface (clear/pop) so the legacy
# module-level cache names keep working during migration.

from __future__ import annotations

import time
from typing import Any


class AdapterCache:
    """TTL key/value cache with optional ETag retention."""

    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, Any, str | None]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value, _etag = entry
        if time.monotonic() - stored_at >= self._ttl:
            del self._entries[key]
            return None
        return value

    def etag(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, _value, etag = entry
        if time.monotonic() - stored_at >= self._ttl:
            return None
        return etag

    def set(self, key: str, value: Any, etag: str | None = None) -> None:
        self._entries[key] = (time.monotonic(), value, etag)

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    # Legacy dict-compatible surface (migration shims, not new API).
    def clear(self) -> None:
        self._entries.clear()

    def pop(self, key: str, default: Any = None) -> Any:
        entry = self._entries.pop(key, None)
        return entry[1] if entry is not None else default

    def __len__(self) -> int:
        return len(self._entries)
