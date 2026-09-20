"""UCP — an envelope, and the one AP2 rides on.

UCP's totals breakdown maps 1:1 onto the Quote, so **this translator carries no
pricing logic**. `subtotal / items_discount / fulfillment / tax / total` are
read straight off a Quote the Merchant computed; anything computed here would be
a second money path.

Completion: direct-checkout-inside-AI maps to our same-domain approve handoff,
which is UCP's own buyer-escalation path and its embedded binding. What we
decline is the trust tier that completes without the buyer, not conformance
(SPEC §9).
"""

from __future__ import annotations

from typing import Any

from openstore.sidecar.core.codes import Protocol
from openstore.sidecar.trait.models import Quote


def totals(quote: Quote) -> dict[str, int]:
    """UCP's breakdown, read off the Quote. Sums are the Merchant's.

    `items_discount` is reported **positive** here because that is UCP's shape,
    while the Quote holds it negative with the sign intrinsic. The conversion is
    one `abs()` and it is the only arithmetic in this file — which is the point:
    a translator that started adding would be pricing.
    """
    return {
        "subtotal": quote.subtotal_minor,
        "items_discount": abs(sum(d.amount_minor for d in quote.discount_lines)),
        "fulfillment": quote.fulfillment_chosen.cost_minor,
        "tax": sum(t.amount_minor for t in quote.tax_lines),
        "total": quote.total_minor,
    }


def checkout(quote: Quote, *, approve_url: str, currency: str = "INR") -> dict[str, Any]:
    """A UCP checkout, terminating in the approve handoff."""
    return {
        "protocol": Protocol.UCP.value,
        "currency": currency,
        "totals": totals(quote),
        "lines": [
            {
                "sku": line.sku,
                "quantity": line.qty,
                "unit_amount": line.unit_price_minor,
                "amount": line.line_total_minor,
            }
            for line in quote.lines
        ],
        "tax_lines": [
            {
                "kind": t.kind,
                "rate_bp": t.rate_bp,
                "amount": t.amount_minor,
                "informational": t.informational,
            }
            for t in quote.tax_lines
        ],
        "completion": {
            "mode": "buyer_escalation",
            "approve_url": approve_url,
            "note": (
                "Completion is a same-domain handoff to the Merchant's own approve page. "
                "This is UCP's buyer-escalation path, not a deviation from it."
            ),
        },
    }
