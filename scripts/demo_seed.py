# scripts/demo_seed.py
# Stage 9 (Part 12 beat 5: campaign beat) — seed analytics history.
#
# Idempotent: creates a known set of historic checkouts (PAID) so that the
# campaign agent's analytics view is non-empty and the orchestrator can draft
# a real campaign. Run with:
#
#   uv run python scripts/demo_seed.py <merchant_id> [--config <path>] [--db sqlite:///openstore.db]
#
# The script reads its config from the merchant's own YAML (catalog_path,
# currency, etc.) and creates checkouts spread across the last 30 days so
# 7d/30d numbers diverge and a clear top-seller/slow-mover split emerges.
# Fixtures are per-merchant (S14) — each entry is one checkout, days_ago plus
# the cart it contained (one or more line items, matching how a real combo
# purchase — e.g. chai + a snack — looks in cart_snapshot).

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

# One line item: (sku, qty, unit_minor). One fixture: (days_ago, [line items]).
LineItem = tuple[str, int, int]
Fixture = tuple[int, list[LineItem]]

_GELATERIA_FIXTURES: list[Fixture] = [
    (1, [("gelato_vanilla", 3, 15000)]),
    (4, [("gelato_vanilla", 2, 15000)]),
    (10, [("gelato_vanilla", 5, 15000)]),
    (2, [("gelato_chocolate", 4, 15000)]),
    (6, [("gelato_chocolate", 1, 15000)]),
    (12, [("gelato_pistachio", 1, 18000)]),  # slow mover
    (20, [("gelato_pistachio", 2, 18000)]),  # slow mover
    (1, [("cone_waffle", 6, 3000)]),
    (3, [("topping_sprinkles", 8, 2000)]),
]

# Chai House (S14): chai_masala is the clear top seller (cheap, frequent,
# often paired with a snack); chai_adrak/chai_kulhad are steady mid-tier;
# chai_green (premium) is the deliberate slow mover; biscuit_parle/
# samosa_aloo are attach items, mostly appearing alongside a chai order
# rather than alone — this is what makes attach_rate meaningful.
_CHAI_HOUSE_FIXTURES: list[Fixture] = [
    (1, [("chai_masala", 3, 8000), ("biscuit_parle", 3, 2000)]),
    (1, [("chai_kulhad", 2, 12000)]),
    (2, [("chai_masala", 5, 8000)]),
    (2, [("samosa_aloo", 4, 15000), ("chai_masala", 4, 8000)]),
    (3, [("chai_adrak", 3, 9000)]),
    (3, [("chai_masala", 2, 8000), ("biscuit_parle", 2, 2000)]),
    (4, [("chai_kulhad", 3, 12000)]),
    (4, [("chai_masala", 6, 8000)]),
    (5, [("chai_adrak", 2, 9000), ("samosa_aloo", 2, 15000)]),
    (6, [("chai_masala", 4, 8000)]),
    # chai_green's most recent sale is 9 days ago (nothing in the last 7) —
    # deliberate: DECISION-034's growth-loop stall detector should be able to
    # find this SKU as a live demo case, not just in synthetic tests.
    (9, [("chai_green", 1, 10000)]),  # slow mover
    (7, [("chai_masala", 3, 8000), ("biscuit_parle", 4, 2000)]),
    (9, [("chai_adrak", 4, 9000)]),
    (10, [("chai_kulhad", 1, 12000)]),
    (12, [("chai_masala", 2, 8000)]),
    (14, [("chai_green", 1, 10000)]),  # slow mover
    (15, [("samosa_aloo", 3, 15000)]),
    (18, [("chai_adrak", 2, 9000)]),
    (20, [("chai_masala", 3, 8000)]),
    (22, [("chai_green", 2, 10000)]),  # slow mover
    (25, [("chai_kulhad", 2, 12000)]),
    (28, [("biscuit_parle", 5, 2000)]),
]

_FIXTURES_BY_MERCHANT: dict[str, list[Fixture]] = {
    "gelateria-milano": _GELATERIA_FIXTURES,
    "chai-house": _CHAI_HOUSE_FIXTURES,
}


def _seed_cart(items: list[LineItem]) -> dict[str, Any]:
    amount_minor = sum(unit_minor * qty for _sku, qty, unit_minor in items)
    return {
        "items": [
            {"sku": sku, "qty": qty, "unit_minor": unit_minor, "tags": []}
            for sku, qty, unit_minor in items
        ],
        "cart_hash": f"sha256:{secrets.token_hex(16)}",
        "cart_version": 1,
        "amount_minor": amount_minor,
        "currency": "INR",
    }


def seed(merchant_id: str, config_path: str, db_url: str | None = None) -> int:
    if merchant_id not in _FIXTURES_BY_MERCHANT:
        known = ", ".join(sorted(_FIXTURES_BY_MERCHANT))
        raise ValueError(f"no seed fixtures for merchant_id {merchant_id!r}; known: {known}")
    fixtures = _FIXTURES_BY_MERCHANT[merchant_id]

    config = load_config(config_path)
    init_database(config)

    from openstore.core.database import _engine  # type: ignore

    engine = _engine if db_url is None else create_engine(db_url)

    now = datetime.now(UTC)

    created = 0
    with Session(engine) as session:
        for days_ago, items in fixtures:
            ck_id = f"chk_seed_{secrets.token_hex(6)}"
            ts = now - timedelta(days=days_ago)
            cart = _seed_cart(items)
            ck = Checkout(
                id=ck_id,
                trace_id=f"trace_seed_{secrets.token_hex(4)}",
                client_id="demo_seed",
                merchant_id=merchant_id,
                cart_hash=f"sha256:{secrets.token_hex(8)}",
                cart_version=1,
                amount_minor=cart["amount_minor"],
                currency="INR",
                state=OrderState.PAID,
                policy_id="pol_seed",
                policy_hash="ph_seed",
                aal_level=2,
                expires_at=ts + timedelta(hours=1),
                idempotency_key=f"seed_{ck_id}",
                cart_snapshot=cart,
                created_at=ts,
                updated_at=ts,
            )
            session.add(ck)
            created += 1
        session.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo analytics history")
    parser.add_argument("merchant_id", help="Merchant id (e.g. gelateria-milano, chai-house)")
    parser.add_argument(
        "--config",
        default="configs/gelateria.yaml",
        help="Path to the merchant config YAML (default: configs/gelateria.yaml)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Override database URL (default: use config.database.url)",
    )
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 4  # verifier-style usage error

    try:
        n = seed(args.merchant_id, args.config, args.db)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4

    print(json.dumps({"seeded_checkouts": n, "merchant_id": args.merchant_id}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
