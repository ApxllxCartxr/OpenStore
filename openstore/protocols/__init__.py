"""Adapter registry + discovery (INTEROP_SPEC §5, §6).

`protocols_doc()` returns the `protocols[]` array for `/.well-known/agent-commerce.json`.
A protocol is listed ONLY if its `SPEC_EXCERPT.md` exists (R0.I1/R5.1b) and its
`conformance.conformance_pass()` returns True. The array is generated from this
registry so it cannot drift from the code (R5.1b).
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass(frozen=True, slots=True)
class ProtocolMeta:
    name: str
    version: str
    endpoint: str
    auth: Tuple[str, ...]
    authority_schemes: Tuple[str, ...]
    adapter_module: str
    conformance_module: str
    excerpt_rel: str          # path relative to repo root


PROTOCOLS: dict[str, ProtocolMeta] = {
    "mcp": ProtocolMeta(
        name="mcp", version="2025-06-18", endpoint="/agent/mcp",
        auth=("oauth2_bearer",), authority_schemes=("native_webauthn",),
        adapter_module="openstore.protocols.mcp.adapter",
        conformance_module="openstore.protocols.mcp.conformance",
        excerpt_rel="openstore/protocols/mcp/SPEC_EXCERPT.md",
    ),
    "acp": ProtocolMeta(
        name="acp", version="1.0", endpoint="/agent/acp",
        auth=("oauth2_bearer", "http_message_signature"),
        authority_schemes=("acp_delegated_token", "native_webauthn"),
        adapter_module="openstore.protocols.acp.adapter",
        conformance_module="openstore.protocols.acp.conformance",
        excerpt_rel="openstore/protocols/acp/SPEC_EXCERPT.md",
    ),
    "ap2": ProtocolMeta(
        name="ap2", version="0.2", endpoint="/agent/ap2",
        auth=("oauth2_bearer",),
        authority_schemes=("ap2_intent_mandate", "ap2_cart_mandate"),
        adapter_module="openstore.protocols.ap2.adapter",
        conformance_module="openstore.protocols.ap2.conformance",
        excerpt_rel="openstore/protocols/ap2/SPEC_EXCERPT.md",
    ),
    "a2a": ProtocolMeta(
        name="a2a", version="1.0.0", endpoint="/.well-known/agent.json",
        auth=("oauth2_bearer",),
        authority_schemes=(),
        adapter_module="openstore.protocols.a2a",
        conformance_module="openstore.protocols.a2a.conformance",
        excerpt_rel="openstore/protocols/a2a/SPEC_EXCERPT.md",
    ),
}


def excerpt_path(name: str) -> str:
    return os.path.join(_REPO_ROOT, PROTOCOLS[name].excerpt_rel)


def has_spec_excerpt(name: str) -> bool:
    path = excerpt_path(name)
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # R0.I1 — must carry a source URL and an ISO date.
    if "http" not in text:
        return False
    if "20" not in text or "-" not in text:  # crude ISO-date presence check
        return False
    return True


def _conformance_ok(name: str) -> bool:
    try:
        mod = importlib.import_module(PROTOCOLS[name].conformance_module)
        return bool(getattr(mod, "conformance_pass")())
    except Exception:
        return False


def protocols_doc() -> list[dict]:
    out = []
    for name, meta in PROTOCOLS.items():
        if not has_spec_excerpt(name):
            continue
        if not _conformance_ok(name):
            continue
        out.append({
            "name": meta.name,
            "version": meta.version,
            "endpoint": meta.endpoint,
            "auth": list(meta.auth),
            "authority_schemes": list(meta.authority_schemes),
            "spec_excerpt": f"/protocols/{meta.name}/spec-excerpt",
        })
    return out


def read_excerpt(name: str) -> str:
    with open(excerpt_path(name), encoding="utf-8") as fh:
        return fh.read()
