# OpenStore core — WebAuthn Relying Party (INV-2, INV-10; PRD §3.5 e2/e4; S3.3)
#
# Registers credentials and verifies assertions using py_webauthn (pinned). Per
# S3.3:
#   * Algorithms: COSE ES256 (-7) and RS256 (-257) ONLY. Any other COSE alg is a
#     hard error `webauthn_unsupported_alg` (R0.5). EdDSA (-8) is out of scope
#     (DECISIONS §11.1.4).
#   * Single-use challenge store with TTL (module constant CHALLENGE_TTL_SECONDS;
#     config.py is out of scope for this stage). Every challenge carries a
#     challenge_binding: {"mode": "policy"} at policy signing,
#     {"mode": "cart", "cart_hash": ...} at checkout, or (S11 Phase 4, Q-020)
#     {"mode": "amendment", "amendment_id": ...} at amendment approval,
#     verified on completion.
#   * UV flag (bit 0x04 in the flags byte at index 32 of authenticator_data) is
#     mandatory for assertions.
#   * Sign-count monotonicity: stored and received both 0 -> accept (counter-less
#     authenticator); received != 0 and received <= stored -> hard error.
#   * Fail loud everywhere (R0.5). All rejections carry only closed-set reason
#     codes from REGISTRY.json. The WebAuthn-specific code is
#     `webauthn_unsupported_alg`; every other RP rejection (bad signature, wrong
#     or expired challenge, credential not found, UV flag missing, sign-count
#     regression) maps to the compiler's `assertion_required` (author decision,
#     OPEN_QUESTIONS; REGISTRY has no finer WebAuthn codes and adding them is
#     forbidden by AGENTS.md R0.2).
#   * Q-005 AMENDMENT (2026-09-01): each assertion rejection additionally
#     carries a precise `failure_type` — one of assertion_signature_invalid |
#     challenge_mismatch | challenge_expired | challenge_reused |
#     sign_count_regression | credential_not_found | uv_flag_missing — so the
#     evidence trail (AuditLogEntry detail + #alerts) never flattens security-
#     relevant failures (especially sign_count_regression). These strings are
#     local to the audit detail and are NOT REGISTRY reason_codes (R0.2 still
#     binds; new reason codes remain RESOLUTION material).
#   * No LLM imports. user_id comes from the session, never the request body
#     (INV-10).

from __future__ import annotations

import base64
import secrets
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import decode_credential_public_key, parse_attestation_object
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from openstore.config import Settings
from openstore.models import WebAuthnCredential

# Closed-set reason codes this module is permitted to raise (REGISTRY.json).
_WEBAUTHN_UNSUPPORTED_ALG = "webauthn_unsupported_alg"
_ASSERTION_REQUIRED = "assertion_required"

# Q-005 AMENDMENT: precise failure types for the audit trail / #alerts. Local to
# the audit detail and the alerts trace — NOT REGISTRY reason_codes (R0.2 binds;
# AGENTS.md forbids adding identifiers without a RESOLUTION).
assertion_signature_invalid = "assertion_signature_invalid"
challenge_mismatch = "challenge_mismatch"
challenge_expired = "challenge_expired"
challenge_reused = "challenge_reused"
sign_count_regression = "sign_count_regression"
credential_not_found = "credential_not_found"
uv_flag_missing = "uv_flag_missing"

_FAILURE_TYPES = frozenset(
    {
        assertion_signature_invalid,
        challenge_mismatch,
        challenge_expired,
        challenge_reused,
        sign_count_regression,
        credential_not_found,
        uv_flag_missing,
    }
)

# Supported COSE algorithms (DECISIONS §11.1.4).
_SUPPORTED_ALGS = frozenset(
    {COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256}
)

# Challenge TTL. Module constant (S3.3); config key `challenge_ttl_seconds = 120`
# is in REGISTRY/PRD but config.py is out of scope for this stage.
CHALLENGE_TTL_SECONDS = 120

# UV flag bit in the authenticator-data flags byte (CTAP2).
_UV_FLAG = 0x04
_AUTH_DATA_FLAGS_BYTE = 32  # rpIdHash(32) + flags(1) + signCount(4) -> flags at index 32


