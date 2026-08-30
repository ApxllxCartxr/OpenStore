from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import base58

MULTICODEC_ED25519 = b"\xed\x01"


def did_from_pubkey(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes_raw()
    return "did:key:z" + base58.b58encode(MULTICODEC_ED25519 + raw).decode()


def pubkey_from_did(did: str) -> Ed25519PublicKey:
    if not did.startswith("did:key:z"):
        raise ValueError("unsupported did method")
    decoded = base58.b58decode(did[len("did:key:z") :])
    if decoded[:2] != MULTICODEC_ED25519:
        raise ValueError("unsupported key type")
    return Ed25519PublicKey.from_public_bytes(decoded[2:])


def generate_keypair():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()
