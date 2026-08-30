"""AXO PROPOSE step — write a draft proposal, never auto-apply
(GROWTH_AGENTS.md §5.1 / R5.2b, R5.2e, R7.2b).

The optimizer writes `growth/axo/proposals/<ts>.yaml`. A merchant applies it
with an explicit CLI command; nothing here writes `config/*.yaml` (R5.2e). Every
proposal is screened through the injection detector before it is presented
(R7.2b); a flagged proposal is dropped and logged, never offered.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from redteam.detector import Detection, detect_instruction

from ..catalog import Product
from .auditor import AuditReport
from .lift import LiftResult

PROPOSALS_DIR = Path(__file__).parent / "proposals"


def screen_text(text: str) -> Detection:
    return detect_instruction(text)


def screen_catalog(catalog: Dict[str, Product]) -> List[Detection]:
    """R7.2c — run the detector over a whole catalog's product copy."""
    out: List[Detection] = []
    for p in catalog.values():
        for field in (p.title, p.description, *p.tags, *p.occasion_tags):
            d = detect_instruction(str(field))
            if d.flagged:
                out.append(d)
    return out


def screen_proposals(report: AuditReport
                     ) -> Tuple[List[AuditReport], List[TagProposal]]:
    """Drop and log any proposal whose evidence text is agent-directed."""
    from .auditor import AuditReport as _AR, TagProposal

    kept: List[TagProposal] = []
    dropped: List[TagProposal] = []
    for prop in report.tag_proposals:
        if detect_instruction(prop.evidence).flagged or detect_instruction(prop.add_tag).flagged:
            dropped.append(prop)
            continue
        kept.append(prop)
    return _AR(tag_proposals=kept, notes=list(report.notes)), dropped


def write_proposal(report: AuditReport, lift: LiftResult,
                   proposals_dir: str | Path = PROPOSALS_DIR,
                   ts: str | None = None) -> Path:
    proposals_dir = Path(proposals_dir)
    if "config" in proposals_dir.parts:
        raise RuntimeError("AXO must not write to config/ (R5.2e)")
    proposals_dir.mkdir(parents=True, exist_ok=True)

    screened, dropped = screen_proposals(report)
    ts = ts or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "draft": True,
        "generated_at": ts,
        "metric": lift.metric,
        "lift": {
            "delta": lift.delta,
            "ci_95": [lift.ci_low, lift.ci_high],
            "measurable": lift.measurable,
            "label": lift.label(),
            "n_per_arm": lift.n,
        },
        "tag_proposals": [
            {"sku": p.sku, "add_tag": p.add_tag,
             "evidence": p.evidence, "confidence": p.confidence}
            for p in screened.tag_proposals
        ],
        "notes": screened.notes,
        "dropped_by_injection_screen": [
            {"sku": p.sku, "add_tag": p.add_tag, "evidence": p.evidence}
            for p in dropped
        ],
    }
    path = proposals_dir / f"{ts}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path