class WebAuthnError(Exception):
    """RP rejection. reason_code is always a closed-set REGISTRY value;
    failure_type, when set, is one of the Q-005 AMENDMENT audit types (local to
    the audit detail / #alerts, never a REGISTRY reason code)."""

    def __init__(self, reason_code: str, message: str, failure_type: str | None = None):
        self.reason_code = reason_code
        self.message = message
        if failure_type is not None and failure_type not in _FAILURE_TYPES:
            raise ValueError(f"unknown failure_type: {failure_type}")
        self.failure_type = failure_type
        super().__init__(f"[{reason_code}] {message}")


# ---------------------------------------------------------------------------
# Single-use challenge store (S3.3)
# ---------------------------------------------------------------------------
class ChallengeStore:
    """In-memory, single-use, TTL-bounded challenge store.

    Single-tenant per sidecar process (AGENTS.md: Currency INR, single-tenant).
    Keyed by challenge base64url. Each entry carries its `challenge_binding` and
    an `issued_at` Unix timestamp. A challenge can be consumed exactly once; a
    reused, expired, or unknown challenge is rejected.
    """

    def __init__(self, ttl_seconds: int = CHALLENGE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def issue(self, challenge_b64url: str, binding: dict[str, Any]) -> None:
        with self._lock:
            self._prune_expired()
            self._entries[challenge_b64url] = {
                "binding": binding,
                "issued_at": time.time(),
                "used": False,
            }

    def consume(self, challenge_b64url: str) -> dict[str, Any]:
        """Validate and consume a single-use challenge.

        Raises WebAuthnError(assertion_required) on unknown, reused, or expired
        challenge (R0.5: no silent default; single use is enforced).

        A consumed challenge stays in the map flagged `used` so that a later
        presentation is distinguishable as a reuse rather than an unknown
        challenge (Q-005 AMENDMENT: the audit trail must record the precise
        failure type — challenge_reused — for the replay signal). Stale entries
        are pruned on the next issue.
        """
        now = time.time()
        with self._lock:
            entry = self._entries.get(challenge_b64url)
            if entry is None:
                raise WebAuthnError(
                    _ASSERTION_REQUIRED, "unknown challenge", failure_type=challenge_mismatch
                )
            if entry["used"]:
                raise WebAuthnError(
                    _ASSERTION_REQUIRED, "challenge already used", failure_type=challenge_reused
                )
            if now - entry["issued_at"] > self._ttl_seconds:
                del self._entries[challenge_b64url]
                raise WebAuthnError(
                    _ASSERTION_REQUIRED, "challenge expired", failure_type=challenge_expired
                )
            entry["used"] = True
            return entry

    def _prune_expired(self) -> None:
        now = time.time()
        stale = [k for k, e in self._entries.items() if now - e["issued_at"] > self._ttl_seconds]
        for k in stale:
            del self._entries[k]


# Process-wide store. Prefer passing an explicit store for tests; default to the
# singleton so the ceremony (begin -> complete) shares state within one process.
_DEFAULT_STORE = ChallengeStore()


# ---------------------------------------------------------------------------
# Chalenge / b64url helpers
# ---------------------------------------------------------------------------
def generate_challenge() -> bytes:
    """Generate a cryptographically random 32-byte challenge."""
    return secrets.token_bytes(32)


def challenge_to_b64url(challenge: bytes) -> str:
    return base64.urlsafe_b64encode(challenge).decode().rstrip("=")


def b64url_to_challenge(b64url: str) -> bytes:
    padding = "=" * (4 - len(b64url) % 4)
    return base64.urlsafe_b64decode(b64url + padding)


def _decode_auth_data_flags_sign_count(authenticator_data: bytes) -> tuple[int, int]:
    """Return (flags_byte, sign_count) from authenticator_data (prd §3.5 e4/e7,
    §3.6 check 7). Layout: rpIdHash(32) + flags(1) + signCount(4)."""
    if len(authenticator_data) < 37:
        raise WebAuthnError(
            _ASSERTION_REQUIRED,
            "authenticator_data too short",
            failure_type=assertion_signature_invalid,
        )
    flags = authenticator_data[_AUTH_DATA_FLAGS_BYTE]
    sign_count = int.from_bytes(
        authenticator_data[_AUTH_DATA_FLAGS_BYTE + 1 : _AUTH_DATA_FLAGS_BYTE + 5], "big"
    )
    return flags, sign_count


def _binding_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare two challenge_binding dicts (PRD §3.5 e5).

    The binding contract is the `mode` plus, for cart mode, the `cart_hash`
    (e5: `challenge_binding.mode == "cart"` and its `cart_hash == goods.cart_hash`).
    Policy mode carries only the mode; any extra metadata (policy_id, policy_hash
    stored by create_policy_signing_challenge) is contextual, not part of the
    binding contract. `amendment` mode (S11 Phase 4, Q-020) mirrors `cart` mode
    exactly but keys on `amendment_id` instead of `cart_hash`: the buyer's
    approval assertion must be bound to the specific drafted amendment, not
    just "some amendment", so a stale or mismatched challenge can never
    authorize a different amendment (R0.5). `campaign` mode (DECISION-024) keys
    on `campaign_id` for the same reason: an approval for one campaign must not
    be spendable on another. Unknown modes never match (R0.3: unknown value =>
    hard error, never a silent pass).
    """
    expected_mode = expected.get("mode")
    actual_mode = actual.get("mode")
    if expected_mode is None or actual_mode is None:
        return False
    if expected_mode != actual_mode:
        return False
    if expected_mode == "cart":
        return expected.get("cart_hash") is not None and expected.get("cart_hash") == actual.get(
            "cart_hash"
        )
    if expected_mode == "amendment":
        return expected.get("amendment_id") is not None and expected.get(
            "amendment_id"
        ) == actual.get("amendment_id")
    if expected_mode == "campaign":
        return expected.get("campaign_id") is not None and expected.get(
            "campaign_id"
        ) == actual.get("campaign_id")
    if expected_mode in ("policy", "merchant-login"):
        return True
    return False


# ---------------------------------------------------------------------------
# COSE algorithm gate (R0.5/R0.7, DECISIONS §11.1.4)
# ---------------------------------------------------------------------------
def _cose_alg_of(public_key_cose: bytes) -> int:
    try:
        cose_key = decode_credential_public_key(public_key_cose)
    except Exception as e:  # malformed COSE -> hard error, never coerced
        raise WebAuthnError(_WEBAUTHN_UNSUPPORTED_ALG, f"undecodable COSE public key: {e}")
    alg = getattr(cose_key, "alg", None)
    if alg is None:
        raise WebAuthnError(_WEBAUTHN_UNSUPPORTED_ALG, "COSE key has no alg")
    return int(alg)


def _enforce_supported_alg(public_key_cose: bytes) -> None:
    alg = _cose_alg_of(public_key_cose)
    if alg not in _SUPPORTED_ALGS:
        raise WebAuthnError(_WEBAUTHN_UNSUPPORTED_ALG, f"unsupported COSE algorithm {alg}")


# ---------------------------------------------------------------------------
# Enrolment (INV-10)
# ---------------------------------------------------------------------------
def begin_registration(
    config: Settings,
    user_handle: str,
    user_name: str,
    display_name: str,
    store: ChallengeStore | None = None,
) -> dict[str, Any]:
    """Begin the registration ceremony (INV-10). challenge is bound to nothing
    specific to enrolment but is still single-use via the store."""
    challenge = generate_challenge()
    selection = AuthenticatorSelectionCriteria(
        # No authenticator_attachment restriction: PLATFORM previously forced
        # a built-in-to-this-device authenticator only, which Chrome enforces
        # client-side by refusing the cross-device/QR (hybrid) flow entirely
        # ("Your device can't be used with this site") — excluding the
        # ordinary case of registering a phone-held passkey against a desktop
        # browser. begin_assertion (below) never set this restriction either.
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
        # NONE, not DIRECT: real-world passkey providers (Android Google
        # Password Manager, iCloud Keychain, and most platform authenticators)
        # refuse to produce a direct attestation statement and surface it to
        # the user as an opaque "Your device can't be used with this site"
        # failure at registration time. DIRECT bought nothing here — no PRD
        # text or REGISTRY entry pins attestation conveyance, and
        # attestation_format is still recorded from whatever statement the
        # authenticator actually returns.
        attestation=AttestationConveyancePreference.NONE,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
        exclude_credentials=[],
    )
    challenge_b64 = challenge_to_b64url(options.challenge)
    (store or _DEFAULT_STORE).issue(challenge_b64, {"mode": "policy"})
    return {
        "challenge": challenge_b64,
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
            "authenticatorAttachment": selection.authenticator_attachment.value
            if selection.authenticator_attachment
            else None,
            "residentKey": selection.resident_key.value if selection.resident_key else None,
            "userVerification": selection.user_verification.value
            if selection.user_verification
            else None,
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
    store: ChallengeStore | None = None,
) -> WebAuthnCredential:
    """Complete the registration ceremony (INV-10). user_id (user_handle) comes
    from the session, never the request body. Public key alg must be ES256 or
    RS256 (R0.5); anything else -> webauthn_unsupported_alg."""
    (store or _DEFAULT_STORE).consume(challenge_b64url)

    att_obj = base64.urlsafe_b64decode(attestation_object + "=" * (-len(attestation_object) % 4))

    # Extract the COSE public key from the attestation and pre-check the
    # algorithm before the full verification (R0.5: hard error, never silently
    # downgraded by py_webauthn's own unsupported-alg path).
    try:
        parsed = parse_attestation_object(att_obj)
        attested = parsed.auth_data.attested_credential_data
        if attested is None:
            raise WebAuthnError(_WEBAUTHN_UNSUPPORTED_ALG, "attestation has no credential data")
        cred_pub = attested.credential_public_key
    except Exception as e:
        raise WebAuthnError(_WEBAUTHN_UNSUPPORTED_ALG, f"cannot extract credential public key: {e}")
    _enforce_supported_alg(cred_pub)

    try:
        verification = verify_registration_response(
            credential={
                "id": credential_id,
                "rawId": credential_id,
                "response": {
                    "clientDataJSON": client_data_json,
                    "attestationObject": attestation_object,
                },
                "type": "public-key",
            },
            expected_challenge=b64url_to_challenge(challenge_b64url),
            expected_rp_id=config.webauthn.rp_id,
            expected_origin=config.webauthn.origin,
            require_user_verification=True,
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
        )
    except InvalidRegistrationResponse as e:
        raise WebAuthnError(_ASSERTION_REQUIRED, f"registration verification failed: {e}")

    # The verified credential's COSE public key is the authoritative one; alg if
    # decodable must already be enforced above.
    credential = WebAuthnCredential(
        credential_id=credential_id,
        user_handle=user_handle,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        attestation_format=verification.fmt if verification.fmt else None,
        attestation_data={
            "credential_type": verification.credential_type.value
            if verification.credential_type
            else None,
            "user_verified": verification.user_verified,
            "attestation_object": base64.urlsafe_b64encode(verification.attestation_object)
            .decode()
            .rstrip("=")
            if verification.attestation_object
            else None,
        },
        is_active=True,
    )
    session.add(credential)
    session.flush()
    return credential


# ---------------------------------------------------------------------------
# Assertion
# ---------------------------------------------------------------------------
def begin_assertion(
    config: Settings,
    user_handle: str,
    binding: dict[str, Any] | None = None,
    store: ChallengeStore | None = None,
) -> dict[str, Any]:
    """Begin an assertion ceremony (INV-2). `binding` is the challenge_binding
    stored with the challenge and verified on completion (S3.3)."""
    challenge = generate_challenge()
    options = generate_authentication_options(
        rp_id=config.webauthn.rp_id,
        challenge=challenge,
        allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_b64 = challenge_to_b64url(options.challenge)
    # Default binding: policy signing mode (used by create_policy_signing_challenge
    # and the studio). Callers at checkout pass {"mode": "cart", "cart_hash": ...}.
    (store or _DEFAULT_STORE).issue(
        challenge_b64, binding if binding is not None else {"mode": "policy"}
    )
    return {
        "challenge": challenge_b64,
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
    binding: dict[str, Any] | None = None,
    store: ChallengeStore | None = None,
) -> tuple[bool, int]:
    """Complete an assertion ceremony (INV-2). Full S3.3 verification:
    challenge single-use + TTL, challenge_binding match, credential presence,
    UV flag (bit 0x04 @ byte 32), sign-count monotonicity, COSE signature.

    Returns (verified=True, new_sign_count) on success; raises WebAuthnError on
    any rejection (R0.5). All rejections use closed-set reason codes.
    """
    entry = (store or _DEFAULT_STORE).consume(challenge_b64url)

    # challenge_binding: the binding requested at begin_assertion must match the
    # one supplied at completion (PRD §3.5 e5, S3.3). The stored binding is
    # authoritative; a supplied binding that does not match is rejected. A
    # missing supplied binding never self-bypasses the check (R0.5).
    stored_binding = entry.get("binding") or {}
    if not _binding_matches(stored_binding, binding if binding is not None else {}):
        raise WebAuthnError(
            _ASSERTION_REQUIRED, "challenge_binding mismatch", failure_type=challenge_mismatch
        )

    credential = session.exec(
        select(WebAuthnCredential).where(
            WebAuthnCredential.credential_id == credential_id,
            WebAuthnCredential.user_handle == user_handle,
            WebAuthnCredential.is_active.is_(True),  # type: ignore[attr-defined]
        )
    ).first()
    if not credential:
        raise WebAuthnError(
            _ASSERTION_REQUIRED, "credential not found", failure_type=credential_not_found
        )

    # Algorithm gate on the stored public key.
    _enforce_supported_alg(credential.public_key)

    auth_data = base64.urlsafe_b64decode(authenticator_data + "=" * (-len(authenticator_data) % 4))
    flags, received_sign_count = _decode_auth_data_flags_sign_count(auth_data)

    # UV flag (bit 0x04 in the flags byte at index 32) mandatory (PRD §3.5 e4).
    if not (flags & _UV_FLAG):
        raise WebAuthnError(
            _ASSERTION_REQUIRED, "user_verification flag not set", failure_type=uv_flag_missing
        )

    # Sign-count monotonicity (S3.3): both 0 -> accept (counter-less); otherwise
    # received must strictly exceed stored.
    stored_sign_count = credential.sign_count
    if received_sign_count != 0 and received_sign_count <= stored_sign_count:
        raise WebAuthnError(
            _ASSERTION_REQUIRED, "sign_count regression", failure_type=sign_count_regression
        )

    try:
        verification = verify_authentication_response(
            credential={
                "id": credential_id,
                "rawId": credential_id,
                "response": {
                    "clientDataJSON": client_data_json,
                    "authenticatorData": authenticator_data,
                    "signature": signature,
                },
                "type": "public-key",
            },
            expected_challenge=b64url_to_challenge(challenge_b64url),
            expected_rp_id=config.webauthn.rp_id,
            expected_origin=config.webauthn.origin,
            credential_public_key=credential.public_key,
            credential_current_sign_count=stored_sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as e:
        raise WebAuthnError(
            _ASSERTION_REQUIRED,
            f"assertion verification failed: {e}",
            failure_type=assertion_signature_invalid,
        )

    new_sign_count = verification.new_sign_count
    credential.sign_count = new_sign_count
    credential.last_used_at = datetime.now(UTC)
    session.add(credential)
    session.flush()
    return True, new_sign_count


def get_user_credentials(session: Session, user_handle: str) -> list[WebAuthnCredential]:
    """Get all active credentials for a user."""
    return list(
        session.exec(
            select(WebAuthnCredential).where(
                WebAuthnCredential.user_handle == user_handle,
                WebAuthnCredential.is_active.is_(True),  # type: ignore[attr-defined]
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Policy signing ceremony (S3.3 / S3.5)
# ---------------------------------------------------------------------------
def create_policy_signing_challenge(
    config: Settings,
    policy_id: str,
    policy_hash: str,
    merchant_id: str,
    store: ChallengeStore | None = None,
) -> dict[str, Any]:
    """Create the challenge for a policy-signing assertion. Binds to policy mode
    ({"mode": "policy"}); the returned challenge_data is informational, while the
    single-use challenge itself lives in the store."""
    challenge = generate_challenge()
    challenge_b64 = challenge_to_b64url(challenge)
    (store or _DEFAULT_STORE).issue(challenge_b64, {"mode": "policy"})
    challenge_data = {
        "type": "policy_signing",
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "merchant_id": merchant_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "nonce": secrets.token_hex(16),
    }
    return {"challenge": challenge_b64, "challenge_data": challenge_data}
