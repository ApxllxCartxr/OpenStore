#!/usr/bin/env python3
"""Generate deterministic WebAuthn fixtures under GOLDEN/webauthn/ (Stage 3, S3.4).

Fixtures MUST be produced by real py_webauthn calls, never hand-written JSON
(R0.7). The wire bytes are built by the VirtualAuthenticator (dev tooling); this
script:
  1. Loads (or, on first run, generates and commits) fixed keypairs under
     GOLDEN/webauthn/keys/: EC P-256, RSA-2048, Ed25519.
  2. Uses VirtualAuthenticator to build genuine WebAuthn registration (fmt "none")
     and assertion objects the same way a real authenticator would.
  3. Verifies every positive fixture through py_webauthn's
     verify_registration_response / verify_authentication_response and asserts it
     PASSES before writing output.
  4. Writes GOLDEN/webauthn/*.json containing base64url credential payloads plus
     the metadata the RP needs to replay them.

Run: uv run python scripts/make_webauthn_fixtures.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from webauthn import verify_authentication_response, verify_registration_response
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from openstore.devtools.virtual_authenticator import (
    RP_ID_DEFAULT,
    FLAGS_UP_UV,
    FLAGS_UP_UV_AT,
    VirtualAuthenticator,
    _client_data_json,
    b64u,
    b64u_raw,
    cose_ec2_p256_public,
    cose_ed25519_public,
    cose_rsa_public,
)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "GOLDEN" / "webauthn"
KEYS = GOLDEN / "keys"

RP_ID = RP_ID_DEFAULT
ORIGIN = "https://openstore.test"


def rp_id_hash() -> bytes:
    return __import__("hashlib").sha256(RP_ID.encode()).digest()


def client_data_json(type_: str, challenge: bytes) -> bytes:
    return _client_data_json(type_, challenge, ORIGIN)


def attested_credential_data(credential_id: bytes, cose_public_key: bytes) -> bytes:
    return bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose_public_key


def registration_auth_data(credential_id: bytes, cose_public_key: bytes, sign_count: int) -> bytes:
    return (
        rp_id_hash()
        + bytes([FLAGS_UP_UV_AT])
        + sign_count.to_bytes(4, "big")
        + attested_credential_data(credential_id, cose_public_key)
    )


def assertion_auth_data(sign_count: int) -> bytes:
    return rp_id_hash() + bytes([FLAGS_UP_UV]) + sign_count.to_bytes(4, "big")


def attestation_object(auth_data: bytes) -> bytes:
    import cbor2

    return cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})


def build_registration(
    credential_id: bytes, challenge: bytes, cose_pk: bytes, sign_count: int
) -> tuple[bytes, bytes]:
    client_data = client_data_json("webauthn.create", challenge)
    auth_data = registration_auth_data(credential_id, cose_pk, sign_count)
    return client_data, attestation_object(auth_data)


def sign_es256(private_key, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    return private_key.sign(data, ec.ECDSA(hashes.SHA256()))


def build_assertion(
    credential_id: bytes, challenge: bytes, sign_count: int, private_key, signer
) -> tuple[bytes, bytes, bytes]:
    client_data = client_data_json("webauthn.get", challenge)
    auth_data = assertion_auth_data(sign_count)
    import hashlib

    sig_base = auth_data + hashlib.sha256(client_data).digest()
    signature = signer(private_key, sig_base)
    return client_data, auth_data, signature


def load_or_generate_key(name, generator) -> object:
    der = KEYS / f"{name}.der"
    if der.exists():
        return serialization.load_der_private_key(der.read_bytes(), password=None)
    key = generator()
    der_bytes = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (KEYS / f"{name}.der").write_bytes(der_bytes)
    (KEYS / f"{name}.pem").write_bytes(pem_bytes)
    return key


def keys() -> tuple[object, object, object]:
    KEYS.mkdir(parents=True, exist_ok=True)
    ec_key = load_or_generate_key("ec_p256", lambda: ec.generate_private_key(ec.SECP256R1()))
    rsa_key = load_or_generate_key(
        "rsa_2048", lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    ed_key = load_or_generate_key("ed25519", lambda: ed25519.Ed25519PrivateKey.generate())
    return ec_key, rsa_key, ed_key


def verify_registration_positive(result: object, challenge: str) -> dict:
    verify_registration_response(
        credential={
            "id": result.credential_id,
            "rawId": result.credential_id,
            "response": {
                "clientDataJSON": result.client_data_json,
                "attestationObject": result.attestation_object,
            },
            "type": "public-key",
        },
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        require_user_verification=True,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )
    return {
        "credential_id": result.credential_id,
        "client_data_json": result.client_data_json,
        "attestation_object": result.attestation_object,
        "sign_count": result.sign_count,
        "public_key_cose": result.public_key_cose,
    }


def verify_assertion_positive(result: object, cose_pk: bytes, challenge: str) -> dict:
    verify_authentication_response(
        credential={
            "id": result.credential_id,
            "rawId": result.credential_id,
            "response": {
                "clientDataJSON": result.client_data_json,
                "authenticatorData": result.authenticator_data,
                "signature": result.signature,
            },
            "type": "public-key",
        },
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=cose_pk,
        credential_current_sign_count=result.sign_count - 1,
        require_user_verification=True,
    )
    return {
        "credential_id": result.credential_id,
        "client_data_json": result.client_data_json,
        "authenticator_data": result.authenticator_data,
        "signature": result.signature,
        "sign_count": result.sign_count,
        "public_key_cose": b64u(cose_pk),
    }


def main() -> None:
    ec_key, rsa_key, ed_key = keys()

    challenge_es = b64u_raw("A" * 43)
    challenge_rs = b64u_raw("B" * 43)
    challenge_ed = b64u_raw("C" * 43)
    challenge_wrong = b64u_raw("D" * 43)
    cred_es = b64u_raw("E" + "A" * 42)
    cred_rs = b64u_raw("E" + "B" * 42)
    cred_ed = b64u_raw("E" + "C" * 42)

    va_es = VirtualAuthenticator(
        credential_id=cred_es, key=ec_key, rp_id=RP_ID, origin=ORIGIN, sign_count=3
    )
    va_rs = VirtualAuthenticator(
        credential_id=cred_rs, key=rsa_key, rp_id=RP_ID, origin=ORIGIN, sign_count=1
    )
    va_ed = VirtualAuthenticator(
        credential_id=cred_ed, key=ed_key, rp_id=RP_ID, origin=ORIGIN, sign_count=1
    )

    rs_cose_bytes = (
        VirtualAuthenticator(credential_id=cred_rs, key=rsa_key)
        .register(challenge_rs)
        .public_key_cose
    )
    rs_cose_bytes = b64u_raw(rs_cose_bytes)

    fixtures: dict[str, dict] = {}

    reg_es = verify_registration_positive(va_es.register(challenge_es), challenge_es)
    reg_es.update(label="es256", alg=-7)
    fixtures["registration_es256"] = reg_es

    reg_rs = verify_registration_positive(va_rs.register(challenge_rs), challenge_rs)
    reg_rs.update(label="rs256", alg=-257)
    fixtures["registration_rs256"] = reg_rs

    as_es = verify_assertion_positive(
        va_es.assert_credential(challenge_es, sign_count=4),
        b64u_raw(reg_es["public_key_cose"]),
        challenge_es,
    )
    as_es.update(label="es256", alg=-7)
    fixtures["assertion_es256"] = as_es

    as_rs = verify_assertion_positive(
        va_rs.assert_credential(challenge_rs, sign_count=2),
        rs_cose_bytes,
        challenge_rs,
    )
    as_rs.update(label="rs256", alg=-257)
    fixtures["assertion_rs256"] = as_rs

    # Wrong-challenge assertion (built against challenge_wrong, expected is challenge_es).
    wrong = va_es.assert_credential(challenge_wrong, sign_count=4)
    fixtures["assertion_wrong_challenge"] = {
        "label": "es256-wrong-challenge",
        "credential_id": wrong.credential_id,
        "client_data_json": wrong.client_data_json,
        "authenticator_data": wrong.authenticator_data,
        "signature": wrong.signature,
        "sign_count": 4,
        "expected_challenge": b64u(challenge_es),
        "public_key_cose": reg_es["public_key_cose"],
    }

    # Sign-count regression (stored == received -> must fail monotonicity).
    re = va_es.assert_credential(challenge_es, sign_count=5)
    fixtures["assertion_sign_count_regression"] = {
        "label": "es256-sign-count-regression",
        "credential_id": re.credential_id,
        "client_data_json": re.client_data_json,
        "authenticator_data": re.authenticator_data,
        "signature": re.signature,
        "sign_count": 5,
        "stored_sign_count": 5,
        "public_key_cose": reg_es["public_key_cose"],
    }

    # EdDSA registration (alg -8) -> RP must reject with webauthn_unsupported_alg.
    ed_reg = va_ed.register(challenge_ed)
    fixtures["registration_eddsa_unsupported"] = {
        "label": "eddsa-unsupported",
        "credential_id": ed_reg.credential_id,
        "client_data_json": ed_reg.client_data_json,
        "attestation_object": ed_reg.attestation_object,
        "alg": -8,
        "public_key_cose": ed_reg.public_key_cose,
        "expected_reason_code": "webauthn_unsupported_alg",
    }

    fixtures["_meta"] = {
        "rp_id": RP_ID,
        "origin": ORIGIN,
        "challenges": {
            "es256": b64u(challenge_es),
            "rs256": b64u(challenge_rs),
            "ed25519": b64u(challenge_ed),
        },
        "credentials": {"es256": b64u(cred_es), "rs256": b64u(cred_rs), "ed25519": b64u(cred_ed)},
    }

    for label, payload in fixtures.items():
        (GOLDEN / f"{label}.json").write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

    print(f"wrote {len(fixtures) - 1} fixtures to {GOLDEN}")
    print("all positive fixtures verified through py_webauthn (R0.7)")


if __name__ == "__main__":
    main()
