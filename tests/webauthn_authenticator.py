"""A software authenticator, so the passkey ceremony is tested rather than
described.

Every byte here is built the way a real authenticator builds it — the CBOR
attestation object, the `authData` flags, the DER signature over
`authData || sha256(clientDataJSON)`. That matters because the property under
test is cryptographic: the challenge **is** the cart binding, so a fixture that
shortcut the signing would assert nothing about the thing that binds.

`cbor2` is `py_webauthn`'s own dependency, used here only to build the responses
it will parse.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

AAGUID = bytes(16)

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40


def _cose_key(public: ec.EllipticCurvePublicKey) -> bytes:
    numbers = public.public_numbers()
    return cbor2.dumps(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )


def _client_data(kind: str, challenge: bytes, origin: str) -> bytes:
    return json.dumps(
        {
            "type": kind,
            "challenge": bytes_to_base64url(challenge),
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass
class SoftwareAuthenticator:
    """One device, holding one key per credential it creates."""

    rp_id: str
    origin: str
    user_verified: bool = True
    counter: int = 0
    keys: dict[bytes, ec.EllipticCurvePrivateKey] = field(default_factory=dict)

    def _auth_data(self, *, attested: bytes = b"") -> bytes:
        flags = FLAG_UP
        if self.user_verified:
            flags |= FLAG_UV
        if attested:
            flags |= FLAG_AT
        self.counter += 1
        return (
            hashlib.sha256(self.rp_id.encode("utf-8")).digest()
            + bytes([flags])
            + self.counter.to_bytes(4, "big")
            + attested
        )

    def create(self, challenge: bytes, *, attestation: str = "none") -> dict[str, object]:
        """A `navigator.credentials.create()` response.

        `attestation="packed"` produces self-attestation — the credential's own
        key signing the registration — which is the cheapest real attestation a
        platform authenticator gives and the one that makes a one-prompt
        ceremony possible.
        """
        key = ec.generate_private_key(ec.SECP256R1())
        credential_id = secrets.token_bytes(16)
        self.keys[credential_id] = key

        cose = _cose_key(key.public_key())
        attested = AAGUID + len(credential_id).to_bytes(2, "big") + credential_id + cose
        auth_data = self._auth_data(attested=attested)
        client_data = _client_data("webauthn.create", challenge, self.origin)

        if attestation == "packed":
            signature = key.sign(
                auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
            )
            attestation_object = cbor2.dumps(
                {"fmt": "packed", "attStmt": {"alg": -7, "sig": signature}, "authData": auth_data}
            )
        else:
            attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})

        return {
            "id": bytes_to_base64url(credential_id),
            "rawId": bytes_to_base64url(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation_object),
            },
        }

    def get(self, challenge: bytes, credential_id_b64: str) -> dict[str, object]:
        """A `navigator.credentials.get()` response."""
        from webauthn.helpers import base64url_to_bytes

        credential_id = base64url_to_bytes(credential_id_b64)
        key = self.keys[credential_id]
        auth_data = self._auth_data()
        client_data = _client_data("webauthn.get", challenge, self.origin)
        signature = key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": credential_id_b64,
            "rawId": credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": None,
            },
        }
