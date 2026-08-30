# OpenStore core — WebAuthn Relying Party (INV-2, INV-10)

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime
from typing import Any

from sqlmodel import Session, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from openstore.config import Settings
from openstore.models import WebAuthnCredential


class WebAuthnError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


def generate_challenge() -> bytes:
    """Generate cryptographically random challenge."""
    return secrets.token_bytes(32)


def challenge_to_b64url(challenge: bytes) -> str:
    return base64.urlsafe_b64encode(challenge).decode().rstrip("=")


def b64url_to_challenge(b64url: str) -> bytes:
    padding = "=" * (4 - len(b64url) % 4)
    return base64.urlsafe_b64decode(b64url + padding)


def begin_registration(
    config: Settings,
    user_handle: str,
    user_name: str,
    display_name: str,
) -> dict[str, Any]:
    """
    INV-10: Begin WebAuthn registration ceremony.

    Returns options for navigator.credentials.create().
    """
    challenge = generate_challenge()

    selection = AuthenticatorSelectionCriteria(
        authenticator_attachment=AuthenticatorAttachment.PLATFORM,
        resident_key=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    options = generate_registration_options(
        rp_id=config.webauthn.rp_id,
        rp_name=config.webauthn.rp_name,
        user_id=user_handle.encode(),
        user_name=user_name,
        user_display_name=display_name,
        challenge=challenge,
        authenticator_selection=selection,
        attestation=AttestationConveyancePreference.DIRECT,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,  # ES256
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,  # RS256
        ],
        exclude_credentials=[],  # Will be filled from existing credentials
    )

    # Convert to JSON-serializable dict
    return {
        "challenge": challenge_to_b64url(options.challenge),
        "rp": {"id": options.rp.id, "name": options.rp.name},
        "user": {
            "id": base64.urlsafe_b64encode(options.user.id).decode().rstrip("="),
            "name": options.user.name,
            "displayName": options.user.display_name,
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": alg.alg.value} for alg in options.pub_key_cred_params
        ],
        "authenticatorSelection": {
            "authenticatorAttachment": selection.authenticator_attachment.value if selection.authenticator_attachment else None,
            "residentKey": selection.resident_key.value if selection.resident_key else None,
            "userVerification": selection.user_verification.value if selection.user_verification else None,
        },
        "attestation": options.attestation.value,
        "excludeCredentials": [
            {
                "id": base64.urlsafe_b64encode(c.id).decode().rstrip("="),
                "type": c.type.value,
                "transports": [t.value for t in (c.transports or [])],
            }
            for c in (options.exclude_credentials or [])
        ],
    }


