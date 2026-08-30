"""native_webauthn authority (INTEROP_SPEC §3, §4) + WebAuthn stand-in authenticator.

The two simulation helpers below (`build_none_attestation`, `simulate_browser_assertion`)
stand in for a real browser authenticator in tests/RP-less paths. The real ceremony
is verified by `openstore.core.authority.webauthn_rp.WebAuthnRP`; these only produce
the structures it expects. The merchant never signs the human leg.

`verify` is the interop authority verifier for scheme `native_webauthn`: a human's
authenticator signature over this cart hash with UV set is the strongest available
authorization (AAL3).
"""

from __future__ import annotations

import cbor2
import hashlib
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from openstore.canonical import canonical_json_bytes
from openstore.core.types import AuthorityPresentation
from openstore.core.authority import VerifiedAuthority, _all_false
from openstore.core.authority.webauthn_verify import verify_native_authority
from openstore.evidence import b64url, b64url_decode


def _cose_public_key(pub) -> bytes:
    nums = pub.public_numbers()
    return cbor2.dumps({
        1: 2, -1: 1,
        -2: nums.x.to_bytes(32, "big"),
        -3: nums.y.to_bytes(32, "big"),
    })


def build_none_attestation(*, signer_key, rp_id: str, challenge_b64: str,
                           origin: str, credential_id: bytes) -> dict:
    """Produce a `navigator.credentials.create()` response with fmt=none."""
    client_data = {"type": "webauthn.create", "origin": origin, "challenge": challenge_b64}
    client_data_json = b64url(json.dumps(client_data).encode())
    auth_data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([0x41])            # AT + UP flags
        + (0).to_bytes(4, "big")   # signCount
        + (b"\x00" * 16)           # aaguid
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + _cose_public_key(signer_key.public_key())
    )
    attestation_object = b64url(cbor2.dumps({1: "none", 2: auth_data, 3: {}}))
    return {"attestation_object": attestation_object, "client_data_json": client_data_json}


def simulate_browser_assertion(*, rp_id: str, origin: str, challenge_b64: str,
                               policy: dict, cart_hash: str,
                               credential_id: bytes, signer_key) -> dict:
    """Produce a `navigator.credentials.get()` response (none fmt, UV+UP set)."""
    client_data = {"type": "webauthn.get", "origin": origin, "challenge": challenge_b64}
    client_data_json = b64url(json.dumps(client_data).encode())
    auth_data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([0x05])           # UP + UV flags
        + (0).to_bytes(4, "big")
    )
    signed = auth_data + hashlib.sha256(b64url_decode(client_data_json)).digest()
    der = signer_key.sign(signed, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {
        "rawId": b64url(credential_id),
        "response": {
            "clientDataJSON": client_data_json,
            "authenticatorData": b64url(auth_data),
            "signature": signature,
        },
    }


def verify(presentation: AuthorityPresentation) -> VerifiedAuthority:
    p = _all_false()
    raw = dict(presentation.raw)
    webauthn = raw.get("webauthn")
    enrolment = raw.get("enrolment")
    policy = presentation.policy_json
    # e2/e4/e5 are derived by real cryptographic verification (fail-closed): an
    # empty or forged proof set yields AAL0 instead of minting AAL3.
    if webauthn and enrolment and policy is not None:
        res = verify_native_authority(
            {"webauthn": webauthn, "enrolment": enrolment, "policy": policy}
        )
        p["e2_policy_signature_valid"] = res["e2_policy_signature_valid"]
        p["e3_assertion_fresh"] = res["e3_assertion_fresh"]
        p["e4_user_verified"] = res["e4_user_verified"]
        p["e5_cart_bound"] = res["e5_cart_bound"]
    p["e1_agent_authenticated"] = bool(raw.get("authenticated", True))
    p["e6_catalog_attested"] = bool(raw.get("catalog_attested", True))
    p["e7_compiler_allow"] = True
    p["e8_intent_recorded"] = True
    p["e9_notified"] = bool(raw.get("notified", True))
    return VerifiedAuthority(
        scheme="native_webauthn", predicates=p,
        policy_json=presentation.policy_json, presentation=raw,
    )
