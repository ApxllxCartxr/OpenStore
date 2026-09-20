"""Merchant signing keys: generated in the sidecar, never leaving it.

ADR-0014. Rotation is **additive by `kid`** and never re-signs history;
revocation invalidates the future, not the past. Those two sentences are the
whole design, and the second one is why revocation carries a timestamp: a
bundle signed before the key was revoked stays valid forever, and one signed
after it fails `untrusted-key`. A revocation that invalidated everything would
destroy the Merchant's own evidence every time they rotated after an incident.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from openstore.sidecar.admission.signatures import public_jwk


@dataclass
class KeyRecord:
    kid: str
    private_key: ec.EllipticCurvePrivateKey
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass
class Keyring:
    """The Merchant's keys, held only here.

    Keys are generated in the sidecar and never leave it; recovery is only from
    the Merchant's own encrypted enrollment export, which the sidecar cannot
    decrypt on their behalf.
    """

    merchant_domain: str
    keys: dict[str, KeyRecord] = field(default_factory=dict)
    _current: str = ""

    def enroll(self, kid: str = "k1", *, now: datetime | None = None) -> KeyRecord:
        if kid in self.keys:
            raise ValueError(f"{kid!r} already enrolled; rotation is additive, not overwriting")
        record = KeyRecord(
            kid=kid,
            private_key=ec.generate_private_key(ec.SECP256R1()),
            created_at=now or datetime.now(UTC),
        )
        self.keys[kid] = record
        self._current = kid
        return record

    def rotate(self, kid: str, *, now: datetime | None = None) -> KeyRecord:
        """Additive: the old key stays in the JWKS and history stays verifiable.
        Re-signing history would mean the Merchant could rewrite it."""
        return self.enroll(kid, now=now)

    def revoke(self, kid: str, *, at: datetime | None = None) -> None:
        record = self.keys[kid]
        record.revoked_at = at or datetime.now(UTC)
        if self._current == kid:
            live = [k for k, r in self.keys.items() if r.active]
            self._current = live[-1] if live else ""

    @property
    def current(self) -> KeyRecord:
        if not self._current:
            raise ValueError("no active signing key; enroll one before sealing a receipt")
        return self.keys[self._current]

    def jwks(self) -> dict[str, object]:
        """Every key, revoked ones included, each carrying its own dates.

        A revoked key stays in the snapshot because a bundle it signed before
        revocation must still verify — and a verifier with no copy of the key
        cannot check that at all.
        """
        return {
            "keys": [
                {
                    **public_jwk(record.private_key, record.kid),
                    "created_at": record.created_at.isoformat(),
                    **({"revoked_at": record.revoked_at.isoformat()} if record.revoked_at else {}),
                }
                for record in sorted(self.keys.values(), key=lambda r: r.kid)
            ]
        }


def sign(record: KeyRecord, payload: bytes) -> str:
    der = record.private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def verify_signature(jwk: dict[str, object], payload: bytes, signature_b64: str) -> bool:
    def _int(value: object) -> int:
        raw = str(value)
        return int.from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), "big")

    try:
        raw = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        return False
    if len(raw) != 64:
        return False

    key = ec.EllipticCurvePublicNumbers(_int(jwk["x"]), _int(jwk["y"]), ec.SECP256R1()).public_key()
    try:
        key.verify(
            utils.encode_dss_signature(
                int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
            ),
            payload,
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature:
        return False
    return True
