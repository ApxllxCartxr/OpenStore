import json
import time
import uuid
import hashlib
import jwt as pyjwt
from merchant.models import MandatePayload

ALLOWED_ALGS = ["EdDSA"]
MANDATE_TTL_SECONDS = 120
KID = "merchant-key-1"

_PRIVATE_KEY = None
_PUBLIC_KEY = None


def _load_keys():
    global _PRIVATE_KEY, _PUBLIC_KEY
    if _PRIVATE_KEY is not None:
        return
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    with open("merchant_signing_key.pem", "rb") as f:
        _PRIVATE_KEY = serialization.load_pem_private_key(f.read(), password=None)
    _PUBLIC_KEY = _PRIVATE_KEY.public_key()


def canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_cart_hash(items: list[dict]) -> str:
    return hashlib.sha256(canonical_json_bytes({"items": items})).hexdigest()


def issue_mandate(
    merchant_id: str,
    checkout_id: str,
    client_id: str,
    cart_hash: str,
    cart_version: int,
    items: list[dict],
    total_minor: int,
    delivery_address_hash: str,
) -> tuple[str, str]:
    _load_keys()
    now = int(time.time())
    payload = MandatePayload(
        iss=merchant_id,
        jti=str(uuid.uuid4()),
        chk=checkout_id,
        sub=client_id,
        cart={"hash": cart_hash, "version": cart_version, "items": items},
        iat=now,
        exp=now + MANDATE_TTL_SECONDS,
        amt=total_minor,
        nonce=uuid.uuid4().hex,
        dlv=delivery_address_hash,
    ).model_dump()

    jws_compact = pyjwt.encode(payload, _PRIVATE_KEY, algorithm="EdDSA", headers={"kid": KID})
    fingerprint = hashlib.sha256(jws_compact.encode()).hexdigest()[:8]
    return jws_compact, fingerprint


def verify_mandate(jws_compact: str) -> dict:
    """Raises jwt exceptions on any failure. Caller is responsible for the
    checkout-state / cart-hash / jti-unburned / spend-cap checks that follow —
    this function ONLY proves the signature and basic claims are valid."""
    _load_keys()
    header = pyjwt.get_unverified_header(jws_compact)
    if header.get("alg") not in ALLOWED_ALGS:
        raise ValueError(f"Rejected alg: {header.get('alg')}")
    return pyjwt.decode(jws_compact, _PUBLIC_KEY, algorithms=ALLOWED_ALGS, audience="openstore-mcp")
