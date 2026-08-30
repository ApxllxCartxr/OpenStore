"""Agent Console — 4th growth metrics panel (GROWTH_AGENTS.md §6, R6.2).

The merchant-facing growth view is a fourth panel on the existing Agent Console
(``openstore/surfaces.py``) — not a second dashboard. This module renders the
panel from a ``SwarmMetrics`` so it can be embedded there. Every metric is
shown with its denominator (R6.1).
"""

from __future__ import annotations

from typing import Dict

from ..metrics import SwarmMetrics


def panel_data(metrics: SwarmMetrics) -> Dict:
    """Machine-readable panel payload, denominators included (R6.1)."""
    n = metrics.n or 1
    return {
        "panel": "growth",
        "n_agents": metrics.n,
        "agent_discovery_rate": {
            "value": metrics.agent_discovery_rate,
            "denominator": metrics.n,
            "raw": metrics.raw_counts.get("found_candidate", 0),
        },
        "policy_fit_rate": metrics.policy_fit_rate,
        "agent_conversion_rate": {
            "value": metrics.agent_conversion_rate,
            "denominator": metrics.n,
            "raw": metrics.raw_counts.get("completed", 0),
        },
        "mean_tool_calls": metrics.mean_tool_calls,
        "headroom_capture_rate": metrics.headroom_capture_rate,
        "blocked_rate_by_reason": metrics.blocked_rate_by_reason,
    }


def panel_html(metrics: SwarmMetrics) -> str:
    d = panel_data(metrics)
    rows = []
    rows.append(f"<tr><td>agent_discovery_rate</td><td>{d['agent_discovery_rate']['value']:.3f}"
                f"</td><td>{d['agent_discovery_rate']['raw']}/{d['agent_discovery_rate']['denominator']}</td></tr>")
    rows.append(f"<tr><td>policy_fit_rate</td><td>{d['policy_fit_rate']:.3f}</td><td>-</td></tr>")
    rows.append(f"<tr><td>agent_conversion_rate</td><td>{d['agent_conversion_rate']['value']:.3f}"
                f"</td><td>{d['agent_conversion_rate']['raw']}/{d['agent_conversion_rate']['denominator']}</td></tr>")
    rows.append(f"<tr><td>mean_tool_calls</td><td>{d['mean_tool_calls']:.2f}</td><td>completed</td></tr>")
    rows.append(f"<tr><td>headroom_capture_rate</td><td>{d['headroom_capture_rate']:.3f}</td><td>-</td></tr>")
    for reason, rate in d["blocked_rate_by_reason"].items():
        raw = round(rate * d["n_agents"])
        rows.append(f"<tr><td>blocked:{reason}</td><td>{rate:.3f}</td><td>{raw}/{d['n_agents']}</td></tr>")
    return (
        "<section id='growth-panel'><h3>Agent Growth Metrics</h3>"
        "<table class='metrics'><thead><tr><th>metric</th><th>value</th><th>denominator</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></section>"
    )
