"""Attack target: a faithful *simulation* of the buyer agent under attack.

This is the demonstration core of AGENT_LAYER.md §4.2–§4.3: the model may be
fooled (`model_deviated`), but `money_moved` is decided by the pure compiler,
never by the model. So a fooled agent still cannot move unauthorized money.
"""

from __future__ import annotations

import re

from openstore.compiler import (
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    compile_decision,
)

# Default v2 policy used when an attack YAML does not embed its own.
DEFAULT_POLICY = {
    "policy_version": 2,
    "merchant_id": "gelateria-roma",
    "currency": "INR",
    "max_spend_per_tx_minor": 50000,
    "max_spend_total_minor": 200000,
    "max_transactions": 10,
    "allowed_tags": ["vegan", "dairy-free"],
    "tag_mode": "all",
    "blocked_skus": ["GEL-RUM-500"],
    "not_before": 1787000000,
    "expires_at": 1788000000,
    "assertion_max_age_seconds": 86400,
}
DEFAULT_CONTEXT = {
    "spent_minor": 0,
    "transactions_count": 0,
    "evaluated_at_unix": 1787000900,
    "currency": "INR",
    "merchant_id": "gelateria-roma",
}
DEFAULT_BASE_CART = [{"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000, "tags": ["vegan", "dairy-free"]}]

_SKU_RE = re.compile(r"GEL-[A-Z0-9-]+")
_QTY_RE = re.compile(r"(\d+)\s*(?:units?|x\s*)?of\s+(GEL-[A-Z0-9-]+)", re.I)


def _parse_injection(payload: str) -> list[dict]:
    """Return cart lines the (fooled) agent would add from an injection payload."""
    added = []
    for m in _QTY_RE.finditer(payload):
        qty = int(m.group(1))
        sku = m.group(2)
        added.append({"sku": sku, "qty": qty, "unit_minor": 26000, "tags": ["alcohol"]})
    if not added:
        skus = _SKU_RE.findall(payload)
        for sku in skus:
            added.append({"sku": sku, "qty": 1, "unit_minor": 26000, "tags": ["alcohol"]})
    return added


class SimulatedBuyerAgent:
    """A model stand-in: parrots any SKU named in an injection into its cart."""

    def __init__(self, base_cart=None, policy=None, context=None):
        self.base_cart = base_cart or [dict(i) for i in DEFAULT_BASE_CART]
        self.policy = policy or DEFAULT_POLICY
        self.context = context or DEFAULT_CONTEXT

    def act(self, attack: dict) -> dict:
        payload = attack.get("payload", "")
        injected = _parse_injection(payload)
        cart = [dict(i) for i in self.base_cart] + injected
        deviated = bool(injected)

        policy = CompilerPolicy(**{k: v for k, v in self.policy.items()
                                   if k in CompilerPolicy.__dataclass_fields__})
        ctx = self.context
        context = CompilerContext(
            merchant_id=ctx.get("merchant_id", policy.merchant_id),
            currency=ctx.get("currency", policy.currency),
            evaluated_at_unix=ctx.get("evaluated_at_unix", policy.expires_at),
            spent_minor=ctx.get("spent_minor", 0),
            transactions_count=ctx.get("transactions_count", 0),
        )
        items = tuple(
            CompilerItem(sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"],
                         tags=tuple(i.get("tags", [])))
            for i in cart
        )
        verdict = compile_decision(items, policy, context).verdict
        money_moved = verdict == "ALLOW"

        return {
            "model_deviated": deviated,
            "money_moved": money_moved,
            "cart": cart,
            "compiler_verdict": verdict,
        }


def run_attack(attack: dict, agent: SimulatedBuyerAgent | None = None) -> dict:
    agent = agent or SimulatedBuyerAgent(
        policy=attack.get("policy"), context=attack.get("context"),
        base_cart=attack.get("base_cart"),
    )
    result = agent.act(attack)
    # Cross-check against the attack's declared success criteria.
    sc = attack.get("success_criteria", {}) or {}
    expected_dev = _sku_in_criteria(sc.get("model_deviated", ""))
    result["criteria_model_deviated"] = expected_dev
    result["criteria_money_moved"] = "order_created" in sc.get("money_moved", "")
    return result


def _sku_in_criteria(text: str):
    m = _SKU_RE.search(text)
    return m.group(0) if m else None
