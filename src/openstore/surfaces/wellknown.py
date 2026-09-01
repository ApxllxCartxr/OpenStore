# OpenStore surfaces — Well-known manifest builders (S6.4)
# Per PRD §3.9 — 6 manifests, schema verbatim, protocols[] from registry.

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from openstore.config import Settings
from openstore.core.holdcancel import AAL_HOLD_SECONDS

# DECISIONS §11.1.10: per-merchant keypairs, kid = "{merchant_id}-key-{n}".
# Keys are cached keyed by merchant_id so each install serves only its own keys.
POAI_KEYS: dict[str, dict[str, Any]] = {}


def get_catalog_signing_key(merchant_id: str) -> bytes | None:
    """Return the merchant's POAI private key for signing catalog attestations."""
    return _load_or_generate_poai_keys(merchant_id).get("private_key")


def _load_or_generate_poai_keys(merchant_id: str) -> dict[str, Any]:
    global POAI_KEYS
    if merchant_id in POAI_KEYS:
        return POAI_KEYS[merchant_id]
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    priv_bytes = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = key.public_key().public_numbers()
    x_b64 = base64.urlsafe_b64encode(pub_numbers.x.to_bytes(32, "big")).decode().rstrip("=")
    y_b64 = base64.urlsafe_b64encode(pub_numbers.y.to_bytes(32, "big")).decode().rstrip("=")
    POAI_KEYS[merchant_id] = {
        "private_key": priv_bytes,
        "public_key": key.public_key(),
        "jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": x_b64,
            "y": y_b64,
            "kid": f"{merchant_id}-key-1",
            "alg": "ES256",
        },
    }
    return POAI_KEYS[merchant_id]


def build_agent_commerce_manifest(config: Settings, origin: str) -> dict[str, Any]:
    return {
        "version": "0.2",
        "merchant": {
            "name": config.merchant.name,
            "id": _merchant_id(config),
        },
        "storefront": f"{origin}/",
        "catalog_endpoint": f"{origin}/agent/catalog",
        "mcp_endpoint": f"{origin}/agent/mcp",
        "a2a_agent_card": f"{origin}/.well-known/agent-card.json",
        "auth": {
            "type": "oauth2",
            "authorization_server": f"{origin}/.well-known/oauth-authorization-server",
            "scopes_supported": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
        },
        "policy": {
            "currency": "INR",
            "max_unconfirmed_spend_minor": 0,
            "requires_human_approval": True,
            "default_per_tx_cap_minor": 50000,
        },
        "protocols": [
            {
                "name": "mcp",
                "version": "2025-06-18",
                "endpoint": f"{origin}/agent/mcp",
                "auth": ["oauth2_bearer"],
                "authority_schemes": ["native_webauthn"],
                "spec_excerpt": "/protocols/mcp/spec-excerpt",
            },
            {
                "name": "acp",
                "version": "2024-11-01",
                "endpoint": f"{origin}/agent/acp",
                "auth": ["oauth2_bearer", "http_message_signature"],
                "authority_schemes": ["acp_delegated_token", "native_webauthn"],
                "spec_excerpt": "/protocols/acp/spec-excerpt",
            },
        ],
        "evidence": {
            "poai_version": "0.1",
            "bundle_endpoint": "/orders/{checkout_id}/evidence",
            "jwks": f"{origin}/.well-known/poai-jwks.json",
        },
        "campaigns": {
            "feed_endpoint": f"{origin}/agent/campaigns",
            "signed_feed": f"{origin}/.well-known/agent-campaigns.json",
        },
    }


def build_agent_policy_manifest(config: Settings) -> dict[str, Any]:
    return {
        "currency": "INR",
        "requires_human_approval": True,
        "aal_hold_seconds": {
            "AAL0": None,
            "AAL1": AAL_HOLD_SECONDS.get(1),
            "AAL2": AAL_HOLD_SECONDS.get(2),
            "AAL3": AAL_HOLD_SECONDS.get(3),
        },
        "evidence_retention_days": getattr(config, "evidence_retention_days", 540),
        "max_per_tx_cap_minor": 50000,
        "policy_schema_version": 2,
    }


def _merchant_id(config: Settings) -> str:
    return config.merchant.name.lower().replace(" ", "-").replace("'", "")


def get_poai_jwks(config: Settings) -> dict[str, Any]:
    keys_data = _load_or_generate_poai_keys(_merchant_id(config))
    return {"keys": [keys_data["jwk"]]}


def get_signed_campaign_feed(config: Settings, origin: str) -> dict[str, Any]:
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import Campaign, CampaignState

    session = get_session(config)
    try:
        all_active = list(session.exec(
            select(Campaign).where(Campaign.state == CampaignState.ACTIVE)
        ).all())
    finally:
        session.close()

    # INV-13: only ACTIVE campaigns whose window contains now appear in the feed.
    # Out-of-window ACTIVE campaigns are excluded — they would not survive a
    # buyer cart that references them (compiler check 12) anyway.
    now = datetime.now(UTC)
    eligible: list[Campaign] = []
    for c in all_active:
        if c.starts_at is None or c.ends_at is None:
            continue
        # Normalize to UTC-aware for comparison
        starts_at = c.starts_at if c.starts_at.tzinfo else c.starts_at.replace(tzinfo=UTC)
        ends_at = c.ends_at if c.ends_at.tzinfo else c.ends_at.replace(tzinfo=UTC)
        if starts_at <= now < ends_at:
            eligible.append(c)

    if not eligible:
        return {"campaigns": [], "merchant_signature": None}

    campaigns_data = [
        {
            "campaign_id": c.id,
            "title": c.title,
            "discount_bps": c.discount_bps,
            "applies_to_skus": c.applies_to_skus,
            "starts_at": c.starts_at.isoformat() if c.starts_at else None,
            "ends_at": c.ends_at.isoformat() if c.ends_at else None,
        }
        for c in eligible
    ]
    payload_bytes = json.dumps({"campaigns": campaigns_data}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")

    keys_data = _load_or_generate_poai_keys(_merchant_id(config))
    priv_bytes = keys_data["private_key"]
    key = serialization.load_der_private_key(priv_bytes, password=None)
    if isinstance(key, ec.EllipticCurvePrivateKey):
        kid = keys_data["jwk"]["kid"]
        header = json.dumps({"alg": "ES256", "kid": kid, "typ": "JWT"}, sort_keys=True).encode("utf-8")
        header_b64 = base64.urlsafe_b64encode(header).decode().rstrip("=")
        signing_input = f"{header_b64}.{payload_b64}"
        der_sig = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        sig_b64 = base64.urlsafe_b64encode(raw_sig).decode().rstrip("=")
        signature = f"{signing_input}.{sig_b64}"
    else:
        signature = None

    return {"campaigns": campaigns_data, "merchant_signature": signature}
