"""AXO SIMULATE + MEASURE steps — paired seeded lift with bootstrap 95% CI
(GROWTH_AGENTS.md §5.1 / R5.2a, R5.2b, R5.2c).

Baseline and variant MUST be evaluated against the identical seeded population
(R5.2a). We run the swarm on both with the same seed (so per-index agents are
matched) and bootstrap over the paired differences. A point estimate alone is
never returned (R5.2b); if the CI spans zero the result is NO_MEASURABLE_LIFT
and is reported anyway (R5.2c).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..catalog import Product
from ..swarm.runner import run_swarm

_PER_RUN_SCALAR = {
    "agent_conversion_rate": lambda r: 1.0 if r.completed else 0.0,
    "agent_discovery_rate": lambda r: 1.0 if r.found_candidate else 0.0,
}


@dataclass(frozen=True, slots=True)
class LiftResult:
    metric: str
    delta: float
    ci_low: float
    ci_high: float
    n: int
    measurable: bool  # False when the 95% CI spans zero (R5.2c)

    def label(self) -> str:
        return "NO_MEASURABLE_LIFT" if not self.measurable else f"+{self.delta:.4f}"


def _paired_differences(baseline_runs, variant_runs, scalar: Callable) -> List[float]:
    n = min(len(baseline_runs), len(variant_runs))
    return [scalar(variant_runs[i]) - scalar(baseline_runs[i]) for i in range(n)]


def _bootstrap_ci(differences: List[float], seed: int, b: int = 2000
                  ) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(differences)
    if n == 0:
        return (0.0, 0.0)
    means = []
    for _ in range(b):
        sample = [differences[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * (b - 1))]
    hi = means[int(0.975 * (b - 1))]
    return lo, hi


def paired_lift(
    baseline_catalog: Dict[str, Product],
    variant_catalog: Dict[str, Product],
    *,
    n: int = 200,
    seed: int = 1,
    metric: str = "agent_conversion_rate",
    bootstraps: int = 2000,
) -> LiftResult:
    if metric not in _PER_RUN_SCALAR:
        raise ValueError(f"unsupported metric: {metric}")
    scalar = _PER_RUN_SCALAR[metric]

    base = run_swarm(n=n, seed=seed, catalog=baseline_catalog).runs
    vari = run_swarm(n=n, seed=seed, catalog=variant_catalog).runs

    diffs = _paired_differences(base, vari, scalar)
    delta = sum(diffs) / len(diffs) if diffs else 0.0
    ci_low, ci_high = _bootstrap_ci(diffs, seed=seed, b=bootstraps)
    # R5.2c — not measurable when the 95% CI spans zero.
    measurable = not (ci_low <= 0 <= ci_high)
    return LiftResult(metric=metric, delta=delta, ci_low=ci_low, ci_high=ci_high,
                      n=len(diffs), measurable=measurable)