def complete_registration(
    session: Session,
    config: Settings,
    user_handle: str,
    credential_id: str,
    client_data_json: str,
    attestation_object: str,
    challenge_b64url: str,
) -> WebAuthnCredential:
    """
    INV-10: Complete WebAuthn registration ceremony.

    Verifies attestation and stores credential.
    """
    challenge = b64url_to_challenge(challenge_b64url)

    try:
        verification = verify_registration_response(
            credential={
                "id": credential_id,
                "rawId": base64.urlsafe_b64decode(credential_id + "=="),
                "response": {
                    "clientDataJSON": base64.urlsafe_b64decode(client_data_json + "=="),
                    "attestationObject": base64.urlsafe_b64decode(attestation_object + "=="),
                },
                "type": "public-key",
            },
            expected_challenge=challenge,
            expected_rp_id=config.webauthn.rp_id,
            expected_origin=config.webauthn.origin,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as e:
        raise WebAuthnError("webauthn_registration_failed", str(e))

    # Store credential
    credential = WebAuthnCredential(
        credential_id=credential_id,
        user_handle=user_handle,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        attestation_format=verification.fmt.value if verification.fmt else None,
        attestation_data={
            "credential_type": verification.credential_type.value if verification.credential_type else None,
            "user_verified": verification.user_verified,
            "attestation_object": base64.urlsafe_b64encode(verification.attestation_object).decode().rstrip("=") if verification.attestation_object else None,
        },
        is_active=True,
    )

    session.add(credential)
    session.flush()

    return credential


def begin_assertion(
    config: Settings,
    user_handle: str,
) -> dict[str, Any]:
    """
    INV-2: Begin WebAuthn assertion ceremony (for policy signing).

    Returns options for navigator.credentials.get().
    """
    challenge = generate_challenge()

    # Get allowed credentials for this user
    # In practice, this would query the database
    allow_credentials: list[PublicKeyCredentialDescriptor] = []

    options = generate_authentication_options(
        rp_id=config.webauthn.rp_id,
        challenge=challenge,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    return {
        "challenge": challenge_to_b64url(options.challenge),
        "rpId": options.rp_id,
        "allowCredentials": [
            {
                "id": base64.urlsafe_b64encode(c.id).decode().rstrip("="),
                "type": c.type.value,
                "transports": [t.value for t in (c.transports or [])],
            }
            for c in (options.allow_credentials or [])
        ],
        "userVerification": options.user_verification.value if options.user_verification else None,
    }


def complete_assertion(
    session: Session,
    config: Settings,
    user_handle: str,
    credential_id: str,
    client_data_json: str,
    authenticator_data: str,
    signature: str,
    challenge_b64url: str,
) -> tuple[bool, int]:
    """
    INV-2: Complete WebAuthn assertion ceremony (verify policy signature).

    Returns (verified, new_sign_count).
    """
    challenge = b64url_to_challenge(challenge_b64url)

    # Get stored credential
    credential = session.exec(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == credential_id,
            WebAuthnCredential.user_handle == user_handle,
            WebAuthnCredential.is_active.is_(True),  # type: ignore[attr-defined]
        )
    ).first()

    if not credential:
        raise WebAuthnError("webauthn_credential_not_found", "Credential not found")

    try:
        verification = verify_authentication_response(
            credential={
                "id": credential_id,
                "rawId": base64.urlsafe_b64decode(credential_id + "=="),
                "response": {
                    "clientDataJSON": base64.urlsafe_b64decode(client_data_json + "=="),
                    "authenticatorData": base64.urlsafe_b64decode(authenticator_data + "=="),
                    "signature": base64.urlsafe_b64decode(signature + "=="),
                },
                "type": "public-key",
            },
            expected_challenge=challenge,
            expected_rp_id=config.webauthn.rp_id,
            expected_origin=config.webauthn.origin,
            credential_public_key=credential.public_key,
            credential_current_sign_count=credential.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as e:
        raise WebAuthnError("webauthn_assertion_failed", str(e))

    # Update sign count
    credential.sign_count = verification.new_sign_count
    credential.last_used_at = datetime.utcnow()
    session.add(credential)
    session.flush()

    return True, verification.new_sign_count


def get_user_credentials(session: Session, user_handle: str) -> list[WebAuthnCredential]:
    """Get all active credentials for a user."""
    return list(session.exec(
        select(WebAuthnCredential).where(
            WebAuthnCredential.user_handle == user_handle,
            WebAuthnCredential.is_active.is_(True),  # type: ignore[attr-defined]
        )
    ).all())


def create_policy_signing_challenge(
    config: Settings,
    policy_id: str,
    policy_hash: str,
    merchant_id: str,
) -> dict[str, Any]:
    """
    Create challenge for policy signing ceremony.

    The challenge binds to the specific policy being signed.
    """
    # Challenge includes policy hash for binding
    challenge_data = {
        "type": "policy_signing",
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "merchant_id": merchant_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "nonce": secrets.token_hex(16),
    }

    challenge_bytes = hashlib.sha256(json.dumps(challenge_data, sort_keys=True).encode()).digest()

    return {
        "challenge": challenge_to_b64url(challenge_bytes),
        "challenge_data": challenge_data,
    }
