"""CLI entry point for webhook replay.

Usage: python -m openstore.replay <event_id>

PRODUCTION_READINESS §1.4 — replay tooling turns "we lost an event in
production" from an archaeology project into one command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a stored webhook event by event_id."
    )
    parser.add_argument(
        "event_id",
        help="The event_id to replay (from X-Razorpay-Event-Id header or sha256 of body)",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output result as JSON",
    )
    args = parser.parse_args()

    from openstore.ledger import Ledger
    from openstore.webhooks import replay_webhook

    ledger = Ledger("sqlite://openstore.db")

    async def _run() -> None:
        from openstore.gateway import FakeGateway

        result = await replay_webhook(ledger, args.event_id, FakeGateway())

        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("status") == "not_implemented":
                print(f"Event {args.event_id}: replay not yet implemented")
                print("(Raw event storage not wired to the events table)")
                sys.exit(1)
            else:
                print(f"Event {args.event_id}: {result.get('status', 'unknown')}")

        sys.exit(0 if result.get("status") != "error" else 1)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
