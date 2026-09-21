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
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
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


# ── Persistence ──────────────────────────────────────────────────────────────
#
# ADR-0014: keys are generated in the sidecar and never leave it. That only
# holds if they also *survive* it. A key held in memory is a new Merchant
# identity on every restart — it breaks the JWKS pinning agents do at add time
# (TOFU), and every receipt sealed before the restart becomes unverifiable
# against the live JWKS. So the keyring is written down, and the file is the
# Merchant's custody.

KEYFILE_VERSION = 1


class KeyfileError(Exception):
    """Refusing to guess about a keyfile.

    Every case here is one where carrying on would silently mint a *new*
    Merchant identity over the top of an existing one, which is the one outcome
    a keyfile exists to prevent.
    """


def _dump_private(record: KeyRecord, passphrase: str) -> str:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    return record.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode()


def save(keyring: Keyring, path: Path, *, passphrase: str = "") -> None:
    """Write the keyring, private keys included, readable only by this user.

    An unencrypted keyfile is a real posture and not a broken one — a passphrase
    the operator has to hold somewhere is not automatically safer than file
    permissions on a host only they can reach. What is not acceptable is being
    unable to tell which one you have, so the file says so.
    """
    document = {
        "version": KEYFILE_VERSION,
        "merchant_domain": keyring.merchant_domain,
        "current": keyring._current,
        "encrypted": bool(passphrase),
        "keys": [
            {
                "kid": record.kid,
                "created_at": record.created_at.isoformat(),
                "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
                "private_key_pem": _dump_private(record, passphrase),
            }
            for record in sorted(keyring.keys.values(), key=lambda r: r.kid)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written private-first: a keyfile that is briefly world-readable has
    # already been readable.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)


def load(path: Path, *, merchant_domain: str, passphrase: str = "") -> Keyring:
    """Read a keyring back, refusing every ambiguity rather than papering over it."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KeyfileError(f"{path} exists but cannot be read as a keyfile: {exc}") from exc

    version = document.get("version")
    if version != KEYFILE_VERSION:
        raise KeyfileError(
            f"{path} is keyfile version {version!r}, and this sidecar writes {KEYFILE_VERSION}."
        )

    stored_domain = document.get("merchant_domain")
    if stored_domain != merchant_domain:
        # Serving one domain's keys under another domain's card publishes keys
        # whose receipts name a different Merchant.
        raise KeyfileError(
            f"{path} holds keys for {stored_domain!r}, but this sidecar serves {merchant_domain!r}. "
            "Point OPENSTORE_MERCHANT_DOMAIN at the right shop, or move the keyfile aside."
        )

    if document.get("encrypted") and not passphrase:
        raise KeyfileError(
            f"{path} is encrypted and SIDECAR_SIGNING_KEY_PASSPHRASE is unset. "
            "Booting without it would enroll a second key alongside keys that already exist."
        )

    keyring = Keyring(merchant_domain=merchant_domain)
    secret = passphrase.encode() if passphrase else None
    for entry in document.get("keys", []):
        try:
            private_key = serialization.load_pem_private_key(
                entry["private_key_pem"].encode(), password=secret
            )
        except (ValueError, TypeError) as exc:
            raise KeyfileError(
                f"{path} key {entry.get('kid')!r} could not be decrypted — "
                "wrong SIDECAR_SIGNING_KEY_PASSPHRASE, or the file is damaged."
            ) from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise KeyfileError(f"{path} key {entry.get('kid')!r} is not an EC key.")
        revoked_at = entry.get("revoked_at")
        keyring.keys[entry["kid"]] = KeyRecord(
            kid=entry["kid"],
            private_key=private_key,
            created_at=datetime.fromisoformat(entry["created_at"]),
            revoked_at=datetime.fromisoformat(revoked_at) if revoked_at else None,
        )

    current = document.get("current", "")
    if current and current not in keyring.keys:
        raise KeyfileError(
            f"{path} names {current!r} as its current key, which is not in the file."
        )
    keyring._current = current
    return keyring


def load_or_enroll(
    path: Path, *, merchant_domain: str, passphrase: str = ""
) -> tuple[Keyring, bool]:
    """The boot path: read the keyring, or mint the Merchant's first key once.

    Returns the keyring and whether a key was enrolled, so boot can say which
    happened. "Enrolled a new signing key" on the second boot of a deploy means
    the keyfile is not where the operator thinks it is, and that is worth a line
    in the log every time.
    """
    if path.exists():
        return load(path, merchant_domain=merchant_domain, passphrase=passphrase), False
    keyring = Keyring(merchant_domain=merchant_domain)
    keyring.enroll("k1")
    save(keyring, path, passphrase=passphrase)
    return keyring, True
