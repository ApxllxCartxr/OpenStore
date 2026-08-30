"""Shared WebAuthn authority verifier (SECURITY FIX N1 / C1).

This module is the single source of truth for verifying the human-auth leg of
a native WebAuthn authority. It is deliberately cycle-free: it imports only
``openstore.evidence`` (base64url helpers) and ``openstore.core.types``
(canonical JSON). It must NOT import ``openstore.verify`` or
``openstore.core.authority`` (the package that hosts it), because
``openstore.verify`` imports ``openstore.core.authority`` at module load — a
back-import would create an import cycle.

The three predicates here (e2 signature, e4 UV, e5 cart-binding) are the ones
the original ``native_webauthn.verify`` trusted as caller-supplied booleans.
They are now derived by real cryptographic verification against the enrolled
public key and the authenticator data. ``verify_native_authority`` is
fail-closed: a missing or malformed proof set yields all-False (AAL0).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives import hashes

from openstore.evidence import b64url, b64url_decode


def cose_public_key_to_ec(cose_b64: str):
    """Decode a COSE ES256 public key (base64url) into an EC public key."""
    raw = cose_b64 if isinstance(cose_b64, bytes) else b64url_decode(cose_b64)
    obj = cbor2.loads(raw)
    # COSE key labels: -2 = x, -3 = y (label -1 is the curve identifier, not y).
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(obj[-2], "big"),
        int.from_bytes(obj[-3], "big"),
        ec.SECP256R1(),
    ).public_key()


def _rfc3339_to_unix(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _verify_webauthn_assertion(authority: dict):
    """Verify the WebAuthn assertion signature against the enrolled public key.

    Returns (ok, reason). Fail-closed: any missing field or signature
    mismatch returns (False, reason)."""
    wa = authority.get("webauthn") or {}
    if not wa:
        return False, "webauthn_signature_invalid"
    try:
        pk = cose_public_key_to_ec(authority["enrolment"]["public_key"])
        auth_data = b64url_decode(wa["authenticator_data"])
        client_data = b64url_decode(wa["client_data_json"])
        signed = auth_data + hashlib.sha256(client_data).digest()
        raw = b64url_decode(wa["signature"])
        if len(raw) != 64:
            return False, "webauthn_signature_invalid"
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        pk.verify(encode_dss_signature(r, s), signed, ec.ECDSA(hashes.SHA256()))
        return True, None
    except Exception:
        return False, "webauthn_signature_invalid"


def _verify_challenge_binding(authority: dict):
    """Verify the assertion's challenge_binding matches the policy hash + cart hash."""
    wa = authority.get("webauthn") or {}
    cb = wa.get("challenge_binding") or {}
    try:
        client_data = json.loads(b64url_decode(wa["client_data_json"]))
    except Exception:
        return False, "challenge_binding_mismatch"
    provided = client_data.get("challenge")
    if cb.get("mode") == "policy":
        expected = b64url(bytes.fromhex(cb["policy_hash"].replace("sha256:", "")))
    elif cb.get("mode") == "cart":
        ph = bytes.fromhex(cb["policy_hash"].replace("sha256:", ""))
        ch = bytes.fromhex(cb["cart_hash"].replace("sha256:", ""))
        expected = b64url(hashlib.sha256(ph + ch).digest())
    else:
        return False, "challenge_binding_mismatch"
    return (provided == expected), None if provided == expected else "challenge_binding_mismatch"


def _verify_uv(authority: dict):
    """Verify the UV flag in authenticator_data matches the claimed uv."""
    wa = authority.get("webauthn") or {}
    try:
        auth_data = b64url_decode(wa["authenticator_data"])
        uv_flag = (auth_data[32] & 0x04) != 0
    except Exception:
        return False, "uv_flag_mismatch"
    claimed = wa.get("uv")
    if uv_flag != claimed:
        return False, "uv_flag_mismatch"
    return True, None


def _verify_freshness(authority: dict) -> bool:
    """e3: the assertion is recent enough per ``policy.assertion_max_age_seconds``.

    Fail-closed: a missing ``signed_at`` or a missing/zero ``assertion_max_age_seconds``
    yields False (a stale or un-dated assertion must not earn AAL3)."""
    wa = authority.get("webauthn") or {}
    policy = authority.get("policy") or {}
    signed_at = wa.get("signed_at")
    max_age = policy.get("assertion_max_age_seconds", 0)
    if not signed_at or not max_age:
        return False
    try:
        ts = _rfc3339_to_unix(signed_at) if isinstance(signed_at, str) else signed_at
    except Exception:
        return False
    return (time.time() - ts) <= max_age


def verify_native_authority(authority: dict) -> dict:
    """Verify the human-auth leg of a native WebAuthn authority.

    Returns the crypto-verifiable predicates ``e2``/``e3``/``e4``/``e5``.
    Fail-closed: any missing field yields all-False (AAL0)."""
    e2, _ = _verify_webauthn_assertion(authority)
    e4, _ = _verify_uv(authority)
    e5, _ = _verify_challenge_binding(authority)
    e3 = _verify_freshness(authority)
    return {
        "e2_policy_signature_valid": e2,
        "e3_assertion_fresh": e3,
        "e4_user_verified": e4,
        "e5_cart_bound": e5,
    }
