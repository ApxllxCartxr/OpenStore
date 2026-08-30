"""CLI entry point for the reconciliation sweeper.

Usage: python -m openstore.reconcile [--min-age MINUTES] [--max-age DAYS]

PRODUCTION_READINESS §1.5 — every payments company runs a sweeper because
at-least-once delivery is still not at-least-once-eventually-guaranteed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the reconciliation sweeper against pending PSP intents."
    )
    parser.add_argument(
        "--min-age", type=int, default=10,
        help="Minimum age in minutes before checking a pending intent (default: 10)",
    )
    parser.add_argument(
        "--max-age", type=int, default=7,
        help="Maximum age in days; intents older than this are skipped (default: 7)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report drift without correcting local state",
    )
    args = parser.parse_args()

    # Lazy imports so the module loads quickly for --help
    from openstore.ledger import Ledger
    from openstore.webhooks import run_reconciliation, record_reconciliation_drift

    ledger = Ledger("sqlite://openstore.db")

    async def _run() -> None:
        from openstore.gateway import FakeGateway  # no live PSP in sweep

        result = await run_reconciliation(
            ledger,
            gateway=FakeGateway(),
            max_age_days=args.max_age,
            min_age_minutes=args.min_age,
        )
        record_reconciliation_drift(result.drift_detected)

        print(f"Checked: {result.checked}")
        print(f"Drift detected: {result.drift_detected}")
        print(f"Corrected: {result.corrected}")
        if result.errors:
            print(f"Errors: {len(result.errors)}")
            for err in result.errors:
                print(f"  - {err}")

        sys.exit(1 if result.drift_detected > 0 else 0)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
