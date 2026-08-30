"""Blocked-Cart Recovery — Agent 2 (GROWTH_AGENTS.md §3).

Turns the compiler's rejection feed into qualified leads. The response is
selected by reason code (the mapping is closed, §3.1), every candidate is
validated by `compile_decision` before it is returned (R3.1a), and every
response is advisory (R3.1c). The agent holds no signing key and no PSP
credential (R3.1b) — it only reads the policy and catalog and proposes.

This module is the decision core; it is hosted in the merchant reasoning
agent process (see `merchant_agent/skills/recovery.py`) over A2A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openstore import compiler as compiler_mod

from ..catalog import Product, context_for, items_for_skus, policy_from_dict

# Reason codes for which the recovery agent may make an offer. Everything else
# is a client/structural error with no preference signal (§3.1).
_OFFERABLE = {
    "tag_violation",
    "sku_blocked",
    "spend_per_tx_exceeded",
    "spend_cumulative_exceeded",
}
_NO_OFFER = {
    "tx_count_exceeded": "Transaction count limit reached; an offer cannot convert.",
    "policy_expired": "Policy expired. Re-sign to continue.",
    "policy_not_yet_valid": "Policy not yet valid. Re-sign to continue.",
    "merchant_mismatch": "Client error: merchant mismatch.",
    "currency_mismatch": "Client error: currency mismatch.",
    "qty_invalid": "Client error: invalid quantity.",
    "sku_duplicate": "Client error: duplicate SKU in cart.",
}


@dataclass(frozen=True, slots=True)
class Denial:
    reason_code: str
    attempted_skus: List[str] = field(default_factory=list)
    offending_sku: Optional[str] = None
    spent_minor: int = 0
    transactions_count: int = 0


@dataclass(slots=True)
class Offer:
    skus: List[str]
    title: str
    unit_minor: int
    rationale: str


@dataclass(slots=True)
class RecoveryResponse:
    advisory: bool = True
    reason_code: str = ""
    offers: List[Offer] = field(default_factory=list)
    message: str = ""


def _reachable(product: Product, policy: compiler_mod.CompilerPolicy) -> bool:
    verdict = compiler_mod.compile_decision(
        (compiler_mod.CompilerItem(
            sku=product.sku, qty=1, unit_minor=product.price_minor,
            tags=product.tags),),
        policy, context_for(policy),
    )
    return verdict.verdict == "ALLOW"


def _validated_offer(catalog, skus, policy, rationale, title=None) -> Optional[Offer]:
    """R3.1a — only return an offer that itself passes compile_decision."""
    items = items_for_skus(catalog, skus)
    if not items:
        return None
    verdict = compiler_mod.compile_decision(items, policy, context_for(policy))
    if verdict.verdict != "ALLOW":
        return None
    total = sum(p.price_minor for p in (catalog[s] for s in skus))
    return Offer(skus=list(skus), title=title or ", ".join(catalog[s].title for s in skus),
                 unit_minor=total, rationale=rationale)


def _nearest_compliant(catalog, policy, offending_sku, *,
                       exclude_blocked_tag: Optional[str] = None) -> List[Product]:
    """Compliant substitutes ranked by related_skus overlap then price proximity."""
    offending = catalog.get(offending_sku)
    related = set(offending.related_skus) if offending else set()
    candidates = []
    for p in catalog.values():
        if p.sku == offending_sku:
            continue
        if exclude_blocked_tag and exclude_blocked_tag in p.tags:
            continue
        if not _reachable(p, policy):
            continue
        rel = 1 if p.sku in related else 0
        candidates.append((rel, -abs(p.price_minor - (offending.price_minor if offending else 0)), p.sku, p))
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in candidates]


def _best_single_under(catalog, policy, cap) -> Optional[Product]:
    best = None
    for p in catalog.values():
        if p.price_minor > cap:
            continue
        if not _reachable(p, policy):
            continue
        if best is None or p.price_minor > best.price_minor:
            best = p
    return best


def recover(denial: Denial, policy: dict,
            catalog: Dict[str, Product]) -> RecoveryResponse:
    cpolicy = policy_from_dict(policy)
    rc = denial.reason_code

    if rc not in _OFFERABLE:
        return RecoveryResponse(reason_code=rc, message=_NO_OFFER.get(rc, "No offer."))

    offers: List[Offer] = []

    if rc == "tag_violation":
        for p in _nearest_compliant(catalog, cpolicy, denial.offending_sku)[:3]:
            o = _validated_offer(catalog, [p.sku], cpolicy,
                                 f"compliant substitute for {denial.offending_sku} "
                                 f"(nearest on related_skus/price)")
            if o:
                offers.append(o)

    elif rc == "sku_blocked":
        # ponytail: exclude the blocked SKU and anything sharing its
        # distinguishing tag (its first tag) — avoids offering a sibling block.
        blocked = catalog.get(denial.offending_sku)
        distinguishing = blocked.tags[0] if blocked and blocked.tags else None
        for p in _nearest_compliant(catalog, cpolicy, denial.offending_sku,
                                    exclude_blocked_tag=distinguishing)[:3]:
            o = _validated_offer(catalog, [p.sku], cpolicy,
                                 f"substitute for blocked {denial.offending_sku}")
            if o:
                offers.append(o)

    elif rc == "spend_per_tx_exceeded":
        # largest compliant sub-basket <= cap, plus single best item that fits
        attempted = [s for s in denial.attempted_skus if s in catalog]
        attempted.sort(key=lambda s: catalog[s].price_minor, reverse=True)
        cap = cpolicy.max_spend_per_tx_minor
        subset: List[str] = []
        total = 0
        for s in attempted:
            price = catalog[s].price_minor
            if total + price <= cap or not subset:
                if total + price <= cap:
                    subset.append(s)
                    total += price
        if subset:
            o = _validated_offer(catalog, subset, cpolicy,
                                 "largest compliant sub-basket within your per-tx cap")
            if o:
                offers.append(o)
        best = _best_single_under(catalog, cpolicy, cap)
        if best:
            o = _validated_offer(catalog, [best.sku], cpolicy,
                                 "single best item within your per-tx cap")
            if o:
                offers.append(o)

    elif rc == "spend_cumulative_exceeded":
        remaining = cpolicy.max_spend_total_minor - denial.spent_minor
        if remaining > 0:
            best = _best_single_under(catalog, cpolicy, remaining)
            if best:
                o = _validated_offer(catalog, [best.sku], cpolicy,
                                     f"best item within remaining budget ({remaining} paise)")
                if o:
                    offers.append(o)

    return RecoveryResponse(reason_code=rc, offers=offers,
                            message="advisory recovery offers")
