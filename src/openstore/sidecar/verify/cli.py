"""`openstore verify <bundle>` — offline, exit 0 / 1 / 2.

Exit codes are the interface: `0` valid, `1` tampered, `2` untrusted key. A
script that pipes receipts through this needs to tell the three apart without
parsing prose, and the codes are carried over from `main` unchanged for exactly
that reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openstore.sidecar.verify.checks import ExitCode, bundle_from_dict, verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openstore-verify",
        description=(
            "Verify a sealed OpenStore receipt. Runs entirely offline: the bundle "
            "carries its own key snapshot."
        ),
    )
    parser.add_argument("bundle", type=Path, help="path to the receipt JSON")
    parser.add_argument(
        "--salt",
        help=(
            "hex order_salt, to open the Destination and Contact commitments. "
            "Without it those two report `unopened`, which is a correct result "
            "and not a failure."
        ),
    )
    parser.add_argument("--destination", type=Path, help="JSON file of the stored Destination")
    parser.add_argument("--contact", type=Path, help="JSON file of the stored Contact Point")
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.bundle.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.bundle}: {exc}", file=sys.stderr)
        return int(ExitCode.TAMPERED)

    result = verify(
        bundle_from_dict(document),
        order_salt=bytes.fromhex(args.salt) if args.salt else None,
        destination=json.loads(args.destination.read_text()) if args.destination else None,
        contact=json.loads(args.contact.read_text()) if args.contact else None,
    )
    print(result.render())
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
