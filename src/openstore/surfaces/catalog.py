# OpenStore surfaces — Catalog feed (S6.5)
# Per PRD §3.8: catalog items per §3.8, sorted tags, integer unit_minor,
# ES256 catalog attestation per payload in §3.8.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openstore.config import Settings

CATALOG_CACHE: list[dict[str, Any]] | None = None


def load_catalog(config: Settings) -> list[dict[str, Any]]:
    """Load catalog from config file (catalog_path). Cached in memory."""
    global CATALOG_CACHE
    if CATALOG_CACHE is not None:
        return CATALOG_CACHE

    catalog_path = getattr(config, "catalog_path", None)
    if not catalog_path:
        return []

    path = Path(catalog_path)
    if not path.exists():
        return []

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    if isinstance(data, list):
        items = data
    else:
        items = data.get("items", [])
    normalized = []
    for item in items:
        normalized.append(
            {
                "sku": str(item["sku"]),
                "name": str(item.get("name", item["sku"])),
                "unit_minor": int(item.get("unit_minor", item.get("price_minor", 0))),
                "tags": sorted([str(t) for t in item.get("tags", [])]),
                "related_skus": [str(s) for s in item.get("related_skus", [])],
                "description": str(item.get("description", "")),
                "offers": item.get("offers", []),
            }
        )
    CATALOG_CACHE = normalized
    return normalized


def search_catalog_items(
    config: Settings,
    query: str,
    tags: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search catalog items by name/description and tags."""
    items = load_catalog(config)
    q = query.lower()
    results = []
    for item in items:
        if q and q not in item["name"].lower() and q not in item.get("description", "").lower():
            continue
        if tags:
            item_tags = set(item["tags"])
            if not item_tags.intersection(set(tags)):
                continue
        results.append(item)
        if len(results) >= limit:
            break
    return results


def suggest_related_items(
    config: Settings, cart_skus: list[str], limit: int = 2
) -> list[dict[str, Any]]:
    """S14: deterministic cross-sell — no LLM call, just a catalog lookup over
    each cart item's related_skus. Never touches cart/money state (display
    only); candidates already in the cart are excluded. Order is stable
    (first cart item's related_skus first, then the next's, deduped)."""
    items = load_catalog(config)
    by_sku = {item["sku"]: item for item in items}
    cart_set = set(cart_skus)

    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sku in cart_skus:
        item = by_sku.get(sku)
        if not item:
            continue
        for related_sku in item.get("related_skus", []):
            if related_sku in cart_set or related_sku in seen:
                continue
            related_item = by_sku.get(related_sku)
            if not related_item:
                continue
            suggestions.append(related_item)
            seen.add(related_sku)
            if len(suggestions) >= limit:
                return suggestions
    return suggestions


def get_catalog_item(config: Settings, sku: str) -> dict[str, Any] | None:
    """Get a single catalog item by SKU."""
    items = load_catalog(config)
    for item in items:
        if item["sku"] == sku:
            return item
    return None


def compute_catalog_digest(config: Settings) -> str:
    """Compute SHA-256 digest over the normalized catalog."""
    import hashlib

    items = load_catalog(config)
    normalized = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def build_catalog_attestation(
    sku: str,
    price_minor: int,
    tags: list[str],
    catalog_digest: str,
    merchant_id: str,
    iat_unix: int,
    private_key_pem: bytes,
) -> str:
    """Build ES256 JWS Compact catalog attestation per §3.8."""
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    payload = {
        "sku": sku,
        "price_minor": price_minor,
        "tags": sorted(tags),
        "catalog_digest": catalog_digest,
        "merchant_id": merchant_id,
        "iat": iat_unix,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    key = serialization.load_der_private_key(private_key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("Catalog attestation requires EC P-256 private key")
    if key.curve.name != "secp256r1":
        raise ValueError("Catalog attestation requires EC P-256 (secp256r1)")

    kid = key.public_key().public_numbers().x.to_bytes(32, "big").hex()[:8]
    header = json.dumps(
        {"alg": "ES256", "kid": f"openstore-key-{kid}", "typ": "JWT"}, sort_keys=True
    ).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    header_b64 = base64.urlsafe_b64encode(header).decode().rstrip("=")

    signing_input = f"{header_b64}.{payload_b64}"
    der_sig = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = base64.urlsafe_b64encode(raw_sig).decode().rstrip("=")
    return f"{signing_input}.{sig_b64}"


def serve_catalog_feed(
    config: Settings, merchant_id: str, private_key_pem: bytes | None = None
) -> dict[str, Any]:
    """Serve the catalog feed with attestations (S6.5)."""
    items = load_catalog(config)
    catalog_digest = compute_catalog_digest(config)
    import time

    iat = int(time.time())

    served_items = []
    for item in items:
        attestation = None
        if private_key_pem:
            try:
                attestation = build_catalog_attestation(
                    sku=item["sku"],
                    price_minor=item["unit_minor"],
                    tags=item["tags"],
                    catalog_digest=catalog_digest,
                    merchant_id=merchant_id,
                    iat_unix=iat,
                    private_key_pem=private_key_pem,
                )
            except Exception:
                pass

        served_items.append(
            {
                **item,
                "catalog_attestation": attestation,
            }
        )

    return {
        "merchant_id": merchant_id,
        "catalog_digest": catalog_digest,
        "currency": "INR",
        "items": served_items,
    }
