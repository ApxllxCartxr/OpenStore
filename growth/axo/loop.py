"""AXO loop orchestrator — Agent 4 (GROWTH_AGENTS.md §5).

AUDIT -> VARIANT -> SIMULATE -> MEASURE -> PROPOSE, closed and live (demo beat
G2). The optimizer never auto-applies: it writes a dated draft proposal that a
merchant applies explicitly (R5.2e).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from ..catalog import Product
from .auditor import AuditReport, audit_catalog
from .lift import LiftResult, paired_lift
from .proposer import PROPOSALS_DIR, write_proposal
from .variant import emit_variant

PROPOSALS_DIR = PROPOSALS_DIR  # re-export


@dataclass(slots=True)
class AxoRun:
    report: AuditReport
    variant_path: Path
    lift: LiftResult
    proposal_path: Path


def run_axo(
    catalog: Dict[str, Product],
    *,
    n: int = 200,
    seed: int = 1,
    metric: str = "agent_conversion_rate",
    proposals_dir: str | Path = PROPOSALS_DIR,
) -> AxoRun:
    report = audit_catalog(catalog)
    variant_path = emit_variant(catalog, report, Path(proposals_dir) / "catalog_variant.yaml")
    # variant catalog must be reloaded from disk to stay honest about mutation
    from ..catalog import load_catalog
    variant_catalog = load_catalog(variant_path)
    lift = paired_lift(catalog, variant_catalog, n=n, seed=seed, metric=metric)
    proposal_path = write_proposal(report, lift, proposals_dir=proposals_dir)
    return AxoRun(report=report, variant_path=variant_path, lift=lift,
                  proposal_path=proposal_path)
