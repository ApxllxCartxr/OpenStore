"""RFC 9421 HTTP Message Signatures, verifier side.

Standards rather than invention. A stranger signs every request against the key
in its published Agent Profile, and the signature covers method, target,
`Content-Digest` (RFC 9530), `created` and `expires`, with a nonce and a short
acceptance window — so a captured request cannot be replayed.

The signature base is the fiddly part of RFC 9421 and it is written here exactly
once, so that A4c's signer (in the chat, in TypeScript) can be tested against
these same vectors. Two people debugging one signature base from opposite ends
in two languages is the classic way a day disappears.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import load_der_public_key

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.trait.errors import TraitError

#: §16.7. Same window as the trait's own HMAC: a signature good for longer than
#: a minute is a signature worth capturing.
SIGNATURE_WINDOW_SECONDS = 60

COVERED_COMPONENTS = ("@method", "@target-uri", "content-digest")


class SignatureRefused(TraitError):
    def __init__(self, detail: str) -> None:
        super().__init__(ReasonCode.SIGNATURE_INVALID, detail)


def content_digest(body: bytes) -> str:
    """RFC 9530, `sha-256=:<base64>:`. Structured-field syntax, colons included —
    a digest formatted differently is one the other end will not match."""
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return f"sha-256=:{digest}:"


def signature_base(
    *,
    method: str,
    target_uri: str,
    content_digest_value: str,
    created: int,
    expires: int,
    nonce: str,
    key_id: str,
) -> bytes:
    """The RFC 9421 signature base, built once for both ends.

    Component order is fixed and the `@signature-params` line repeats it, which
    is what stops an attacker reordering or dropping a covered component and
    still matching.
    """
    params = (
        f'("@method" "@target-uri" "content-digest");created={created};'
        f'expires={expires};keyid="{key_id}";alg="ecdsa-p256-sha256";nonce="{nonce}"'
    )
    lines = [
        f'"@method": {method.upper()}',
        f'"@target-uri": {target_uri}',
        f'"content-digest": {content_digest_value}',
        f'"@signature-params": {params}',
    ]
    return "\n".join(lines).encode("utf-8")


def _jwk_to_public_key(jwk: dict[str, object]) -> ec.EllipticCurvePublicKey:
    def _b64(value: object) -> int:
        raw = str(value)
        padded = raw + "=" * (-len(raw) % 4)
        return int.from_bytes(base64.urlsafe_b64decode(padded), "big")

    numbers = ec.EllipticCurvePublicNumbers(_b64(jwk["x"]), _b64(jwk["y"]), ec.SECP256R1())
    return numbers.public_key()


@dataclass
class NonceCache:
    """Replay dies here, not in review.

    Nonces are remembered for exactly the window they are valid in. An unbounded
    set is a memory leak whose size an attacker chooses.
    """

    window_seconds: int = SIGNATURE_WINDOW_SECONDS
    _seen: dict[str, int] = field(default_factory=dict)

    def check_and_remember(self, nonce: str, now: int) -> None:
        if not nonce:
            raise SignatureRefused("signed request carries no nonce")
        self._prune(now)
        if nonce in self._seen:
            raise SignatureRefused("this request has already been seen (nonce replay)")
        self._seen[nonce] = now

    def _prune(self, now: int) -> None:
        for nonce in [n for n, ts in self._seen.items() if now - ts > self.window_seconds]:
            del self._seen[nonce]


@dataclass
class SignatureVerifier:
    """Verifies a stranger's signed request against its pinned JWKS."""

    nonces: NonceCache = field(default_factory=NonceCache)
    window_seconds: int = SIGNATURE_WINDOW_SECONDS

    def verify(
        self,
        *,
        jwks: dict[str, object],
        method: str,
        target_uri: str,
        body: bytes,
        content_digest_header: str,
        signature_b64: str,
        created: int,
        expires: int,
        nonce: str,
        key_id: str,
        now: int | None = None,
    ) -> None:
        """Raise unless the signature is valid, fresh, unseen, and over *this*
        body."""
        moment = now if now is not None else int(time.time())

        expected_digest = content_digest(body)
        if content_digest_header != expected_digest:
            # Checked before the signature so a body swap is named as a body
            # swap rather than as a generic signature failure.
            raise SignatureRefused(
                "Content-Digest does not match the body; the request was modified in flight"
            )

        if created > moment + 5:
            raise SignatureRefused(f"signature created {created - moment}s in the future")
        if moment > expires:
            raise SignatureRefused("signature has expired")
        if expires - created > self.window_seconds:
            raise SignatureRefused(
                f"signature window is {expires - created}s; the maximum is "
                f"{self.window_seconds}s, because a longer-lived signature is worth capturing"
            )

        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise SignatureRefused("no keys in the pinned JWKS")
        jwk = next(
            (k for k in keys if isinstance(k, dict) and (k.get("kid") == key_id or not key_id)),
            None,
        )
        if jwk is None:
            raise SignatureRefused(f"no pinned key with kid {key_id!r}")

        base = signature_base(
            method=method,
            target_uri=target_uri,
            content_digest_value=content_digest_header,
            created=created,
            expires=expires,
            nonce=nonce,
            key_id=key_id,
        )

        try:
            raw = base64.b64decode(signature_b64)
        except (ValueError, TypeError):
            raise SignatureRefused("signature is not valid base64") from None

        if len(raw) != 64:
            raise SignatureRefused(f"ES256 signatures are 64 raw bytes (r||s); got {len(raw)}")
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")

        try:
            _jwk_to_public_key(jwk).verify(
                utils.encode_dss_signature(r, s), base, ec.ECDSA(hashes.SHA256())
            )
        except InvalidSignature:
            raise SignatureRefused("signature does not verify against the pinned key") from None

        # Only after the signature verifies. Remembering a nonce from an
        # unverified request lets anyone burn nonces they never signed.
        self.nonces.check_and_remember(nonce, moment)


def sign_for_tests(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    method: str,
    target_uri: str,
    body: bytes,
    created: int,
    expires: int,
    nonce: str,
    key_id: str,
) -> tuple[str, str]:
    """Produce a signature the verifier accepts.

    Lives in the sidecar because A4c's real signer is the chat's, in TypeScript,
    and both are checked against the same vectors. Having a Python signer here
    is what makes "the same specification read from two ends" testable before
    the chat exists.
    """
    digest = content_digest(body)
    base = signature_base(
        method=method,
        target_uri=target_uri,
        content_digest_value=digest,
        created=created,
        expires=expires,
        nonce=nonce,
        key_id=key_id,
    )
    der = private_key.sign(base, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.b64encode(raw).decode(), digest


def public_jwk(private_key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, object]:
    numbers = private_key.public_key().public_numbers()

    def _b64(value: int) -> str:
        return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()

    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(numbers.x),
        "y": _b64(numbers.y),
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
    }


__all__ = [
    "COVERED_COMPONENTS",
    "NonceCache",
    "SignatureRefused",
    "SignatureVerifier",
    "content_digest",
    "load_der_public_key",
    "public_jwk",
    "signature_base",
    "sign_for_tests",
]
