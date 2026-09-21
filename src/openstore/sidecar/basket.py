"""The basket, which lives here and not in the agent.

An agent that keeps its own basket has two baskets and shows the wrong one. The
lines, the Destination, the Contact Point and the chosen fulfillment all live in
the sidecar; a chat's cart operations are calls, not local state (SPEC §11).

**Nothing here prices anything.** A total comes from door 9 or it does not
exist — the Merchant is truth, and a basket that could add up its own lines
would eventually disagree with the shop about what a Consumer owes.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from openstore.sidecar.trait.models import Destination, Line


@dataclass
class Basket:
    """One agent's working basket, before any checkout exists."""

    agent_id: str
    cart_id: str = field(default_factory=lambda: f"cart_{secrets.token_hex(8)}")
    lines: list[Line] = field(default_factory=list)
    destination: Destination | None = None
    contact: dict[str, str] = field(default_factory=dict)
    fulfillment_option_id: str = ""
    discount_code: str | None = None

    def add(self, sku: str, qty: int, parent: str | None = None) -> None:
        """Quantities accumulate per SKU: two `add-line` calls for one SKU are
        one line of two, not two lines the Merchant would quote separately."""
        for line in self.lines:
            if line.sku == sku and line.parent == parent:
                line.qty += qty
                return
        self.lines.append(Line(sku=sku, qty=qty, parent=parent))

    def remove(self, sku: str) -> bool:
        before = len(self.lines)
        # Add-ons hang off a parent line, so removing the parent removes them —
        # leaving an orphaned gift-wrap on a basket with nothing to wrap is how
        # a Consumer is charged for something they cannot receive.
        self.lines = [ln for ln in self.lines if ln.sku != sku and ln.parent != sku]
        return len(self.lines) != before

    def quotable(self) -> bool:
        return bool(self.lines and self.destination and self.fulfillment_option_id)


@dataclass
class BasketStore:
    baskets: dict[str, Basket] = field(default_factory=dict)

    def for_agent(self, agent_id: str) -> Basket:
        if agent_id not in self.baskets:
            self.baskets[agent_id] = Basket(agent_id=agent_id)
        return self.baskets[agent_id]

    def clear(self, agent_id: str) -> None:
        self.baskets.pop(agent_id, None)
