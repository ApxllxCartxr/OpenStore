"""`openstore-keys` — the Merchant's signing keys, and proving a backup works.

**Losing this keyfile invalidates every receipt this shop has ever issued.** The
keys are the Merchant's identity (ADR-0014): agents pin them, receipts are
sealed with them, and the verifier checks signatures against the JWKS they
publish. There is no recovery from somebody else's copy, because nobody else has
one — recovery is only from the Merchant's own export.

So there are two commands and they are the two halves of a backup that is worth
having:

- `export` writes an encrypted copy somewhere else.
- `check` proves that copy still opens and still holds the key the shop is
  publishing. **A backup nobody has restored is a hope, not a backup**, and this
  is the drill.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openstore.sidecar.evidence.keys import Keyring, load, save


def _load(path: Path, passphrase: str) -> Keyring | None:
    try:
        # The domain is read back out of the file rather than asserted: this is
        # a backup tool, and refusing to open a keyfile because the operator
        # typed the domain differently would be the opposite of useful.
        import json

        document = json.loads(path.read_text(encoding="utf-8"))
        return load(
            path, merchant_domain=str(document.get("merchant_domain", "")), passphrase=passphrase
        )
    except Exception as error:  # noqa: BLE001 - every failure is one message to an operator
        print(f"cannot open {path}: {error}", file=sys.stderr)
        return None


def _export(args: argparse.Namespace) -> int:
    keyring = _load(args.keyfile, args.passphrase)
    if keyring is None:
        return 2
    if not args.export_passphrase:
        print(
            "Refusing to write an unencrypted export. The live keyfile may be "
            "unencrypted — it sits on a host only you can reach — but a copy that "
            "travels to a backup does not have that protection. Pass "
            "--export-passphrase.",
            file=sys.stderr,
        )
        return 2

    save(keyring, args.out, passphrase=args.export_passphrase)
    print(f"wrote {args.out} ({len(keyring.keys)} key(s), encrypted)")
    print(
        "Keep it somewhere this host cannot reach, and run "
        f"`openstore-keys check {args.out} --passphrase ...` now rather than "
        "on the day you need it."
    )
    return 0


def _check(args: argparse.Namespace) -> int:
    backup = _load(args.keyfile, args.passphrase)
    if backup is None:
        return 1

    print(f"  opens:        yes ({len(backup.keys)} key(s))")
    print(f"  merchant:     {backup.merchant_domain}")
    print(f"  current kid:  {backup.current.kid}")

    if not args.against:
        print("\n  A backup that opens is half the drill. Pass --against <live keyfile>")
        print("  to prove it holds the key this shop is actually publishing.")
        return 0

    live = _load(args.against, args.against_passphrase)
    if live is None:
        return 1

    problems: list[str] = []
    if live.merchant_domain != backup.merchant_domain:
        problems.append(
            f"different Merchant: live is {live.merchant_domain}, backup is {backup.merchant_domain}"
        )
    live_kids = {k for k in live.keys}
    missing = live_kids - set(backup.keys)
    if missing:
        problems.append(
            f"the backup is missing key(s) the shop is publishing: {sorted(missing)}. "
            f"Every receipt sealed with them would verify against nothing."
        )
    if live.current.kid != backup.current.kid:
        problems.append(
            f"different current key: live seals with {live.current.kid}, backup would "
            f"seal with {backup.current.kid}"
        )

    if problems:
        print("\n  NOT A USABLE BACKUP")
        for problem in problems:
            print(f"    - {problem}")
        return 1

    print(f"  matches live: yes ({len(live_kids)} key(s), current {live.current.kid})")
    print("\n  This backup would restore the shop's identity.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openstore-keys",
        description=(
            "Back up and verify the Merchant's signing keys. Losing them invalidates "
            "every receipt this shop has ever issued."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="write an encrypted copy of the keyfile")
    export.add_argument("keyfile", type=Path, help="the live keyfile")
    export.add_argument("out", type=Path, help="where to write the copy")
    export.add_argument("--passphrase", default="", help="the live keyfile's passphrase, if any")
    export.add_argument(
        "--export-passphrase", default="", help="passphrase for the copy (required)"
    )
    export.set_defaults(func=_export)

    check = sub.add_parser("check", help="prove a backup opens, and matches the live keys")
    check.add_argument("keyfile", type=Path, help="the backup to check")
    check.add_argument("--passphrase", default="", help="the backup's passphrase, if any")
    check.add_argument("--against", type=Path, help="the live keyfile to compare against")
    check.add_argument("--against-passphrase", default="", help="the live keyfile's passphrase")
    check.set_defaults(func=_check)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
