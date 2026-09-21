"""The backup drill, exercised.

Losing the keyfile invalidates every receipt this shop has ever issued, and
there is no recovery from anybody else's copy — nobody has one. So the only
thing that makes a backup real is having restored it, and these are that.
"""

from __future__ import annotations

from pathlib import Path

from openstore.sidecar.evidence.cli import main
from openstore.sidecar.evidence.keys import Keyring, save


def _live(tmp_path: Path, *, passphrase: str = "") -> Path:
    keyring = Keyring(merchant_domain="spoiledduckie.localhost")
    keyring.enroll("k1")
    path = tmp_path / "keys.json"
    save(keyring, path, passphrase=passphrase)
    return path


def test_an_export_round_trips_and_matches_the_live_keys(tmp_path: Path) -> None:
    live = _live(tmp_path)
    backup = tmp_path / "backup.json"

    assert main(["export", str(live), str(backup), "--export-passphrase", "s3cret"]) == 0
    assert backup.exists()
    assert main(["check", str(backup), "--passphrase", "s3cret", "--against", str(live)]) == 0


def test_an_unencrypted_export_is_refused(tmp_path: Path) -> None:
    """The live keyfile may be unencrypted — it sits on a host only the operator
    can reach. A copy that travels to a backup does not have that protection."""
    live = _live(tmp_path)

    assert main(["export", str(live), str(tmp_path / "plain.json")]) == 2
    assert not (tmp_path / "plain.json").exists()


def test_a_backup_missing_a_published_key_is_not_a_usable_backup(tmp_path: Path) -> None:
    """The failure this drill exists to find: the shop rotated, the backup did
    not, and every receipt sealed since would verify against nothing."""
    live_ring = Keyring(merchant_domain="spoiledduckie.localhost")
    live_ring.enroll("k1")
    stale = tmp_path / "stale.json"
    save(live_ring, stale, passphrase="s3cret")

    live_ring.rotate("k2")
    live = tmp_path / "keys.json"
    save(live_ring, live, passphrase="")

    assert main(["check", str(stale), "--passphrase", "s3cret", "--against", str(live)]) == 1


def test_a_backup_that_will_not_open_fails_rather_than_reassures(tmp_path: Path) -> None:
    backup = tmp_path / "corrupt.json"
    backup.write_text("{not json", encoding="utf-8")

    assert main(["check", str(backup)]) == 1


def test_checking_without_a_live_keyfile_says_the_drill_is_half_done(tmp_path: Path) -> None:
    """Opening is not matching. A backup of the wrong shop's keys opens fine."""
    backup = _live(tmp_path, passphrase="s3cret")

    assert main(["check", str(backup), "--passphrase", "s3cret"]) == 0
