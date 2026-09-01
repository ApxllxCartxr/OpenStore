# scripts/demo_seed.py
# Stage 9 (Part 12 beat 5: campaign beat) — seed analytics history.
#
# Idempotent: creates a known set of historic checkouts (PAID) so that the
# campaign agent's analytics view is non-empty and the orchestrator can draft
# a real campaign. Run with:
#
#   uv run python scripts/demo_seed.py <merchant_id> [--db sqlite:///openstore.db]
#
# The script reads its config from the same gelateria.yaml the server uses
# (catalog_path, currency, etc.) and creates checkouts in the last 7 and last
# 30 days so that 7d/30d numbers diverge.

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, create_engine

# Add the project src to sys.path so this script runs from the repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openstore.config import load_config  # noqa: E402
from openstore.core.database import init_database  # noqa: E402
from openstore.models import Checkout, OrderState  # noqa: E402


def _seed_cart(sku: str, unit_minor: int, qty: int) -> dict[str, Any]:
    return {
        "items": [{"sku": sku, "qty": qty, "unit_minor": unit_minor, "tags": []}],
        "cart_hash": f"sha256:{secrets.token_hex(16)}",
        "cart_version": 1,
        "amount_minor": unit_minor * qty,
        "currency": "INR",
    }


def seed(merchant_id: str, config_path: str, db_url: str | None = None) -> int:
    config = load_config(config_path)
    init_database(config)

    from openstore.core.database import _engine  # type: ignore
    engine = _engine if db_url is None else create_engine(db_url)

    now = datetime.now(UTC)

    # A known mix of historic sales across the last 30 days.
    # (sku, qty, unit_minor, days_ago)
    fixtures: list[tuple[str, int, int, int]] = [
        ("gelato_vanilla", 3, 15000, 1),
        ("gelato_vanilla", 2, 15000, 4),
        ("gelato_vanilla", 5, 15000, 10),
        ("gelato_chocolate", 4, 15000, 2),
        ("gelato_chocolate", 1, 15000, 6),
        ("gelato_pistachio", 1, 18000, 12),   # slow mover
        ("gelato_pistachio", 2, 18000, 20),   # slow mover
        ("cone_waffle", 6, 3000, 1),
        ("topping_sprinkles", 8, 2000, 3),
    ]

    created = 0
    with Session(engine) as session:
        for sku, qty, unit, days_ago in fixtures:
            ck_id = f"chk_seed_{secrets.token_hex(6)}"
            ts = now - timedelta(days=days_ago)
            ck = Checkout(
                id=ck_id,
                trace_id=f"trace_seed_{secrets.token_hex(4)}",
                client_id="demo_seed",
                merchant_id=merchant_id,
                cart_hash=f"sha256:{secrets.token_hex(8)}",
                cart_version=1,
                amount_minor=unit * qty,
                currency="INR",
                state=OrderState.PAID,
                policy_id="pol_seed",
                policy_hash="ph_seed",
                aal_level=2,
                expires_at=ts + timedelta(hours=1),
                idempotency_key=f"seed_{ck_id}",
                cart_snapshot=_seed_cart(sku, unit, qty),
                created_at=ts,
                updated_at=ts,
            )
            session.add(ck)
            created += 1
        session.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo analytics history")
    parser.add_argument("merchant_id", help="Merchant id (e.g. gelateria-milano)")
    parser.add_argument(
        "--config", default="gelateria.yaml",
        help="Path to the merchant config YAML (default: gelateria.yaml)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Override database URL (default: use config.database.url)",
    )
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 4  # verifier-style usage error

    n = seed(args.merchant_id, args.config, args.db)
    print(json.dumps({"seeded_checkouts": n, "merchant_id": args.merchant_id}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
