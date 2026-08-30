"""Synthetic Buyer Swarm — Agent 1 (GROWTH_AGENTS.md §2).

A measurement substrate: N synthetic buyers, each a (persona, goal) pair, driven
through the catalog by a deterministic buyer heuristic, with the authorization
layer (`compile_decision`) as the only source of truth about what is allowed.

The catalog — not the agent — is the thing under test (§2.3). Runs are seeded
(R2.1b) so §5's paired comparison is valid, and the swarm refuses to run against
a live-payments instance (R2.2b).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from openstore import compiler as compiler_mod
from openstore.config import settings as openstore_settings

from ..catalog import (
    Product,
    context_for,
    items_for_skus,
    load_catalog,
    policy_from_dict,
)
from ..personas import (
    Persona,
    default_personas,
    validate_weights,
    weighted_sample,
)
from ..metrics import SwarmMetrics, SwarmRun, compute_metrics, reachable_fraction

GROWTH_SWARM_MODE_ENV = "GROWTH_SWARM_MODE"

DEFAULT_CATALOG_PATH = Path(__file__).parent.parent / "data" / "gelato_catalog.yaml"


def require_swarm_safe_mode(settings=openstore_settings) -> None:
    """R2.2b — refuse to run against a live-payments instance.

    The swarm MUST run against a dedicated test merchant with Razorpay disabled.
    If a live PSP key is configured, GROWTH_SWARM_MODE=1 is required; otherwise
    we hard-fail. This makes it impossible to point a swarm at real money.
    """
    live_psp = bool(getattr(settings, "razorpay_key_id", ""))
    if live_psp and os.environ.get(GROWTH_SWARM_MODE_ENV) != "1":
        raise RuntimeError(
            "refusing to run swarm against a live-payments instance: "
            "razorpay_key_id is set but GROWTH_SWARM_MODE != '1'"
        )


def stub_create_order(persona_id: str, basket_minor: int) -> str:
    """GROWTH_SWARM_MODE stub for order creation (no PSP, no signing key)."""
    if os.environ.get(GROWTH_SWARM_MODE_ENV) != "1":
        raise RuntimeError("stub_create_order requires GROWTH_SWARM_MODE=1")
    return f"swarm-order-{persona_id}-{basket_minor}"


# ---------------------------------------------------------------------------
# Synthetic buyer heuristic
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> List[str]:
    return [w.strip(".,!?") for w in text.lower().split() if w.strip(".,!?")]


def _relevance(product: Product, goal: str) -> float:
    """Lexical relevance of a product to a shopping goal (no LLM)."""
    goal_tokens = set(_tokenize(goal))
    if not goal_tokens:
        return 0.0
    blob = " ".join([product.title, product.description, *product.tags,
                     *product.occasion_tags]).lower()
    score = 0.0
    for tok in goal_tokens:
        if tok in blob:
            score += 1.0
        # tag-name tokens in the goal are strong signals
        if tok in product.tags:
            score += 2.0
    return score


def _ranked_products(catalog: Dict[str, Product], goal: str, rng: random.Random
                     ) -> List[Product]:
    products = list(catalog.values())
    rng.shuffle(products)  # seed-fixed tie-break
    products.sort(key=lambda p: (_relevance(p, goal), -p.price_minor, p.sku), reverse=True)
    return products


def _is_reachable(product: Product, policy: compiler_mod.CompilerPolicy) -> bool:
    verdict = compiler_mod.compile_decision(
        (compiler_mod.CompilerItem(
            sku=product.sku, qty=1, unit_minor=product.price_minor,
            tags=product.tags),),
        policy, context_for(policy),
    )
    return verdict.verdict == "ALLOW"


def _choose_purchase(ranked: List[Product], reachable: set,
                     policy: compiler_mod.CompilerPolicy) -> List[Product]:
    """The buyer tries to buy its best-match product (top of its ranked search
    that is reachable and fits the per-tx cap). It does *not* silently downshift
    to an unrelated cheap item — if nothing reachable fits the budget it
    abandons (agent-era friction), which is what makes the conversion metric
    non-trivial to measure.
    """
    cap = policy.max_spend_per_tx_minor
    for p in ranked:
        if p.sku in reachable and p.price_minor <= cap:
            return [p]
    return []


@dataclass(slots=True)
class SwarmResult:
    runs: List[SwarmRun] = field(default_factory=list)
    metrics: Optional[SwarmMetrics] = None


def run_swarm(
    *,
    n: int = 200,
    seed: int = 1,
    personas: Optional[List[Persona]] = None,
    catalog: Optional[Dict[str, Product]] = None,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    settings=openstore_settings,
) -> SwarmResult:
    """Run the swarm and return per-agent records + aggregate metrics (§2.2)."""
    require_swarm_safe_mode(settings)
    personas = personas or default_personas()
    validate_weights(personas)
    catalog = catalog or load_catalog(catalog_path)

    sampled = weighted_sample(personas, n, seed)
    runs: List[SwarmRun] = []
    for idx, persona in enumerate(sampled):
        rng = random.Random(f"{seed}:{idx}")
        policy = persona.compiler_policy()
        goal = rng.choice(list(persona.goals)) if persona.goals else ""
        ranked = _ranked_products(catalog, goal, rng)

        reachable = {p.sku for p in ranked if _is_reachable(p, policy)}
        found = len(reachable) > 0

        # candidate_rank: 1-based position of first reachable product in the
        # agent's own search results.
        candidate_rank = None
        for i, p in enumerate(ranked, start=1):
            if p.sku in reachable:
                candidate_rank = i
                break

        basket = _choose_purchase(ranked, reachable, policy)
        submitted = len(basket) > 0
        basket_minor = sum(p.price_minor for p in basket)

        compiler_verdict = "DENY"
        compiler_reason = None
        completed = False
        if basket:
            verdict = compiler_mod.compile_decision(
                items_for_skus(catalog, [p.sku for p in basket]),
                policy, context_for(policy),
            )
            compiler_verdict = verdict.verdict
            compiler_reason = verdict.reason_code
            if verdict.verdict == "ALLOW":
                completed = True
                # Only mint a stub order when explicitly in growth-swarm mode;
                # as a pure measurement (e.g. the AXO loop) we just record it.
                if os.environ.get(GROWTH_SWARM_MODE_ENV) == "1":
                    stub_create_order(persona.persona_id, basket_minor)

        headroom = max(0, policy.max_spend_per_tx_minor - basket_minor)
        runs.append(SwarmRun(
            persona_id=persona.persona_id,
            policy=persona.policy,
            found_candidate=found,
            candidate_rank=candidate_rank,
            tool_calls=1 + len(ranked[: max(1, candidate_rank or len(ranked))]),
            submitted_cart=submitted,
            compiler_verdict=compiler_verdict,
            compiler_reason=compiler_reason,
            completed=completed,
            basket_minor=basket_minor,
            headroom_minor=headroom,
        ))

    metrics = compute_metrics(runs, catalog)
    return SwarmResult(runs=runs, metrics=metrics)
