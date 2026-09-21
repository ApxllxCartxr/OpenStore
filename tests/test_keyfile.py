"""Keys survive a restart, or they are not the Merchant's identity (ADR-0014).

The keyring was implemented, tested, and never constructed outside a test, so
the running sidecar served `{"keys": []}` — and the first thing that noticed was
a buyer agent refusing the shop for carrying no keys. These tests are about the
part that was missing: the file.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openstore.sidecar.evidence.keys import (
    KeyfileError,
    Keyring,
    load,
    load_or_enroll,
    save,
    sign,
    verify_signature,
)

DOMAIN = "spoiledduckie.localhost"


def test_a_restart_keeps_the_same_key(tmp_path: Path) -> None:
    """The whole point. A key regenerated on boot is a new Merchant identity,
    which breaks every agent that pinned the old one and orphans every receipt
    already sealed."""
    path = tmp_path / "keys.json"
    first, enrolled = load_or_enroll(path, merchant_domain=DOMAIN)
    assert enrolled is True

    second, enrolled_again = load_or_enroll(path, merchant_domain=DOMAIN)
    assert enrolled_again is False
    assert second.current.kid == first.current.kid
    assert second.jwks() == first.jwks()


def test_a_signature_from_before_the_restart_still_verifies(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    before, _ = load_or_enroll(path, merchant_domain=DOMAIN)
    signature = sign(before.current, b"a receipt sealed before the restart")

    after, _ = load_or_enroll(path, merchant_domain=DOMAIN)
    jwk = next(k for k in after.jwks()["keys"] if k["kid"] == before.current.kid)
    assert verify_signature(jwk, b"a receipt sealed before the restart", signature)


def test_the_keyfile_is_not_readable_by_anyone_else(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    load_or_enroll(path, merchant_domain=DOMAIN)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_passphrase_encrypts_the_private_key_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    load_or_enroll(path, merchant_domain=DOMAIN, passphrase="hunter2")
    document = json.loads(path.read_text())
    assert document["encrypted"] is True
    assert "ENCRYPTED PRIVATE KEY" in document["keys"][0]["private_key_pem"]


def test_an_encrypted_keyfile_refuses_the_wrong_passphrase(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    load_or_enroll(path, merchant_domain=DOMAIN, passphrase="hunter2")
    with pytest.raises(KeyfileError, match="could not be decrypted"):
        load(path, merchant_domain=DOMAIN, passphrase="wrong")


def test_an_encrypted_keyfile_refuses_a_missing_passphrase(tmp_path: Path) -> None:
    """Rather than enrolling a second key over the top of keys that exist."""
    path = tmp_path / "keys.json"
    load_or_enroll(path, merchant_domain=DOMAIN, passphrase="hunter2")
    with pytest.raises(KeyfileError, match="encrypted"):
        load_or_enroll(path, merchant_domain=DOMAIN)


def test_a_keyfile_refuses_to_serve_another_merchants_keys(tmp_path: Path) -> None:
    """Publishing one domain's keys under another's card signs that Merchant's
    receipts with keys that name a different one."""
    path = tmp_path / "keys.json"
    load_or_enroll(path, merchant_domain=DOMAIN)
    with pytest.raises(KeyfileError, match="someone-else.example"):
        load_or_enroll(path, merchant_domain="someone-else.example")


def test_rotation_survives_a_restart_additively(tmp_path: Path) -> None:
    """ADR-0014: history is never re-signed, so a rotated key stays published."""
    path = tmp_path / "keys.json"
    keyring, _ = load_or_enroll(path, merchant_domain=DOMAIN)
    keyring.rotate("k2")
    save(keyring, path)

    reloaded = load(path, merchant_domain=DOMAIN)
    assert sorted(k["kid"] for k in reloaded.jwks()["keys"]) == ["k1", "k2"]
    assert reloaded.current.kid == "k2"


def test_revocation_survives_a_restart_with_its_timestamp(tmp_path: Path) -> None:
    """Compromise invalidates the future, not the past — which only holds if the
    moment it happened is still on disk after a restart."""
    path = tmp_path / "keys.json"
    keyring, _ = load_or_enroll(path, merchant_domain=DOMAIN)
    keyring.rotate("k2")
    at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    keyring.revoke("k1", at=at)
    save(keyring, path)

    reloaded = load(path, merchant_domain=DOMAIN)
    assert reloaded.keys["k1"].revoked_at == at
    assert reloaded.keys["k1"].active is False
    assert reloaded.current.kid == "k2"
    # Still published: a bundle it signed before revocation must keep verifying.
    assert any(k["kid"] == "k1" for k in reloaded.jwks()["keys"])


def test_a_damaged_keyfile_refuses_rather_than_minting_a_new_identity(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text("{ not json")
    with pytest.raises(KeyfileError, match="cannot be read"):
        load_or_enroll(path, merchant_domain=DOMAIN)


def test_an_unknown_keyfile_version_refuses(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"version": 99, "merchant_domain": DOMAIN, "keys": []}))
    with pytest.raises(KeyfileError, match="version"):
        load(path, merchant_domain=DOMAIN)


def test_an_empty_keyring_refuses_to_sign_rather_than_sealing_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no active signing key"):
        Keyring(merchant_domain=DOMAIN).current
