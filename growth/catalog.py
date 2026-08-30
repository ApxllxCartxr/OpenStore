"""Product catalog model for growth agents (docs/GROWTH_AGENTS.md).

The swarm, recovery, bundler, and AXO all reason about a catalog of products.
The spec assumes a richer product record than the live `openstore` runtime
catalog (which only carries `title`/`blocked`): each product here carries
`price_minor`, `tags`, `related_skus`, and `occasion_tags` so the growth agents
have something legible to work with. A sample 12-SKU gelato catalog ships in
`growth/data/gelato_catalog.yaml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from openstore import compiler as compiler_mod


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    title: str
    description: str = ""
    price_minor: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)
    related_skus: tuple[str, ...] = field(default_factory=tuple)
    occasion_tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "title": self.title,
            "description": self.description,
            "price_minor": self.price_minor,
            "tags": list(self.tags),
            "related_skus": list(self.related_skus),
            "occasion_tags": list(self.occasion_tags),
        }


def load_catalog(path: str | Path) -> Dict[str, Product]:
    """Load a catalog YAML: a list of product dicts (or {sku: {...}})."""
    data = yaml.safe_load(Path(path).read_text())
    return _normalize(data)


def load_catalog_from_dict(catalog: Dict[str, dict]) -> Dict[str, Product]:
    """Normalise the live `openstore` runtime catalog (sku -> {title, blocked})."""
    items = []
    for sku, meta in catalog.items():
        meta = meta or {}
        items.append({
            "sku": sku,
            "title": meta.get("title", sku),
            "description": meta.get("description", ""),
            "price_minor": int(meta.get("price_minor", 0)),
            "tags": list(meta.get("tags", [])),
            "related_skus": list(meta.get("related_skus", [])),
            "occasion_tags": list(meta.get("occasion_tags", [])),
        })
    return {p["sku"]: Product(**p) for p in items}


def _normalize(data) -> Dict[str, Product]:
    if isinstance(data, dict) and data and all(isinstance(v, dict) for v in data.values()):
        raw = [{"sku": k, **v} for k, v in data.items()]
    else:
        raw = data if isinstance(data, list) else []
    out: Dict[str, Product] = {}
    for item in raw:
        item = dict(item)
        sku = item.get("sku")
        if not sku:
            continue
        out[sku] = Product(
            sku=sku,
            title=item.get("title", sku),
            description=item.get("description", ""),
            price_minor=int(item.get("price_minor", 0)),
            tags=tuple(item.get("tags", [])),
            related_skus=tuple(item.get("related_skus", [])),
            occasion_tags=tuple(item.get("occasion_tags", [])),
        )
    return out


def policy_from_dict(policy: dict) -> compiler_mod.CompilerPolicy:
    """Build a frozen CompilerPolicy from a persona policy dict."""
    return compiler_mod.CompilerPolicy(
        policy_version=int(policy.get("policy_version", 2)),
        merchant_id=policy.get("merchant_id", "merchant:openstore"),
        currency=policy.get("currency", "INR"),
        max_spend_per_tx_minor=int(policy.get("max_spend_per_tx_minor", 0)),
        max_spend_total_minor=int(policy.get("max_spend_total_minor", 0)),
        max_transactions=int(policy.get("max_transactions", 1)),
        allowed_tags=tuple(policy.get("allowed_tags", [])),
        tag_mode=policy.get("tag_mode", "all"),
        blocked_skus=tuple(policy.get("blocked_skus", [])),
        not_before=int(policy.get("not_before", 0)),
        expires_at=int(policy.get("expires_at", 2_000_000_000)),
        assertion_max_age_seconds=int(policy.get("assertion_max_age_seconds", 86400)),
    )


def context_for(policy: dict | compiler_mod.CompilerPolicy, *, spent_minor: int = 0,
                transactions_count: int = 0,
                evaluated_at_unix: int = 1_700_000_000) -> compiler_mod.CompilerContext:
    """A neutral evaluation context for a catalog-scoped simulation."""
    merchant_id = policy.merchant_id if isinstance(policy, compiler_mod.CompilerPolicy) else \
        policy.get("merchant_id", "merchant:openstore")
    currency = policy.currency if isinstance(policy, compiler_mod.CompilerPolicy) else \
        policy.get("currency", "INR")
    return compiler_mod.CompilerContext(
        merchant_id=merchant_id,
        currency=currency,
        evaluated_at_unix=evaluated_at_unix,
        spent_minor=spent_minor,
        transactions_count=transactions_count,
    )


def items_for_skus(catalog: Dict[str, Product], skus: List[str],
                   qty: int = 1) -> tuple[compiler_mod.CompilerItem, ...]:
    out = []
    for sku in skus:
        p = catalog.get(sku)
        if p is None:
            continue
        out.append(compiler_mod.CompilerItem(
            sku=p.sku, qty=qty, unit_minor=p.price_minor, tags=p.tags))
    return tuple(out)
