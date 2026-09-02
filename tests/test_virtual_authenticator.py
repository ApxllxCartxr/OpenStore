# tests/test_virtual_authenticator.py
# Unit tests for the dev VirtualAuthenticator (S3.3): real py_webauthn-
# verifiable registration + assertion ceremonies with EC P-256 (ES256).

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from openstore.devtools.virtual_authenticator import (
    VirtualAuthenticator,
    generate_es256_authenticator,
)

RP_ID = "openstore.test"
ORIGIN = "https://openstore.test"


def _b64d(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def _verify_registration(reg, challenge: bytes) -> None:
    from webauthn import verify_registration_response

    ver = verify_registration_response(
        credential={
            "id": reg.credential_id,
            "rawId": reg.credential_id,
            "response": {
                "clientDataJSON": reg.client_data_json,
                "attestationObject": reg.attestation_object,
            },
            "type": "public-key",
        },
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        require_user_verification=True,
    )
    assert ver.user_verified is True


def _verify_assertion(asr, challenge: bytes, cose_pk: bytes) -> None:
    from webauthn import verify_authentication_response

    verify_authentication_response(
        credential={
            "id": asr.credential_id,
            "rawId": asr.credential_id,
            "response": {
                "clientDataJSON": asr.client_data_json,
                "authenticatorData": asr.authenticator_data,
                "signature": asr.signature,
            },
            "type": "public-key",
        },
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=cose_pk,
        credential_current_sign_count=asr.sign_count - 1,
        require_user_verification=True,
    )


def test_default_authenticator_is_es256_p256():
    va = generate_es256_authenticator()
    assert isinstance(va.key, ec.EllipticCurvePrivateKey)
    assert va.key.curve.name == "secp256r1"


def test_registration_verifies_and_assigns_credential():
    key = ec.generate_private_key(ec.SECP256R1())
    va = VirtualAuthenticator(
        credential_id=b"C" * 32, key=key, rp_id=RP_ID, origin=ORIGIN, sign_count=3
    )
    challenge = b"A" * 32
    reg = va.register(challenge)
    _verify_registration(reg, challenge)
    assert reg.sign_count == 3


def test_assertion_verifies_and_increments_sign_count():
    key = ec.generate_private_key(ec.SECP256R1())
    va = VirtualAuthenticator(
        credential_id=b"C" * 32, key=key, rp_id=RP_ID, origin=ORIGIN, sign_count=3
    )
    reg = va.register(b"A" * 32)
    challenge = b"B" * 32
    asr = va.assert_credential(challenge, sign_count=4)
    _verify_assertion(asr, challenge, _b64d(reg.public_key_cose))
    assert asr.sign_count == 4


def test_uv_flag_is_set_in_auth_data():
    key = ec.generate_private_key(ec.SECP256R1())
    va = VirtualAuthenticator(credential_id=b"C" * 32, key=key, rp_id=RP_ID, origin=ORIGIN)
    asr = va.assert_credential(b"Z" * 32)
    auth = _b64d(asr.authenticator_data)
    flags = auth[32]
    assert flags & 0x04  # user_verification
    assert flags & 0x01  # user present


def test_wrong_challenge_rejected():
    key = ec.generate_private_key(ec.SECP256R1())
    va = VirtualAuthenticator(
        credential_id=b"C" * 32, key=key, rp_id=RP_ID, origin=ORIGIN, sign_count=3
    )
    reg = va.register(b"A" * 32)
    asr = va.assert_credential(b"B" * 32, sign_count=4)
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    with pytest.raises(InvalidAuthenticationResponse):
        _verify_assertion(asr, b"C" * 32, _b64d(reg.public_key_cose))
