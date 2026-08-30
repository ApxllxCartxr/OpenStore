#!/usr/bin/env python3
"""Generate deterministic WebAuthn fixtures under GOLDEN/webauthn/ (Stage 3, S3.4).

Fixtures MUST be produced by real py_webauthn calls, never hand-written JSON
(R0.7). This script:
  1. Loads (or, on first run, generates and commits) fixed keypairs under
     GOLDEN/webauthn/keys/: EC P-256, RSA-2048, Ed25519.
  2. Builds genuine WebAuthn registration (fmt "none") and assertion objects the
     same way a real authenticator would, using cbor2 + cryptography.
  3. Verifies every positive fixture through py_webauthn's
     verify_registration_response / verify_authentication_response and asserts it
     PASSES before writing output.
  4. Writes GOLDEN/webauthn/*.json containing base64url credential payloads plus
     the metadata the RP needs to replay them.

Run: uv run python scripts/make_webauthn_fixtures.py
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import cbor2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric import padding
from webauthn import verify_authentication_response, verify_registration_response
from webauthn.helpers.cose import COSEAlgorithmIdentifier, COSEKey

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "GOLDEN" / "webauthn"
KEYS = GOLDEN / "keys"

RP_ID = "openstore.test"
ORIGIN = "https://openstore.test"

KTY, ALG, CRV, X, Y, N, E = (COSEKey.KTY, COSEKey.ALG, COSEKey.CRV, COSEKey.X,
                             COSEKey.Y, COSEKey.N, COSEKey.E)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_raw(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def rp_id_hash() -> bytes:
    return hashlib.sha256(RP_ID.encode()).digest()


FLAGS_UP_UV_AT = 0x01 | 0x04 | 0x40  # UP | UV | attested-credential-data (registration)
FLAGS_UP_UV = 0x01 | 0x04  # UP | UV (assertion)


def client_data_json(type_: str, challenge: bytes) -> bytes:
    payload = {"type": type_, "challenge": b64u(challenge), "origin": ORIGIN, "crossOrigin": False}
    return json.dumps(payload, separators=(",", ":")).encode()


def attested_credential_data(credential_id: bytes, cose_public_key: bytes) -> bytes:
    aaguid = bytes(16)
    cred_id_len = len(credential_id).to_bytes(2, "big")
    return aaguid + cred_id_len + credential_id + cose_public_key


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
    return cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})


def cose_ec2_p256_public(private_key, alg: int = -7) -> bytes:
    nums = private_key.public_key().public_numbers()
    size = ec.SECP256R1().key_size // 8
    return cbor2.dumps({KTY: 2, ALG: alg, CRV: 1, X: nums.x.to_bytes(size, "big"),
                        Y: nums.y.to_bytes(size, "big")})


def cose_rsa_public(private_key, alg: int = -257) -> bytes:
    nums = private_key.public_key().public_numbers()
    n_len = (nums.n.bit_length() + 7) // 8
    e_len = (nums.e.bit_length() + 7) // 8
    return cbor2.dumps({KTY: 3, ALG: alg, N: nums.n.to_bytes(n_len, "big"),
                        E: nums.e.to_bytes(e_len, "big")})


def cose_ed25519_public(private_key, alg: int = -8) -> bytes:
    return cbor2.dumps({KTY: 1, ALG: alg, CRV: 6, X: private_key.public_key().public_bytes_raw()})


def sign_es256(private_key, data: bytes) -> bytes:
    return private_key.sign(data, ec.ECDSA(hashes.SHA256()))


def sign_rs256(private_key, data: bytes) -> bytes:
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def sign_ed25519(private_key, data: bytes) -> bytes:
    return private_key.sign(data)


def build_registration(credential_id: bytes, challenge: bytes, cose_pk: bytes, sign_count: int) -> tuple[bytes, bytes]:
    client_data = client_data_json("webauthn.create", challenge)
    auth_data = registration_auth_data(credential_id, cose_pk, sign_count)
    return client_data, attestation_object(auth_data)


def build_assertion(credential_id: bytes, challenge: bytes, sign_count: int,
                    private_key, signer) -> tuple[bytes, bytes, bytes]:
    client_data = client_data_json("webauthn.get", challenge)
    auth_data = assertion_auth_data(sign_count)
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
    rsa_key = load_or_generate_key("rsa_2048", lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048))
    ed_key = load_or_generate_key("ed25519", lambda: ed25519.Ed25519PrivateKey.generate())
    return ec_key, rsa_key, ed_key


def verify_registration_positive(cred_id: bytes, challenge: bytes, cose_pk: bytes,
                                 sign_count: int) -> dict:
    client_data, att = build_registration(cred_id, challenge, cose_pk, sign_count)
    ver = verify_registration_response(
        credential={
            "id": b64u(cred_id), "rawId": b64u(cred_id),
            "response": {"clientDataJSON": b64u(client_data), "attestationObject": b64u(att)},
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
    assert ver.user_verified is True
    assert ver.sign_count == sign_count
    return {"credential_id": b64u(cred_id), "client_data_json": b64u(client_data),
            "attestation_object": b64u(att), "sign_count": sign_count,
            "public_key_cose": b64u(cose_pk)}


def verify_assertion_positive(cred_id: bytes, challenge: bytes, sign_count: int,
                              private_key, cose_pk, signer) -> dict:
    client_data, auth_data, signature = build_assertion(cred_id, challenge, sign_count, private_key, signer)
    verify_authentication_response(
        credential={
            "id": b64u(cred_id), "rawId": b64u(cred_id),
            "response": {"clientDataJSON": b64u(client_data), "authenticatorData": b64u(auth_data),
                         "signature": b64u(signature)},
            "type": "public-key",
        },
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=cose_pk,
        credential_current_sign_count=sign_count - 1,
        require_user_verification=True,
    )
    return {"credential_id": b64u(cred_id), "client_data_json": b64u(client_data),
            "authenticator_data": b64u(auth_data), "signature": b64u(signature),
            "sign_count": sign_count, "public_key_cose": b64u(cose_pk)}


def main() -> None:
    ec_key, rsa_key, ed_key = keys()

    challenge_es = b64u_raw("A" * 43)
    challenge_rs = b64u_raw("B" * 43)
    challenge_ed = b64u_raw("C" * 43)
    challenge_wrong = b64u_raw("D" * 43)
    cred_es = b64u_raw("E" + "A" * 42)
    cred_rs = b64u_raw("E" + "B" * 42)
    cred_ed = b64u_raw("E" + "C" * 42)

    fixtures: dict[str, dict] = {}

    es_cose = cose_ec2_p256_public(ec_key, -7)
    rs_cose = cose_rsa_public(rsa_key, -257)
    ed_cose = cose_ed25519_public(ed_key, -8)

    # Positive registration fixtures (verified through py_webauthn).
    reg_es = verify_registration_positive(cred_es, challenge_es, es_cose, 3)
    reg_es["label"] = "es256"
    reg_es["alg"] = -7
    fixtures["registration_es256"] = reg_es

    reg_rs = verify_registration_positive(cred_rs, challenge_rs, rs_cose, 1)
    reg_rs["label"] = "rs256"
    reg_rs["alg"] = -257
    fixtures["registration_rs256"] = reg_rs

    # Positive assertion fixtures (verified through py_webauthn).
    as_es = verify_assertion_positive(cred_es, challenge_es, 4, ec_key, es_cose, sign_es256)
    as_es["label"] = "es256"
    as_es["alg"] = -7
    fixtures["assertion_es256"] = as_es

    as_rs = verify_assertion_positive(cred_rs, challenge_rs, 2, rsa_key, rs_cose, sign_rs256)
    as_rs["label"] = "rs256"
    as_rs["alg"] = -257
    fixtures["assertion_rs256"] = as_rs

    # Wrong-challenge assertion (built against challenge_wrong, expected_challenge
    # is challenge_es) -> RP must reject.
    cd_w, auth_w, sig_w = build_assertion(cred_es, challenge_wrong, 4, ec_key, sign_es256)
    fixtures["assertion_wrong_challenge"] = {
        "label": "es256-wrong-challenge",
        "credential_id": b64u(cred_es),
        "client_data_json": b64u(cd_w),
        "authenticator_data": b64u(auth_w),
        "signature": b64u(sig_w),
        "sign_count": 4,
        "expected_challenge": b64u(challenge_es),
        "public_key_cose": b64u(es_cose),
    }

    # Sign-count regression (stored == received -> must fail monotonicity).
    cd_re, auth_re, sig_re = build_assertion(cred_es, challenge_es, 5, ec_key, sign_es256)
    fixtures["assertion_sign_count_regression"] = {
        "label": "es256-sign-count-regression",
        "credential_id": b64u(cred_es),
        "client_data_json": b64u(cd_re),
        "authenticator_data": b64u(auth_re),
        "signature": b64u(sig_re),
        "sign_count": 5,
        "stored_sign_count": 5,
        "public_key_cose": b64u(es_cose),
    }

    # EdDSA registration (alg -8) -> RP must reject with webauthn_unsupported_alg.
    cd_ed, att_ed = build_registration(cred_ed, challenge_ed, ed_cose, 1)
    fixtures["registration_eddsa_unsupported"] = {
        "label": "eddsa-unsupported",
        "credential_id": b64u(cred_ed),
        "client_data_json": b64u(cd_ed),
        "attestation_object": b64u(att_ed),
        "alg": -8,
        "public_key_cose": b64u(ed_cose),
        "expected_reason_code": "webauthn_unsupported_alg",
    }

    fixtures["_meta"] = {
        "rp_id": RP_ID,
        "origin": ORIGIN,
        "challenges": {"es256": b64u(challenge_es), "rs256": b64u(challenge_rs), "ed25519": b64u(challenge_ed)},
        "credentials": {"es256": b64u(cred_es), "rs256": b64u(cred_rs), "ed25519": b64u(cred_ed)},
    }

    for label, payload in fixtures.items():
        (GOLDEN / f"{label}.json").write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

    print(f"wrote {len(fixtures) - 1} fixtures to {GOLDEN}")
    print("all positive fixtures verified through py_webauthn (R0.7)")


if __name__ == "__main__":
    main()
