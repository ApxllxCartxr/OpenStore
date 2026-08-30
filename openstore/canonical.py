"""Canonical JSON + digest (IMPLEMENTATION_SPEC §1.1).

Restricted profile: sorted keys, tight separators, NFC strings, integer-only
numbers. Not RFC 8785. Shared by the compiler, evidence builder, and the
standalone verifier so every party hashes byte-identically.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

FORBIDDEN_MODULES = (
    "time", "datetime", "random", "secrets", "uuid", "os",
)


class CanonicalError(ValueError):
    pass


def _validate(obj: Any) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int) or isinstance(obj, str) or obj is None:
        return
    if isinstance(obj, float):
        raise CanonicalError("noncanonical_type")
    if isinstance(obj, list):
        for v in obj:
            _validate(v)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or not _KEY_RE.match(k):
                raise CanonicalError("noncanonical_key")
            _validate(v)
        return
    raise CanonicalError("noncanonical_type")


def _normalize(obj: Any) -> Any:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    return obj


def canonical_json_bytes(obj: Any) -> bytes:
    _validate(obj)
    norm = _normalize(obj)
    return json.dumps(
        norm,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()
