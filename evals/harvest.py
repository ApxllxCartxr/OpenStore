"""Harvest real PoAI bundles into eval cases (AGENT_LAYER.md §3.1, R3.1a)."""

from __future__ import annotations

import json
from pathlib import Path

from openstore.models import EvidenceBundleRow
from openstore.canonical import digest

from .types import Case


def harvest_bundles(session, *, include_text: bool = False) -> list[Case]:
    """Read every EvidenceBundleRow and emit a `source="harvested"` case.

    R3.1a: `request_text` is redacted to its digest unless `include_text`.
    """
    rows = session.query(EvidenceBundleRow).all()
    cases: list[Case] = []
    for row in rows:
        bundle = row.bundle_json if isinstance(row.bundle_json, dict) else json.loads(row.bundle_json)
        hi = bundle.get("human_intent") or {}
        auth = bundle.get("authority") or {}
        adj = bundle.get("adjudication") or {}
        goods = bundle.get("goods") or {}
        ctx = adj.get("context", {})
        request_text = hi.get("request_text", "")
        if not include_text:
            request_text = digest({"text": request_text})
        policy = auth.get("policy", {})
        cases.append(
            Case(
                case_id=f"hv_{row.bundle_id}",
                request_text=request_text,
                policy=policy,
                catalog_digest=(goods.get("cart_hash") or ""),
                expected_safety=adj.get("verdict", "DENY"),
                expected_fidelity="unanswerable",  # not machine-decidable; hand-relabel later
                source="harvested",
                notes="auto-harvested",
                cart_items=goods.get("items", []),
                context={
                    "spent_minor": ctx.get("spent_minor", 0),
                    "transactions_count": ctx.get("transactions_count", 0),
                    "evaluated_at_unix": ctx.get("evaluated_at_unix", 0),
                    "currency": ctx.get("currency", "INR"),
                    "merchant_id": ctx.get("merchant_id", ""),
                },
            )
        )
    return cases


def write_cases(cases: list[Case], out_path: str | Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c.to_json(), ensure_ascii=False) + "\n")
