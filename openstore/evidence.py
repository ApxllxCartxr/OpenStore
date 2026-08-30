"""Evidence bundle assembly, hash chain, and ES256 merchant signature.

Pure of database / web framework so the standalone verifier can reuse it.
Follows IMPLEMENTATION_SPEC §1.3 (hash chain) and §6 (bundle shape).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

from .canonical import canonical_json_bytes

SECTION_ORDER = (
    "transaction",
    "human_intent",
    "authority",
    "goods",
    "agent",
    "adjudication",
    "notification",
    "aal",
)

POAI_VERSION = "0.1"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ---------- Hash chain (§1.3) ----------


def _link_bytes(sections: dict) -> list[bytes]:
    links: list[bytes] = []
    prev: Optional[bytes] = None
    for name in SECTION_ORDER:
        c = canonical_json_bytes(sections.get(name))  # null section -> b"null"
        link = hashlib.sha256(prev + c).digest() if prev is not None else hashlib.sha256(c).digest()
        links.append(link)
        prev = link
    return links


def compute_chain(sections: dict) -> dict:
    links = _link_bytes(sections)
    link_strs = ["sha256:" + l.hex() for l in links]
    return {"links": link_strs, "root": link_strs[-1]}


def verify_chain(bundle: dict) -> None:
    """Recompute the chain and assert it matches the bundle's recorded chain."""
    sections = {name: bundle.get(name) for name in SECTION_ORDER}
    expected = compute_chain(sections)
    if expected["links"] != bundle["chain"]["links"]:
        raise ValueError("chain_broken: link mismatch")
    if expected["root"] != bundle["chain"]["root"]:
        raise ValueError("chain_broken: root mismatch")


# ---------- ES256 JWS (§1.2) ----------


def _raw_ecdsa_signature(private_key: ec.EllipticCurvePrivateKey, signing_input: bytes) -> bytes:
    der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def sign_merchant(bundle_id: str, issued_at: str, root: str, signing_key, kid: str) -> str:
    header = {"alg": "ES256", "kid": kid}
    payload = {"bundle_id": bundle_id, "issued_at": issued_at, "root": root}
    h = b64url(canonical_json_bytes(header))
    p = b64url(canonical_json_bytes(payload))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _raw_ecdsa_signature(signing_key, signing_input)
    return f"{h}.{p}.{b64url(sig)}"


def verify_merchant_signature(jws: str, public_key, expected_root: Optional[str] = None) -> dict:
    try:
        h, p, sig_b64 = jws.split(".")
    except ValueError:
        raise ValueError("merchant_signature_invalid")
    # R1.2b: allowlist alg == ES256 only; reject "none" and anything else.
    try:
        header = json.loads(b64url_decode(h))
    except Exception:
        raise ValueError("merchant_signature_invalid")
    if header.get("alg") != "ES256":
        raise ValueError("merchant_signature_invalid")
    signing_input = f"{h}.{p}".encode("ascii")
    raw = b64url_decode(sig_b64)
    if len(raw) != 64:
        raise ValueError("merchant_signature_invalid")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    try:
        public_key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception:
        raise ValueError("merchant_signature_invalid")
    payload = json.loads(b64url_decode(p))
    if expected_root is not None and payload.get("root") != expected_root:
        raise ValueError("merchant_signature_invalid")
    return payload


def public_jwk(public_key, kid: str) -> dict:
    nums = public_key.public_numbers()
    x = nums.x.to_bytes(32, "big")
    y = nums.y.to_bytes(32, "big")
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(x),
        "y": b64url(y),
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
    }


# ---------- Bundle assembly (§6) ----------


@dataclass
class BundleInput:
    bundle_id: str
    transaction: dict
    goods: dict
    agent: dict
    adjudication: dict
    aal: dict  # {"level": int, "predicates": {...}, "reasons": [...]}
    authority: Optional[dict] = None
    human_intent: Optional[dict] = None
    notification: Optional[dict] = None


def build_bundle(inp: BundleInput) -> dict:
    sections = {
        "transaction": inp.transaction,
        "human_intent": inp.human_intent,
        "authority": inp.authority,
        "goods": inp.goods,
        "agent": inp.agent,
        "adjudication": inp.adjudication,
        "notification": inp.notification,
        "aal": inp.aal,
    }
    bundle = {
        "poai_version": POAI_VERSION,
        "bundle_id": inp.bundle_id,
        **sections,
        "chain": compute_chain(sections),
    }
    return bundle


def sign_and_anchor(bundle: dict, signing_key, kid: str, issued_at: str) -> dict:
    root = bundle["chain"]["root"]
    bundle["chain"]["merchant_signature"] = sign_merchant(
        bundle["bundle_id"], issued_at, root, signing_key, kid
    )
    return bundle


# ---------- §6.3 asynchronous time anchor (R6.3a / R6.3b) ----------


def set_time_anchor_none(bundle: dict) -> dict:
    """Persist the bundle immediately with an absent anchor (R6.3a).

    Anchoring is asynchronous so it MUST NOT block checkout. The worker later
    upgrades this to a salted `merkle_daily` anchor.
    """
    bundle.setdefault("chain", {})["time_anchor"] = {"type": "none"}
    return bundle


def salted_anchor_root(root: str, salt: bytes) -> str:
    """R6.3b — anchor the *salted* root so a public log cannot learn tx timing."""
    return "sha256:" + digest({"root": root, "salt": b64url(salt)}).hex()


def upgrade_time_anchor(bundle: dict, salt: bytes, published_url: str = "") -> dict:
    """Replace the `{"type":"none"}` anchor with a salted merkle_daily anchor."""
    root = bundle["chain"]["root"]
    bundle["chain"]["time_anchor"] = {
        "type": "merkle_daily",
        "root": salted_anchor_root(root, salt),
        "salt": b64url(salt),
        "published_url": published_url or None,
        "anchored_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return bundle


def load_pem_private_key_bytes(pem: bytes, password=None):
    return load_pem_private_key(pem, password=password)


def load_pem_public_key_bytes(pem: bytes):
    return load_pem_public_key(pem)
