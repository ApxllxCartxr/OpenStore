"""The `passkey` Authority: one WebAuthn ceremony whose challenge **is** the
binding.

`AuthorityKind.PASSKEY` has been in the closed set since hour 0 and this module
did not exist, so the strongest claim the system can make — *cart* bound by the
*payer's own device* — was enum-level only. Everything else was live; this was
the gap between a Binding table and a ceremony.

**The challenge is not a nonce.** It is `sha256` over the `cart_hash`, the exact
amount, the currency, the Merchant domain and the expiry. The authenticator
signs that, so a signature over a swapped basket is a signature over a different
challenge and fails — the binding is a property of the cryptography rather than
of a server-side lookup nobody re-checks. A random challenge plus a remembered
association would be the ordinary way to build this, and it binds nothing.

**Enrollment happens inside the first approve ceremony** (SPEC §7, PLAN A4).
There are no Consumer accounts here, so roaming five shops must cost five taps
and not five signups: the first visit's `create()` carries the same challenge
the tap would, and the credential exists only as a way to authorize this spend.

**Attestation, and the honest fallback.** Under `fmt: none` the authenticator
signs nothing that is verifiable as an assertion over our challenge — a
registration response with no attestation statement proves possession of a fresh
key and not agreement to a basket. So we *ask* for attestation, verify it when we
get one, and when we cannot, run an immediate `get()` over the **same** challenge
and record `ceremony = assertion`. That is two prompts, named and counted in the
Transcript, rather than one prompt and a binding asserted but not proven.

**User verification is required, not preferred.** `preferred` degrades silently
to presence-only on authenticators that feel like it, and "somebody touched a
key" is not the human permission a spend rests on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AttestationFormat,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from openstore.sidecar.core.canonical import canonical_bytes
from openstore.sidecar.core.codes import Ceremony, ReasonCode
from openstore.sidecar.trait.errors import TraitError

#: Version-tagged so a future change to what the challenge covers is a different
#: preimage rather than a silently different meaning for the same bytes.
CHALLENGE_VERSION = "openstore-passkey-challenge-v1"


class PasskeyRefused(TraitError):
    """A ceremony that did not prove what it had to. Always `authority-stale` or
    `authority-missing` — never a generic error, because the Consumer is looking
    at the refusal and their money is the subject."""


def challenge_for(
    *,
    cart_hash: str,
    total_minor: int,
    currency: str,
    merchant_domain: str,
    expiry_utc: str,
) -> bytes:
    """The challenge, which is the binding.

    Keyword-only: five values of which two are strings that look alike is a
    preimage waiting to be assembled in the wrong order, and getting it wrong
    produces a ceremony that verifies and binds the wrong thing.
    """
    preimage = {
        "v": CHALLENGE_VERSION,
        "cart_hash": cart_hash,
        "total_minor": total_minor,
        "currency": currency,
        "merchant_domain": merchant_domain,
        "expiry_utc": expiry_utc,
    }
    return hashlib.sha256(canonical_bytes(preimage)).digest()


@dataclass(frozen=True)
class StoredCredential:
    """One enrolled authenticator, as little of it as verification needs."""

    credential_id: bytes
    public_key: bytes
    sign_count: int = 0
    attestation_fmt: str = AttestationFormat.NONE.value


@dataclass(frozen=True)
class VerifiedPasskey:
    """What a completed ceremony proved, in the words the Transcript uses.

    `prompts` is counted rather than assumed: "one tap" is the claim this design
    is sold on, and the fallback path costs two. A number that is recorded can be
    checked by a golden replay; a number that is hoped for cannot.
    """

    credential_id: str
    ceremony: Ceremony
    cart_hash: str
    total_minor: int
    user_verified: bool
    attestation_fmt: str
    prompts: int


@dataclass
class PasskeyRP:
    """The Relying Party. One per Merchant domain, because the RP ID is what
    decides which passkeys exist at all (ADR-0008) — a sidecar that derived it
    from a request header would let the origin choose its own identity.

    Credentials live in memory here, like the Pending Carts beside them. A
    passkey enrolled to authorize one spend has no meaning after it; there are
    no Consumer accounts in v1 and a durable credential table would be the first
    half of one nobody asked for.
    """

    rp_id: str
    origin: str
    rp_name: str = "This shop"
    credentials: dict[bytes, StoredCredential] = field(default_factory=dict)
    #: Challenges handed out and not yet spent, keyed by the tap token they
    #: belong to. Single-use, like the tap token itself.
    pending: dict[str, bytes] = field(default_factory=dict)

    # ── beginning ────────────────────────────────────────────────────────────

    def begin(
        self,
        *,
        token: str,
        cart_hash: str,
        total_minor: int,
        currency: str,
        expiry_utc: str,
        credential_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Options for this tap, and which ceremony they are for.

        Returns `("enrollment" | "assertion", options)`. An enrollment is what a
        first visit gets, because there is nobody to have enrolled earlier.
        """
        challenge = challenge_for(
            cart_hash=cart_hash,
            total_minor=total_minor,
            currency=currency,
            merchant_domain=self.rp_id,
            expiry_utc=expiry_utc,
        )
        self.pending[token] = challenge

        known = self._known(credential_ids)
        if known:
            return "assertion", self._assertion_options(challenge, known)
        return "enrollment", self._enrollment_options(challenge, token)

    def _known(self, credential_ids: list[str] | None) -> list[StoredCredential]:
        """Only credentials this RP actually holds. An id the browser offers that
        we have never seen is not a credential, it is a claim."""
        if not credential_ids:
            return []
        found = []
        for raw in credential_ids:
            try:
                stored = self.credentials.get(base64url_to_bytes(raw))
            except Exception:  # noqa: BLE001 - a malformed id is simply not one of ours
                continue
            if stored is not None:
                found.append(stored)
        return found

    def _enrollment_options(self, challenge: bytes, token: str) -> dict[str, Any]:
        import json

        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            # The tap token, which is single-use and carries no identity. There
            # is no Consumer account to name here and inventing a handle would
            # put something durable on the authenticator that outlives the one
            # spend it was created for.
            user_id=token.encode("utf-8"),
            user_name=f"tap-{token[:8]}",
            user_display_name="This purchase",
            challenge=challenge,
            # Asked for, so that a one-prompt ceremony is possible at all. When
            # the authenticator answers `none`, `verify_enrollment` says so and
            # the caller runs the second prompt rather than claiming the binding.
            attestation=AttestationConveyancePreference.DIRECT,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED,
                # Discoverable, so the second visit needs no identifier from us —
                # there is no account to look one up from.
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
        )
        return dict(json.loads(options_to_json(options)))

    def _assertion_options(self, challenge: bytes, known: list[StoredCredential]) -> dict[str, Any]:
        import json

        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in known],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return dict(json.loads(options_to_json(options)))

    def options_for(self, token: str, cart_hash: str) -> dict[str, Any]:
        """Re-issue assertion options over the challenge already handed out.

        Used by the attestation fallback, and it must be the **same** challenge:
        a second prompt over a fresh one would be two ceremonies over two
        bindings, which proves nothing about the basket the first one saw.
        """
        import json

        challenge = self.pending.get(token)
        if challenge is None:
            raise PasskeyRefused(
                ReasonCode.AUTHORITY_STALE, "that passkey ceremony has expired; start again"
            )
        stored = list(self.credentials.values())
        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in stored],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return dict(json.loads(options_to_json(options)))

    # ── finishing ────────────────────────────────────────────────────────────

    def verify_enrollment(
        self, *, token: str, credential: dict[str, Any], cart_hash: str, total_minor: int
    ) -> VerifiedPasskey | None:
        """Verify a `create()` response.

        Returns `None` when the response carried no attestation — the credential
        is stored and usable, and nothing about the basket has been proven yet,
        so the caller must run the assertion over the same challenge. Returning
        a `VerifiedPasskey` there would be the lie this whole module exists to
        avoid.
        """
        challenge = self._challenge(token)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001 - every failure is one refusal to the Consumer
            raise PasskeyRefused(
                ReasonCode.AUTHORITY_MISSING, f"that passkey ceremony did not verify: {exc}"
            ) from None

        fmt = verified.fmt.value if hasattr(verified.fmt, "value") else str(verified.fmt)
        self.credentials[verified.credential_id] = StoredCredential(
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            attestation_fmt=fmt,
        )

        if fmt == AttestationFormat.NONE.value:
            # Nothing in a `none` response is signed as an assertion over our
            # challenge. The credential exists; the agreement does not.
            return None

        self.pending.pop(token, None)
        return VerifiedPasskey(
            credential_id=bytes_to_base64url(verified.credential_id),
            ceremony=Ceremony.ENROLLMENT,
            cart_hash=cart_hash,
            total_minor=total_minor,
            user_verified=verified.user_verified,
            attestation_fmt=fmt,
            prompts=1,
        )

    def verify_assertion(
        self,
        *,
        token: str,
        credential: dict[str, Any],
        cart_hash: str,
        total_minor: int,
        prompts: int = 1,
    ) -> VerifiedPasskey:
        """Verify a `get()` response against the challenge this tap was issued."""
        challenge = self._challenge(token)
        raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id", "")))
        stored = self.credentials.get(raw_id)
        if stored is None:
            raise PasskeyRefused(
                ReasonCode.AUTHORITY_MISSING,
                "that passkey is not one this shop has seen; enroll it on this tap",
            )

        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001 - one refusal, in the Consumer's words
            raise PasskeyRefused(
                ReasonCode.AUTHORITY_STALE, f"that passkey ceremony did not verify: {exc}"
            ) from None

        self.credentials[raw_id] = StoredCredential(
            credential_id=stored.credential_id,
            public_key=stored.public_key,
            sign_count=verified.new_sign_count,
            attestation_fmt=stored.attestation_fmt,
        )
        # Single-use, like the tap token it belongs to: a challenge that could be
        # answered twice is a replay of a human's agreement.
        self.pending.pop(token, None)
        return VerifiedPasskey(
            credential_id=bytes_to_base64url(raw_id),
            ceremony=Ceremony.ASSERTION,
            cart_hash=cart_hash,
            total_minor=total_minor,
            user_verified=verified.user_verified,
            attestation_fmt=stored.attestation_fmt,
            prompts=prompts,
        )

    def _challenge(self, token: str) -> bytes:
        challenge = self.pending.get(token)
        if challenge is None:
            raise PasskeyRefused(
                ReasonCode.AUTHORITY_STALE,
                "that passkey ceremony has expired or was already used; start again",
            )
        return challenge
