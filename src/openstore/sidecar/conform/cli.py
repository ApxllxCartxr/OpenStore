"""`openstore-conform <url>` — exit 0 / 1 / 2.

Exit codes are the interface: `0` this store implements the trait, `1` it does
not yet, `2` the run could not happen at all. A script that gates a deploy on
conformance needs to tell the three apart without parsing prose.

**This writes to the store it points at.** Orders are created and stock is held
and released. Every hold is given back, but a conformance run is still a run
against a real shop, so it says so before it starts and `--read-only` is the
subset that writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from openstore.sidecar.conform.checks import run

ENV_SECRET = "TRAIT_HMAC_SECRET"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openstore-conform",
        description=(
            "Check whether a store implements the OpenStore Merchant-truth trait. "
            "Points at any URL, picks its own subjects out of the catalogue it "
            "finds, and reports what holds and what does not."
        ),
        epilog=(
            "The suite creates orders and takes stock holds, and gives every hold "
            "back. Use --read-only for the subset that writes nothing."
        ),
    )
    parser.add_argument(
        "url",
        help="the trait's base URL, e.g. https://shop.example/wp-json/openstore/v1",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get(ENV_SECRET, ""),
        help=f"the shared HMAC secret. Defaults to ${ENV_SECRET}.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="run only the checks that write nothing: doors 1, 2 and 9.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="machine-readable output, for a CI step that wants the detail.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show what each passing check actually observed.",
    )
    args = parser.parse_args(argv)

    if not args.secret:
        print(
            f"No secret. Pass --secret or set ${ENV_SECRET} — every door is signed, "
            f"and an unsigned request is refused by design.",
            file=sys.stderr,
        )
        return 2

    if not args.as_json:
        print(f"\n  OpenStore trait conformance\n  {args.url}")
        if args.read_only:
            print("  read-only: doors 1, 2 and 9 only, nothing is written")

    report = asyncio.run(run(args.url, args.secret, read_only=args.read_only))

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render(verbose=args.verbose))
        print()

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
