import base64
import hashlib

from fido2.server import Fido2Server, websafe_decode
from fido2.webauthn import (
    AttestedCredentialData,
    Aaguid,
    AuthenticationResponse,
    AuthenticatorAssertionResponse,
    CollectedClientData,
    AuthenticatorData,
    PublicKeyCredentialRpEntity,
)
from fido2.cose import CoseKey
import fido2.cbor as cbor

from reference.merchant.mandate import canonical_json_bytes

rp = PublicKeyCredentialRpEntity(id="localhost", name="OpenStore Merchant")
fido_server = Fido2Server(rp)


def b64url_decode(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def build_credential(credential_id_b64: str, public_key_b64: str) -> AttestedCredentialData:
    cred_id_raw = b64url_decode(credential_id_b64)
    pk_cbor = b64url_decode(public_key_b64)
    pk_dict = cbor.decode(pk_cbor)
    public_key = CoseKey.parse(pk_dict)
    return AttestedCredentialData.create(Aaguid.NONE, cred_id_raw, public_key)


def verify_assertion_policy(
    credential_id_b64: str,
    public_key_b64: str,
    assertion_b64: dict,
    policy_json: dict,
    nonce_to_policy_hash: dict[str, str],
) -> bool:
    """Verify a WebAuthn assertion and confirm the signed nonce maps to the
    expected policy hash.

    Args:
        credential_id_b64: base64url credential ID (matches assertion["rawId"])
        public_key_b64: base64url COSE public key
        assertion_b64: browser assertion dict with base64url string values:
            {id, rawId, response: {clientDataJSON, authenticatorData, signature}, type}
        policy_json: the IntentPolicy dict the agent claims was signed
        nonce_to_policy_hash: mapping nonce_b64 -> SHA-256 hex of canonical policy

    Returns:
        True if valid.

    Raises:
        ValueError with a descriptive message on any failure.
    """
    credential = build_credential(credential_id_b64, public_key_b64)

    # Reconstruct AssertionResponse from base64url strings
    resp = assertion_b64["response"]
    client_data = CollectedClientData(b64url_decode(resp["clientDataJSON"]))
    auth_data = AuthenticatorData(b64url_decode(resp["authenticatorData"]))
    signature = b64url_decode(resp["signature"])
    raw_id = b64url_decode(assertion_b64.get("rawId", credential_id_b64))

    assertion = AuthenticationResponse(
        raw_id=raw_id,
        response=AuthenticatorAssertionResponse(
            client_data=client_data,
            authenticator_data=auth_data,
            signature=signature,
        ),
    )

    # Verify signature
    if credential.credential_id != assertion.raw_id:
        raise ValueError("Credential ID mismatch")
    try:
        credential.public_key.verify(
            auth_data + client_data.hash, assertion.response.signature
        )
    except Exception:
        raise ValueError("Invalid assertion signature")

    # Bind policy: extract nonce from clientData, verify it maps to the policy hash
    nonce_bytes = assertion.response.client_data.challenge
    nonce_str = b64url_encode(nonce_bytes)

    expected_hash = nonce_to_policy_hash.get(nonce_str)
    if expected_hash is None:
        raise ValueError("Unknown nonce — no policy bound to this challenge")

    policy_canonical = canonical_json_bytes(policy_json)
    actual_hash = hashlib.sha256(policy_canonical).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            "Policy hash mismatch — assertion was signed for a different policy"
        )

    return True
