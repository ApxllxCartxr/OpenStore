"""Headroom Bundler — Agent 3 (GROWTH_AGENTS.md §4).

At `checkout_initiate`, propose at most one add-on that (a) is policy-compliant,
(b) fits inside the headroom between the cart and the signed per-tx ceiling, and
(c) is complementary by `related_skus` or shared occasion tag. The offer is
advisory, structured arithmetic (R4.1c), and the buyer may decline — after which
it is not re-offered for that checkout (R4.1d).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from openstore import compiler as compiler_mod

from ..catalog import Product, context_for, items_for_skus, policy_from_dict


@dataclass(slots=True)
class BundleSuggestion:
    sku: str
    title: str
    unit_minor: int
    new_total_minor: int
    headroom_remaining_minor: int
    policy_compliant: bool
    rationale: str


@dataclass(slots=True)
class BundlerSession:
    """Per-session decline memory (R4.1d). No cross-session memory in v0.1."""

    declined: set = field(default_factory=set)

    def key(self, client_id: str, policy_hash: str) -> Tuple[str, str]:
        return (client_id, policy_hash)

    def declined_once(self, client_id: str, policy_hash: str) -> bool:
        return self.key(client_id, policy_hash) in self.declined

    def record_decline(self, client_id: str, policy_hash: str) -> None:
        self.declined.add(self.key(client_id, policy_hash))


def _cart_total(catalog: Dict[str, Product], cart_skus: List[str]) -> int:
    return sum(catalog[s].price_minor for s in cart_skus if s in catalog)


def _complementary(addon: Product, cart: List[Product]) -> bool:
    cart_skus = {c.sku for c in cart}
    cart_occasions = {t for c in cart for t in c.occasion_tags}
    for c in cart:
        if addon.sku in c.related_skus:
            return True
    return bool(set(addon.occasion_tags) & cart_occasions) and addon.sku not in cart_skus


def propose_bundle(
    cart_skus: List[str],
    policy: dict,
    catalog: Dict[str, Product],
    *,
    client_id: str = "",
    session: Optional[BundlerSession] = None,
    policy_hash: str = "",
) -> Optional[BundleSuggestion]:
    """R4.1 — at most one suggestion; combined basket must compile ALLOW."""
    cpolicy = policy_from_dict(policy)
    if session is not None and session.declined_once(client_id, policy_hash):
        return None

    cart = [catalog[s] for s in cart_skus if s in catalog]
    cart_total = _cart_total(catalog, cart_skus)
    headroom = cpolicy.max_spend_per_tx_minor - cart_total
    if headroom <= 0:
        return None

    candidates = []
    for p in catalog.values():
        if p.sku in {c.sku for c in cart}:
            continue
        if p.price_minor > headroom:
            continue
        if not _complementary(p, cart):
            continue
        # combined basket must still pass compile_decision (R4.1b)
        combined = cart_skus + [p.sku]
        verdict = compiler_mod.compile_decision(
            items_for_skus(catalog, combined), cpolicy, context_for(cpolicy))
        if verdict.verdict != "ALLOW":
            continue
        candidates.append((p.price_minor, p.sku, p))

    if not candidates:
        return None
    candidates.sort(key=lambda t: (-t[0], t[2].sku))
    price, _, addon = candidates[0]
    new_total = cart_total + price
    return BundleSuggestion(
        sku=addon.sku,
        title=addon.title,
        unit_minor=price,
        new_total_minor=new_total,
        headroom_remaining_minor=cpolicy.max_spend_per_tx_minor - new_total,
        policy_compliant=True,
        rationale="complementary add-on within your signed per-tx headroom",
    )
