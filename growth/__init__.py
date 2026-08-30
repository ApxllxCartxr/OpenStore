"""Growth agent package (docs/GROWTH_AGENTS.md).

Merchant-side agents that make money from agent traffic. None of them can move
money (§7.1): every module here imports only the *read-only* `compile_decision`
from the authorization layer to validate its own proposals, never a write path,
a signing key, or a PSP client.

Package layout:
    growth/catalog.py      — product model + catalog loading
    growth/personas.py     — swarm persona loading + weight validation
    growth/metrics.py      — aggregate agent-era growth metrics
    growth/swarm/          — Agent 1, Synthetic Buyer Swarm
    growth/recovery/       — Agent 2, Blocked-Cart Recovery
    growth/bundler/        — Agent 3, Headroom Bundler
    growth/axo/            — Agent 4, Catalog Optimizer (AXO loop)
    growth/console_panel.py— 4th Agent Console panel
"""

from __future__ import annotations

__all__ = ["catalog", "personas", "metrics", "swarm", "recovery", "bundler", "axo"]
