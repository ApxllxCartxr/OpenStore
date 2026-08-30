"""`python -m growth.swarm` — run the Synthetic Buyer Swarm (demo beat G1)."""

from __future__ import annotations

import argparse
import os
import sys

from .runner import run_swarm


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenStore Synthetic Buyer Swarm")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--catalog", default=None)
    args = parser.parse_args()

    os.environ.setdefault("GROWTH_SWARM_MODE", "1")
    result = run_swarm(n=args.n, seed=args.seed, catalog_path=args.catalog or "growth/data/gelato_catalog.yaml")
    m = result.metrics
    print(f"Swarm run: {m.n} agents, seed={args.seed}")
    print(f"  agent_discovery_rate    = {m.agent_discovery_rate:.3f}  (raw {m.raw_counts.get('found_candidate')})")
    print(f"  policy_fit_rate         = {m.policy_fit_rate:.3f}")
    print(f"  agent_conversion_rate   = {m.agent_conversion_rate:.3f}  (raw {m.raw_counts.get('completed')})")
    print(f"  mean_tool_calls         = {m.mean_tool_calls:.2f}")
    print(f"  headroom_capture_rate   = {m.headroom_capture_rate:.3f}")
    if m.blocked_rate_by_reason:
        print("  blocked_rate_by_reason  = " +
              ", ".join(f"{k}={v:.3f}" for k, v in m.blocked_rate_by_reason.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
