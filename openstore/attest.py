"""Catalog attestation (IMPLEMENTATION_SPEC §4).

An attestation binds a `(sku, price_minor, tags)` triple to the digest of the
catalog it was drawn from, signed by the merchant (ES256 JWS Compact, matching
`evidence.py` so the offline verifier can reuse the same crypto).

`compute_catalog_digest` and `attest_item` are pure (no DB / clock / network)
so the verifier re-runs the same logic. `AttestationRegistry` is an in-memory
`get_or_create` stand-in for the spec's `CatalogAttestation` table, implementing
the R4.2 reuse rule without coupling to a persistence layer.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import List, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .canonical import canonical_json_bytes


class UnattestableError(ValueError):
    """Raised when an item cannot be attested (e.g. no merchant key configured)."""


class AttestationError(ValueError):
    """Raised when an attestation JWS fails to verify."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _raw_ecdsa_signature(private_key: ec.EllipticCurvePrivateKey, signing_input: bytes) -> bytes:
    der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _sorted_tags(tags) -> Tuple[str, ...]:
    return tuple(sorted(tags or []))


def compute_catalog_digest(products: List[dict]) -> str:
    """`digest()` of the normalised catalog (§4.1): items sorted by sku ascending,
    each with `sku`, `price_minor`, `tags` (sorted ascending)."""
    items = [
        {
            "sku": p["sku"],
            "price_minor": p["price_minor"],
            "tags": list(_sorted_tags(p.get("tags"))),
        }
        for p in sorted(products, key=lambda p: p["sku"])
    ]
    return _digest_obj({"items": items})


def _digest_obj(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def attest_item(
    *,
    sku: str,
    price_minor: int,
    tags: List[str],
    catalog_digest: str,
    merchant_id: str,
    iat_unix: int,
    signing_key,
    kid: str,
) -> str:
    """Sign an ES256 JWS Compact over the attestation payload (§4.1)."""
    if signing_key is None:
        raise UnattestableError("no merchant attestation key configured")
    payload = {
        "sku": sku,
        "price_minor": price_minor,
        "tags": list(_sorted_tags(tags)),
        "catalog_digest": catalog_digest,
        "merchant_id": merchant_id,
        "iat": iat_unix,
    }
    header = {"alg": "ES256", "kid": kid}
    h = b64url(canonical_json_bytes(header))
    p = b64url(canonical_json_bytes(payload))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _raw_ecdsa_signature(signing_key, signing_input)
    return f"{h}.{p}.{b64url(sig)}"


def verify_attestation(jws_compact: str, public_key) -> dict:
    """Verify an attestation JWS and return its payload. Raises `AttestationError`."""
    try:
        h, p, sig_b64 = jws_compact.split(".")
    except ValueError:
        raise AttestationError("attestation_malformed")
    signing_input = f"{h}.{p}".encode("ascii")
    raw = b64url_decode(sig_b64)
    if len(raw) != 64:
        raise AttestationError("attestation_signature_invalid")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    try:
        public_key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception as e:
        raise AttestationError("attestation_signature_invalid") from e
    try:
        return json.loads(b64url_decode(p))
    except Exception as e:
        raise AttestationError("attestation_payload_malformed") from e


def attestation_fresh(payload: dict, cart_created_at_unix: int) -> bool:
    """R4.3 — an attestation is valid evidence iff `iat <= cart.created_at`."""
    return payload.get("iat", 0) <= cart_created_at_unix


@dataclass(frozen=True, slots=True)
class CatalogAttestation:
    sku: str
    price_minor: int
    tags: Tuple[str, ...]
    catalog_digest: str
    merchant_id: str
    iat: int
    jws: str


class AttestationRegistry:
    """In-memory `get_or_create_attestation` (spec §4.2 / R4.2).

    Reuses an existing attestation iff `sku`, `price_minor`, sorted `tags`, and
    `catalog_digest` all match. Any change mints a new attestation with a new
    `iat`.
    """

    def __init__(self, *, merchant_id: str, signing_key, kid: str):
        self._merchant_id = merchant_id
        self._signing_key = signing_key
        self._kid = kid
        self._cache: dict = {}

    def _key(self, sku: str, price_minor: int, tags: Tuple[str, ...], catalog_digest: str) -> tuple:
        return (sku, price_minor, tags, catalog_digest)

    def get_or_create(
        self,
        *,
        sku: str,
        price_minor: int,
        tags: List[str],
        catalog_digest: str,
        iat_unix: int,
    ) -> CatalogAttestation:
        if self._signing_key is None:
            raise UnattestableError(f"cannot attest sku {sku}: no merchant key")
        stags = _sorted_tags(tags)
        key = self._key(sku, price_minor, stags, catalog_digest)
        existing = self._cache.get(key)
        if existing is not None:
            return existing
        jws = attest_item(
            sku=sku,
            price_minor=price_minor,
            tags=list(stags),
            catalog_digest=catalog_digest,
            merchant_id=self._merchant_id,
            iat_unix=iat_unix,
            signing_key=self._signing_key,
            kid=self._kid,
        )
        rec = CatalogAttestation(
            sku=sku,
            price_minor=price_minor,
            tags=stags,
            catalog_digest=catalog_digest,
            merchant_id=self._merchant_id,
            iat=iat_unix,
            jws=jws,
        )
        self._cache[key] = rec
        return rec
