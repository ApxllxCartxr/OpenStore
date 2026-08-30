import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .cbor import sha256 as _sha256
from .did import did_from_pubkey, pubkey_from_did


def sha256_of_json(obj) -> bytes:
    return _sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())

ALG = "EdDSA"


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def sign_digest(private_key: Ed25519PrivateKey, digest: bytes, kid: str) -> str:
    header = {"alg": ALG, "kid": kid}
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    p = b64url(digest)
    signing_input = f"{h}.{p}".encode("ascii")
    sig = private_key.sign(signing_input)
    return f"{h}.{p}.{b64url(sig)}"


def verify_jws(jws: str, public_key: Ed25519PublicKey) -> bytes:
    h, p, s = jws.split(".")
    signing_input = f"{h}.{p}".encode("ascii")
    public_key.verify(b64url_decode(s), signing_input)
    return b64url_decode(p)


def did_for(private_key: Ed25519PrivateKey) -> str:
    return did_from_pubkey(private_key.public_key())


def pubkey_for(did: str) -> Ed25519PublicKey:
    return pubkey_from_did(did)
