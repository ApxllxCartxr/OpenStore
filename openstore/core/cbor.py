import hashlib

import cbor2


def canonical_cbor(obj) -> bytes:
    return cbor2.dumps(obj, canonical=True)


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()
