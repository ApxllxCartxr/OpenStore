"""AXO VARIANT step — emit a candidate catalog, never mutate the live one
(GROWTH_AGENTS.md §5.1 / R5.2e).

`catalog_variant.yaml` applies the audited tag additions to a *copy* of the
catalog and is written to a proposals/working directory. This module never
writes to `config/*.yaml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from ..catalog import Product
from .auditor import AuditReport, TagProposal


def apply_proposals(catalog: Dict[str, Product], proposals: List[TagProposal]
                    ) -> Dict[str, Product]:
    """Return a new catalog with proposed tags merged in (input untouched)."""
    out: Dict[str, Product] = {}
    for sku, p in catalog.items():
        out[sku] = p
    for prop in proposals:
        p = out.get(prop.sku)
        if p is None:
            continue
        if prop.add_tag in p.tags:
            continue
        out[prop.sku] = Product(
            sku=p.sku, title=p.title, description=p.description,
            price_minor=p.price_minor,
            tags=p.tags + (prop.add_tag,),
            related_skus=p.related_skus,
            occasion_tags=p.occasion_tags,
        )
    return out


def emit_variant(catalog: Dict[str, Product], report: AuditReport,
                 path: str | Path) -> Path:
    """Write `catalog_variant.yaml`. Raises if the target is under config/."""
    path = Path(path)
    if "config" in path.parts:
        raise RuntimeError("AXO must not write to config/ (R5.2e)")
    variant = apply_proposals(catalog, report.tag_proposals)
    data = [p.to_dict() for p in variant.values()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path
