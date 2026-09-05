# OpenStore surfaces — Catalog feed (S6.5)
# Per PRD §3.8: catalog items per §3.8, sorted tags, integer unit_minor,
# ES256 catalog attestation per payload in §3.8.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openstore.config import Settings

# Legacy single-slot cache. Tests reach in and set this to None to force a
# reload, so it stays as the invalidation signal, but the real cache below is
# keyed by (path, mtime): the single slot was shared across every config in a
# process, so two merchant configs served each other's catalog, and an edited
# catalog.yaml needed a restart to take effect.
CATALOG_CACHE: list[dict[str, Any]] | None = None

_CATALOG_BY_PATH: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def load_catalog(config: Settings) -> list[dict[str, Any]]:
    """Load the catalog named by config.catalog_path, cached per path and
    invalidated when the file's mtime changes."""
    global CATALOG_CACHE

    catalog_path = getattr(config, "catalog_path", None)
    if not catalog_path:
        return []

    path = Path(catalog_path)
    if not path.exists():
        return []

    key = str(path.resolve())
    mtime = path.stat().st_mtime
    if CATALOG_CACHE is not None:
        cached = _CATALOG_BY_PATH.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

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
    _CATALOG_BY_PATH[key] = (mtime, normalized)
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


def active_offers_by_sku(config: Settings) -> dict[str, list[dict[str, Any]]]:
    """PRD §9.2 stage 5: publish approved campaigns 'to catalog item offers[]'.

    The catalog's `offers` field has been a dead passthrough since S6.5 — it was
    normalized on load and never written by anything. This projects the same
    ACTIVE-and-in-window set the signed feed serves (INV-13), reusing that
    query so the two can never disagree. It is a convenience view: the campaign
    signature on /.well-known/agent-campaigns.json remains the authority, and
    the compiler still re-checks validity at check 12 (R0.8 applied to
    marketing — offer text is never trusted at face value).
    """
    from openstore.core.database import get_session

    session = get_session(config)
    try:
        campaigns = _active_in_window_campaigns(session)
    finally:
        session.close()

    by_sku: dict[str, list[dict[str, Any]]] = {}
    for campaign in campaigns:
        for sku in campaign.applies_to_skus:
            by_sku.setdefault(sku, []).append(
                {
                    "campaign_id": campaign.id,
                    "title": campaign.title,
                    "discount_bps": campaign.discount_bps,
                    "starts_at": campaign.starts_at.isoformat() + "Z",
                    "ends_at": campaign.ends_at.isoformat() + "Z",
                }
            )
    return by_sku


def _active_in_window_campaigns(session: Any) -> list[Any]:
    from datetime import UTC, datetime

    from sqlmodel import select

    from openstore.models import Campaign, CampaignState

    now = datetime.now(UTC).replace(tzinfo=None)
    return [
        c
        for c in session.exec(select(Campaign).where(Campaign.state == CampaignState.ACTIVE)).all()
        if c.starts_at <= now < c.ends_at
    ]


def serve_catalog_feed(
    config: Settings, merchant_id: str, private_key_pem: bytes | None = None
) -> dict[str, Any]:
    """Serve the catalog feed with attestations (S6.5) and live offers (§9.2)."""
    items = load_catalog(config)
    catalog_digest = compute_catalog_digest(config)
    offers = active_offers_by_sku(config)
    import time

    iat = int(time.time())

    served_items = []
    for item in items:
        attestation = None
        if private_key_pem:
            # No bare except here: a signing failure used to be swallowed and the
            # item served unsigned, which is indistinguishable from "this merchant
            # has no key" to a reading agent (R0.5).
            attestation = build_catalog_attestation(
                sku=item["sku"],
                price_minor=item["unit_minor"],
                tags=item["tags"],
                catalog_digest=catalog_digest,
                merchant_id=merchant_id,
                iat_unix=iat,
                private_key_pem=private_key_pem,
            )

        served_items.append(
            {
                **item,
                "offers": offers.get(item["sku"], []),
                "catalog_attestation": attestation,
            }
        )

    return {
        "merchant_id": merchant_id,
        "catalog_digest": catalog_digest,
        "currency": "INR",
        "items": served_items,
    }
