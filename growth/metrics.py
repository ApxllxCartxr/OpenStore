"""Agent-era growth metrics (GROWTH_AGENTS.md §2.2 and §6).

Each metric is a pure function of a list of swarm run records plus the catalog
it ran against. `policy_fit_rate` is computed through `openstore.blast_radius`
(R2.2a) — never re-derived here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openstore import blast_radius as br

from .catalog import Product, policy_from_dict


@dataclass(frozen=True, slots=True)
class SwarmRun:
    persona_id: str
    policy: dict
    found_candidate: bool = False
    candidate_rank: Optional[int] = None
    tool_calls: int = 0
    submitted_cart: bool = False
    compiler_verdict: str = "DENY"
    compiler_reason: Optional[str] = None
    completed: bool = False
    basket_minor: int = 0
    headroom_minor: int = 0


@dataclass(slots=True)
class SwarmMetrics:
    n: int
    agent_discovery_rate: float
    policy_fit_rate: float
    agent_conversion_rate: float
    mean_tool_calls: float
    headroom_capture_rate: float
    blocked_rate_by_reason: Dict[str, float]
    raw_counts: Dict[str, int] = field(default_factory=dict)


def reachable_fraction(policy: dict, catalog: Dict[str, Product]) -> float:
    """Fraction of the catalog reachable under `policy` (R2.2a)."""
    if not catalog:
        return 0.0
    catalog_list = [p.to_dict() for p in catalog.values()]
    radius = br.compute_blast_radius(policy, catalog_list)
    allowed = radius["allowed_sku_count"]
    return allowed / len(catalog)


def compute_metrics(runs: List[SwarmRun], catalog: Dict[str, Product]) -> SwarmMetrics:
    n = len(runs)
    if n == 0:
        return SwarmMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, {})

    discovery = sum(1 for r in runs if r.found_candidate)
    completed = [r for r in runs if r.completed]
    completed_n = len(completed)

    policy_fits = [reachable_fraction(r.policy, catalog) for r in runs]

    total_headroom = sum(r.headroom_minor for r in runs)
    total_cap = sum(r.policy["max_spend_per_tx_minor"] for r in runs)
    headroom_capture = 1.0 - (total_headroom / total_cap) if total_cap else 0.0

    reason_counts: Dict[str, int] = {}
    for r in runs:
        if r.compiler_verdict == "DENY" and r.compiler_reason:
            reason_counts[r.compiler_reason] = reason_counts.get(r.compiler_reason, 0) + 1

    return SwarmMetrics(
        n=n,
        agent_discovery_rate=discovery / n,
        policy_fit_rate=sum(policy_fits) / n,
        agent_conversion_rate=completed_n / n,
        mean_tool_calls=(sum(r.tool_calls for r in completed) / completed_n) if completed_n else 0.0,
        headroom_capture_rate=headroom_capture,
        blocked_rate_by_reason={k: v / n for k, v in reason_counts.items()},
        raw_counts={
            "found_candidate": discovery,
            "completed": completed_n,
            "denied": sum(1 for r in runs if r.compiler_verdict == "DENY"),
        },
    )
