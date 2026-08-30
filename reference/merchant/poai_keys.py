"""PoAI ES256 merchant key (IMPLEMENTATION_SPEC §1.2).

Loads `keys/poai_es256.pem`, generating it on first run. The key is gitignored;
the public half is published at `/.well-known/poai-jwks.json`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from openstore.evidence import public_jwk

KEYS_DIR = Path(__file__).parent / "keys"
KEY_PATH = KEYS_DIR / "poai_es256.pem"


def _compute_kid(public_key) -> str:
    nums = public_key.public_numbers()
    raw = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    return "poai-es256-" + hashlib.sha256(raw).hexdigest()[:16]


def _load_or_create() -> ec.EllipticCurvePrivateKey:
    if KEY_PATH.exists():
        pem = KEY_PATH.read_bytes()
    else:
        KEYS_DIR.mkdir(exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        KEY_PATH.write_bytes(pem)
    return serialization.load_pem_private_key(pem, password=None)


_signing_key = _load_or_create()
_kid = _compute_kid(_signing_key.public_key())


def signing_key() -> ec.EllipticCurvePrivateKey:
    return _signing_key


def kid() -> str:
    return _kid


def jwks() -> dict:
    return public_jwk(_signing_key.public_key(), _kid)
