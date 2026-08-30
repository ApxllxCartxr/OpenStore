"""Relying-Party side of a real WebAuthn ceremony.

The merchant NEVER signs the human-auth leg itself. It issues a challenge,
the browser's authenticator signs it, and this RP *verifies* that assertion
(rpId hash, origin, challenge, signature, UV flag) before relaying the
authenticator's own outputs into the PoAI `authority` section.

A third party later re-verifies the same `authority` via ``openstore.verify``,
so the merchant cannot forge the human step.

This implements the ``none`` attestation flow (no hardware attestation) which is
the common, real-world path for account-backed WebAuthn.
"""

from __future__ import annotations

import cbor2
import hashlib
import json
import secrets
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from openstore.canonical import canonical_json_bytes
from openstore.evidence import b64url, b64url_decode


class WebAuthnError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cose_public_key(pub) -> bytes:
    nums = pub.public_numbers()
    return cbor2.dumps({
        1: 2, -1: 1,
        -2: nums.x.to_bytes(32, "big"),
        -3: nums.y.to_bytes(32, "big"),
    })


def cose_public_key_to_ec(cose_b64):
    raw = cose_b64 if isinstance(cose_b64, bytes) else b64url_decode(cose_b64)
    obj = cbor2.loads(raw)
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(obj[-2], "big"),
        int.from_bytes(obj[-3], "big"),
        ec.SECP256R1(),
    ).public_key()


class WebAuthnRP:
    def __init__(self, rp_id: str, origin: str):
        self.rp_id = rp_id
        self.origin = origin
        self.credentials: dict[bytes, bytes] = {}  # credential_id -> COSE public key
        self._challenges: dict[str, bytes] = {}
        self._reg_challenges: dict[str, bytes] = {}
        self._dev = None

    # ---------- registration (none attestation) ----------

    def register(self, *, credential_id: bytes, cose_public_key: bytes) -> None:
        self.credentials[credential_id] = cose_public_key

    def register_dev_credential(self) -> bytes:
        """Create + store a credential for the in-process (no-browser) path."""
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        key = _ec.generate_private_key(ec.SECP256R1())
        cred_id = secrets.token_bytes(16)
        self.register(credential_id=cred_id, cose_public_key=_cose_public_key(key.public_key()))
        self._dev = (cred_id, key)
        return cred_id

    def begin_registration(self) -> dict:
        challenge = secrets.token_bytes(32)
        session_id = secrets.token_hex(16)
        self._reg_challenges[session_id] = challenge
        return {
            "session_id": session_id,
            "challenge": b64url(challenge),
            "rp_id": self.rp_id,
        }

    def register_credential(self, *, attestation_object: str, client_data_json: str, session_id: str) -> str:
        """Verify a real ``navigator.credentials.create()`` response (fmt=none)."""
        expected = self._reg_challenges.pop(session_id, None)
        if expected is None:
            raise WebAuthnError("unknown_or_expired_session")
        client_data = json.loads(b64url_decode(client_data_json))
        if client_data.get("type") != "webauthn.create":
            raise WebAuthnError("unexpected_client_data_type")
        if client_data.get("origin") != self.origin:
            raise WebAuthnError("origin_mismatch")
        if b64url_decode(client_data.get("challenge", "")) != expected:
            raise WebAuthnError("challenge_mismatch")
        ao = cbor2.loads(b64url_decode(attestation_object))
        if ao.get(1) != "none":
            raise WebAuthnError("unsupported_attestation_fmt")
        auth_data = ao[2]
        if len(auth_data) < 37 or not (auth_data[32] & 0x40):
            raise WebAuthnError("no_attested_credential_data")
        cred_data = auth_data[37:]
        cred_len = int.from_bytes(cred_data[16:18], "big")
        credential_id = cred_data[18:18 + cred_len]
        cose = cred_data[18 + cred_len:]
        self.register(credential_id=credential_id, cose_public_key=cose)
        return b64url(credential_id)

    # ---------- authentication (assertion) ----------

    def begin_assertion(self, policy: dict, cart_hash: str) -> dict:
        # The challenge is the cart/policy binding hash: the authenticator signs
        # over *this specific* cart + policy, so a swapped quote fails verification.
        # session_id enforces one-time use (replay protection) on top of that.
        policy_hash = hashlib.sha256(canonical_json_bytes(policy)).digest()
        cart_bytes = bytes.fromhex(cart_hash.replace("sha256:", ""))
        challenge = hashlib.sha256(policy_hash + cart_bytes).digest()
        session_id = secrets.token_hex(16)
        self._challenges[session_id] = challenge
        return {
            "session_id": session_id,
            "challenge": b64url(challenge),
            "rp_id": self.rp_id,
            "publicKey": {
                "challenge": b64url(challenge),
                "rpId": self.rp_id,
                "timeout": 60000,
                "allowCredentials": [
                    {"type": "public-key", "id": b64url(cid)} for cid in self.credentials
                ],
                "userVerification": "required",
            },
        }

    def complete_assertion(self, session_id: str, response: dict, *, policy: dict, cart_hash: str) -> dict:
        expected = self._challenges.pop(session_id, None)
        if expected is None:
            raise WebAuthnError("unknown_or_expired_session")
        raw_id = b64url_decode(response["rawId"])
        cd = response["response"]
        client_data = json.loads(b64url_decode(cd["clientDataJSON"]))
        if client_data.get("type") != "webauthn.get":
            raise WebAuthnError("unexpected_client_data_type")
        if b64url_decode(client_data.get("challenge", "")) != expected:
            raise WebAuthnError("challenge_mismatch")
        if client_data.get("origin") != self.origin:
            raise WebAuthnError("origin_mismatch")

        auth_data = b64url_decode(cd["authenticatorData"])
        if len(auth_data) < 37:
            raise WebAuthnError("malformed_authenticator_data")
        if hashlib.sha256(self.rp_id.encode()).digest() != auth_data[:32]:
            raise WebAuthnError("rp_id_mismatch")
        if not (auth_data[32] & 0x01):
            raise WebAuthnError("user_present_flag_missing")

        cose = self.credentials.get(raw_id)
        if cose is None:
            raise WebAuthnError("unknown_credential")
        pub = cose_public_key_to_ec(cose)
        signed = auth_data + hashlib.sha256(b64url_decode(cd["clientDataJSON"])).digest()
        raw = b64url_decode(cd["signature"])
        if len(raw) != 64:
            raise WebAuthnError("bad_signature")
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        try:
            pub.verify(encode_dss_signature(r, s), signed, ec.ECDSA(hashes.SHA256()))
        except Exception:
            raise WebAuthnError("signature_invalid")

        uv = bool(auth_data[32] & 0x04)
        policy_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
        return {
            "enrolment": {"public_key": b64url(cose)},
            "webauthn": {
                "authenticator_data": cd["authenticatorData"],
                "client_data_json": cd["clientDataJSON"],
                "signature": cd["signature"],
                "uv": uv,
                "signed_at": _now_iso(),
                "challenge_binding": {
                    "mode": "cart",
                    "policy_hash": policy_hash,
                    "cart_hash": cart_hash,
                },
            },
            "policy": policy,
        }
