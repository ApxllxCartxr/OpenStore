# OpenStore surfaces — Catalog feed (S6.5)
# Per PRD §3.8: catalog items per §3.8, sorted tags, integer unit_minor,
# ES256 catalog attestation per payload in §3.8.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openstore.config import Settings

from openstore.surfaces.adapters.base import AdapterCapability
from openstore.surfaces.adapters.cache import AdapterCache
from openstore.surfaces.adapters.errors import AdapterError, not_configured, price_invalid, price_missing, sku_missing
from openstore.surfaces.adapters.normalize import clean_sku, normalize_tags, normalized_item, parse_stock, price_to_minor
from openstore.surfaces.adapters.registry import get_adapter, register_adapter

# Legacy single-slot cache. Tests reach in and set this to None to force a
# reload, so it stays as the invalidation signal, but the real cache below is
# keyed by (path, mtime): the single slot was shared across every config in a
# process, so two merchant configs served each other's catalog, and an edited
# catalog.yaml needed a restart to take effect.
CATALOG_CACHE: list[dict[str, Any]] | None = None

_CATALOG_BY_PATH: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _yaml_path(config: Settings, source: Any) -> str | None:
    override = getattr(source, "path", None) if source is not None else None
    if override:
        return str(override)
    return getattr(config, "catalog_path", None)


def _load_yaml_items(config: Settings, source: Any) -> list[dict[str, Any]]:
    """YAML adapter read: fail-loud pricing (DEF-1), frozen shape (S25)."""
    global CATALOG_CACHE
    from openstore.config import merchant_id as _merchant_id

    _ = _merchant_id  # merchant scoping arrives with multi-tenant reads (S26+)
    catalog_path = _yaml_path(config, source)
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
    for raw_item in items:
        if not isinstance(raw_item, dict) or clean_sku(raw_item.get("sku")) == "":
            raise sku_missing("yaml", "catalog row without a sku field")
        sku = clean_sku(raw_item.get("sku"))
        if "unit_minor" not in raw_item and "price_minor" not in raw_item:
            raise price_missing(sku, "yaml")
        raw_price = raw_item.get("unit_minor", raw_item.get("price_minor"))
        if isinstance(raw_price, int) and not isinstance(raw_price, bool):
            unit_minor = raw_price
        elif isinstance(raw_price, str) and raw_price.strip().lstrip("-").isdigit():
            unit_minor = int(raw_price.strip())
        else:
            raise price_invalid(sku, "yaml", raw_price)
        if unit_minor <= 0:
            raise price_invalid(sku, "yaml", raw_price)
        try:
            stock = parse_stock(raw_item.get("stock", None))
        except ValueError as e:
            raise price_invalid(sku, "yaml", raw_item.get("stock"), "stock") from e
        normalized.append(
            normalized_item(
                sku=sku,
                name=str(raw_item.get("name", sku)),
                unit_minor=unit_minor,
                tags=normalize_tags(raw_item.get("tags", [])),
                related_skus=[str(s) for s in raw_item.get("related_skus", [])],
                related_source="yaml",
                description=str(raw_item.get("description", "")),
                offers=raw_item.get("offers", []),
                stock=stock,
            )
        )
    _CATALOG_BY_PATH[key] = (mtime, normalized)
    CATALOG_CACHE = normalized
    return normalized


class YamlAdapter:
    """The YAML catalog as an SDK adapter (no special status)."""

    name = "yaml"
    capabilities = frozenset(
        {AdapterCapability.CATALOG_READ, AdapterCapability.STOCK_READ}
    )

    def __init__(self, config: Settings, source: Any = None):
        self._config = config
        self._source = source

    def fetch_items(self) -> list[dict[str, Any]]:
        return _load_yaml_items(self._config, self._source)

    def fetch_stock(self, skus: list[str]) -> dict[str, int]:
        by_sku = {i["sku"]: i for i in self.fetch_items()}
        out: dict[str, int] = {}
        for sku in skus:
            item = by_sku.get(sku)
            if item is not None and item.get("stock") is not None:
                out[sku] = int(item["stock"])
        return out

    def write_stock(self, deltas: dict[str, int]) -> None:
        raise not_configured("yaml", "stock write-back (flat file, no write API)")

    def push_order_status(self, order: Any) -> None:
        raise not_configured("yaml", "order write-back (flat file, no order API)")

    def health_check(self) -> Any:
        try:
            items = self.fetch_items()
        except AdapterError as e:
            return Any(ok=False, detail=f"{e.reason_code}: {e.message}")
        return Any(ok=True, item_count=len(items))


def _build_yaml(config: Settings, source: Any, _client: Any) -> Any:
    return YamlAdapter(config, source)


register_adapter("yaml", _build_yaml)


def load_catalog(config: Settings, session: Session | None = None) -> list[dict[str, Any]]:
    """Load the catalog through the resolved CatalogAdapter (S25).

    Legacy catalog_path:/shopify: blocks normalize into catalog_source at
    resolution (adapters/registry.py); YAML-with-no-path and missing files
    still answer [] exactly as before.
    
    If a session is provided, it's used to apply the DB overlay (catalog_source
    from /merchant/settings). If no session, a new one is created.
    """
    from openstore.surfaces.adapters import get_adapter
    from openstore.core.settings_overlay import effective_settings

    eff = effective_settings(config, session)
    return get_adapter(eff, purpose="catalog").fetch_items()


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
    finally:
        session.close()


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
