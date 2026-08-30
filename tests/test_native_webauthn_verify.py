"""Acceptance tests for SECURITY FIX N1 / C1 (native_webauthn authority).

The original ``native_webauthn.verify`` trusted caller-supplied booleans for the
human-auth predicates (e2/e4/e5). An attacker could present ``raw={}`` with those
booleans set True and mint AAL3. These tests pin the corrected behaviour:

* a real WebAuthnRP assertion (signature + UV + cart-binding verified) yields AAL3
* an empty/missing proof set yields AAL0 (fail-closed)
"""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives.asymmetric import ec

from openstore.aal import Predicates, resolve_aal
from openstore.core.authority import native_webauthn
from openstore.core.authority.webauthn_rp import WebAuthnRP
from openstore.core.types import AuthorityPresentation
from openstore.evidence import b64url, b64url_decode


def _real_authority() -> dict:
    """Run a full real WebAuthn ceremony and return the authority ``raw``."""
    signer_key = ec.generate_private_key(ec.SECP256R1())
    rp = WebAuthnRP(rp_id="localhost", origin="https://localhost")
    cred_id = secrets.token_bytes(32)

    begin_reg = rp.begin_registration()
    att = native_webauthn.build_none_attestation(
        signer_key=signer_key,
        rp_id="localhost",
        challenge_b64=begin_reg["challenge"],
        origin="https://localhost",
        credential_id=cred_id,
    )
    rp.register_credential(
        attestation_object=att["attestation_object"],
        client_data_json=att["client_data_json"],
        session_id=begin_reg["session_id"],
    )

    policy = {"assertion_max_age_seconds": 300}
    cart_hash = "sha256:" + "00" * 32
    begin = rp.begin_assertion(policy, cart_hash)
    browser = native_webauthn.simulate_browser_assertion(
        rp_id="localhost",
        origin="https://localhost",
        challenge_b64=begin["challenge"],
        policy=policy,
        cart_hash=cart_hash,
        credential_id=cred_id,
        signer_key=signer_key,
    )
    return rp.complete_assertion(begin["session_id"], browser, policy=policy, cart_hash=cart_hash)


def test_real_assertion_yields_aal3():
    raw = _real_authority()
    presentation = AuthorityPresentation(
        scheme="native_webauthn", raw=raw, policy_json=raw["policy"]
    )
    va = native_webauthn.verify(presentation)

    assert va.predicates["e2_policy_signature_valid"] is True
    assert va.predicates["e4_user_verified"] is True
    assert va.predicates["e5_cart_bound"] is True
    assert va.predicates["e3_assertion_fresh"] is True
    assert resolve_aal(Predicates(**va.predicates), 2) == (3, ())


def test_empty_raw_fails_closed_to_aal0():
    presentation = AuthorityPresentation(
        scheme="native_webauthn", raw={}, policy_json={"assertion_max_age_seconds": 300}
    )
    va = native_webauthn.verify(presentation)

    assert va.predicates["e2_policy_signature_valid"] is False
    assert va.predicates["e4_user_verified"] is False
    assert va.predicates["e5_cart_bound"] is False
    assert resolve_aal(Predicates(**va.predicates), 2) == (0, ("policy_signature_invalid",))


def test_forged_signature_fails_closed():
    raw = _real_authority()
    # Tamper with the signature: flip a byte so verification must fail.
    wa = dict(raw["webauthn"])
    sig = bytearray(b64url_decode(wa["signature"]))
    sig[0] ^= 0xFF
    wa["signature"] = b64url(bytes(sig))
    tampered = dict(raw)
    tampered["webauthn"] = wa

    presentation = AuthorityPresentation(
        scheme="native_webauthn", raw=tampered, policy_json=raw["policy"]
    )
    va = native_webauthn.verify(presentation)

    assert va.predicates["e2_policy_signature_valid"] is False
    assert resolve_aal(Predicates(**va.predicates), 2)[0] == 0
