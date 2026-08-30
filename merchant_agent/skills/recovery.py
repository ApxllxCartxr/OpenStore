"""Merchant reasoning agent skills (hosts the growth agents over A2A).

GROWTH_AGENTS.md §3.1b: the Blocked-Cart Recovery agent runs in the merchant
reasoning agent process, which holds no signing key and no Razorpay write
credential. This module is a thin A2A-shaped host around `growth.recovery`.
"""

from __future__ import annotations

from typing import Any, Dict

from growth.catalog import load_catalog
from growth.recovery import Denial, recover

# ponytail: a single default catalog for the recovery host; the merchant agent
# would inject its own live catalog in production.
_CATALOG_PATH = "growth/data/gelato_catalog.yaml"


def handle_recovery(payload: Dict[str, Any]) -> Dict[str, Any]:
    """A2A entry point: recover from a compiler denial.

    `payload` carries the denial (reason_code, attempted_skus, offending_sku,
    spent_minor, transactions_count), the buyer `policy`, and optionally a
    `catalog_path`. Returns an advisory response; the buyer agent decides.
    """
    denial = Denial(
        reason_code=payload["reason_code"],
        attempted_skus=list(payload.get("attempted_skus", [])),
        offending_sku=payload.get("offending_sku"),
        spent_minor=int(payload.get("spent_minor", 0)),
        transactions_count=int(payload.get("transactions_count", 0)),
    )
    catalog = load_catalog(payload.get("catalog_path", _CATALOG_PATH))
    resp = recover(denial, payload["policy"], catalog)
    return {
        "advisory": True,
        "reason_code": resp.reason_code,
        "message": resp.message,
        "offers": [
            {"skus": o.skus, "title": o.title,
             "unit_minor": o.unit_minor, "rationale": o.rationale}
            for o in resp.offers
        ],
    }
