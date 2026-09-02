# OpenStore — VirtualAuthenticator (dev tooling, S3.3/S3.4).
#
# A deterministic in-memory FIDO2 authenticator for test/dev ceremonies. It
# performs REAL py_webauthn-verifiable registration ("fmt none" via explicit
# attestation) and assertion ceremonies, defaulting to an EC P-256 (ES256, COSE
# alg -7) key. It reproduces the exact wire bytes of the GOLDEN/webauthn fixtures
# so that the fixture generator (scripts/make_webauthn_fixtures.py) can be
# refactored onto it without changing any pinned golden vector (R0.7).

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

RP_ID_DEFAULT = "openstore.test"
ORIGIN_DEFAULT = "https://openstore.test"

FLAGS_UP_UV_AT = 0x01 | 0x04 | 0x40  # UP | UV | attested-credential-data (registration)
FLAGS_UP_UV = 0x01 | 0x04  # UP | UV (assertion)

# COSEKey field tags (mirror py_webauthn helpers.cose).
COSE_KTY, COSE_ALG, COSE_CRV = 1, 3, -1
COSE_X, COSE_Y = -2, -3
COSE_N, COSE_E = -1, -2


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_raw(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def cose_ec2_p256_public(private_key: ec.EllipticCurvePrivateKey, alg: int = -7) -> bytes:
    nums = private_key.public_key().public_numbers()
    size = ec.SECP256R1().key_size // 8
    return cbor2.dumps(
        {
            COSE_KTY: 2,
            COSE_ALG: alg,
            COSE_CRV: 1,
            COSE_X: nums.x.to_bytes(size, "big"),
            COSE_Y: nums.y.to_bytes(size, "big"),
        }
    )


def cose_rsa_public(private_key: rsa.RSAPrivateKey, alg: int = -257) -> bytes:
    nums = private_key.public_key().public_numbers()
    n_len = (nums.n.bit_length() + 7) // 8
    e_len = (nums.e.bit_length() + 7) // 8
    return cbor2.dumps(
        {
            COSE_KTY: 3,
            COSE_ALG: alg,
            COSE_N: nums.n.to_bytes(n_len, "big"),
            COSE_E: nums.e.to_bytes(e_len, "big"),
        }
    )


def cose_ed25519_public(private_key: ed25519.Ed25519PrivateKey, alg: int = -8) -> bytes:
    return cbor2.dumps(
        {
            COSE_KTY: 1,
            COSE_ALG: alg,
            COSE_CRV: 6,
            COSE_X: private_key.public_key().public_bytes_raw(),
        }
    )


def _signer_for(key: Any) -> Callable[[Any, bytes], bytes]:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return lambda k, d: k.sign(d, ec.ECDSA(hashes.SHA256()))
    if isinstance(key, rsa.RSAPrivateKey):
        return lambda k, d: k.sign(d, padding.PKCS1v15(), hashes.SHA256())
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return lambda k, d: k.sign(d)
    raise TypeError(f"unsupported key type: {type(key).__name__}")


def _public_cose_for(key: Any) -> bytes:
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return cose_ec2_p256_public(key)
    if isinstance(key, rsa.RSAPrivateKey):
        return cose_rsa_public(key)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return cose_ed25519_public(key)
    raise TypeError(f"unsupported key type: {type(key).__name__}")


def _client_data_json(type_: str, challenge: bytes, origin: str) -> bytes:
    payload = {"type": type_, "challenge": b64u(challenge), "origin": origin, "crossOrigin": False}
    return json.dumps(payload, separators=(",", ":")).encode()


@dataclass
class RegistrationResult:
    credential_id: str
    client_data_json: str
    attestation_object: str
    sign_count: int
    public_key_cose: str


@dataclass
class AssertionResult:
    credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str
    sign_count: int


@dataclass
class VirtualAuthenticator:
    """A single-authenticator in-memory FIDO2 device (S3.3)."""

    credential_id: bytes
    key: Any
    rp_id: str = RP_ID_DEFAULT
    origin: str = ORIGIN_DEFAULT
    sign_count: int = field(default=1)

    def rp_id_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest()

    def register(
        self,
        challenge: bytes,
        sign_count: int | None = None,
    ) -> RegistrationResult:
        """Perform a $ credId-assigning registration ceremony (fmt ``none``)."""
        sc = self.sign_count if sign_count is None else sign_count
        client_data = _client_data_json("webauthn.create", challenge, self.origin)
        cose_pk = _public_cose_for(self.key)
        aaguid = bytes(16)
        cred_id_len = len(self.credential_id).to_bytes(2, "big")
        attested = aaguid + cred_id_len + self.credential_id + cose_pk
        auth_data = self.rp_id_hash() + bytes([FLAGS_UP_UV_AT]) + sc.to_bytes(4, "big") + attested
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        self.sign_count = sc
        return RegistrationResult(
            credential_id=b64u(self.credential_id),
            client_data_json=b64u(client_data),
            attestation_object=b64u(att_obj),
            sign_count=sc,
            public_key_cose=b64u(cose_pk),
        )

    def assert_credential(
        self,
        challenge: bytes,
        sign_count: int | None = None,
    ) -> AssertionResult:
        """Perform a $ credential assertion ceremony (signs authData||SHA256(cd))."""
        sc = self.sign_count if sign_count is None else sign_count
        client_data = _client_data_json("webauthn.get", challenge, self.origin)
        auth_data = self.rp_id_hash() + bytes([FLAGS_UP_UV]) + sc.to_bytes(4, "big")
        sig_base = auth_data + hashlib.sha256(client_data).digest()
        signature = _signer_for(self.key)(self.key, sig_base)
        self.sign_count = sc
        return AssertionResult(
            credential_id=b64u(self.credential_id),
            client_data_json=b64u(client_data),
            authenticator_data=b64u(auth_data),
            signature=b64u(signature),
            sign_count=sc,
        )


def generate_es256_authenticator(
    rp_id: str = RP_ID_DEFAULT,
    origin: str = ORIGIN_DEFAULT,
    credential_id: bytes | None = None,
    sign_count: int = 1,
) -> VirtualAuthenticator:
    """Build a default EC P-256 (ES256) virtual authenticator.

    credential_id defaults to a fresh random 32 bytes (real registrations never
    reuse a credential ID); pass an explicit value for reproducible fixtures.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    if credential_id is None:
        import secrets

        credential_id = secrets.token_bytes(32)
    return VirtualAuthenticator(
        credential_id=credential_id,
        key=key,
        rp_id=rp_id,
        origin=origin,
        sign_count=sign_count,
    )
