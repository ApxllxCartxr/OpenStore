"""The demo Provider. Declares every method, moves no real money.

`make_link` returns a link to a local page with Approve and Decline; Approve
fires a correctly-HMAC'd webhook after a short delay, Decline fires a failure
webhook immediately (§16.10). Link lifetime is 15 minutes, matching §16.7's
`confirmed` payment window — the sidecar releases at the link's own expiry and
never later than the Provider's lifetime, so those two numbers must agree.

It is the demo default, and CI runs it. It is not a mock: the same webhook
verification, the same reconcile, the same adoption path.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from openstore.sidecar.core.codes import PaymentMethod
from openstore.sidecar.provider.trait import (
    PaymentLink,
    PaymentProvider,
    PaymentStatus,
    RefundResult,
)


@dataclass
class _Link:
    link_id: str
    order_id: str
    amount_minor: int
    currency: str
    paid: bool = False
    cancelled: bool = False
    reference: str = ""


@dataclass
class FakeProvider(PaymentProvider):
    """Every method, no rail."""

    name: str = "fake"
    links: dict[str, _Link] = field(default_factory=dict)
    refunds: dict[str, RefundResult] = field(default_factory=dict)
    #: Set to make `make_link` raise after the link exists but before the caller
    #: hears about it — the crash-mid-link case that `check_status` adopts.
    crash_after_create: bool = False

    def declared_methods(self) -> frozenset[PaymentMethod]:
        return frozenset(PaymentMethod)

    async def make_link(
        self, order_id: str, amount_minor: int, currency: str, *, expires_in_seconds: int = 900
    ) -> PaymentLink:
        link_id = f"link_{secrets.token_hex(8)}"
        self.links[link_id] = _Link(link_id, order_id, amount_minor, currency)
        if self.crash_after_create:
            raise ConnectionError("crashed after the link was created, before the response")
        return PaymentLink(
            link_id=link_id,
            url=f"http://spoiledduckie.localhost/agentic/fake-pay/{link_id}",
            amount_minor=amount_minor,
            currency=currency,
            expires_in_seconds=expires_in_seconds,
        )

    async def check_status(self, link_id: str) -> PaymentStatus:
        link = self.links[link_id]
        return PaymentStatus(
            link_id=link.link_id,
            order_id=link.order_id,
            paid=link.paid,
            amount_minor=link.amount_minor,
            currency=link.currency,
            reference=link.reference,
            cancelled=link.cancelled,
        )

    async def cancel(self, link_id: str) -> None:
        self.links[link_id].cancelled = True

    async def refund(
        self, reference: str, amount_minor: int, currency: str, *, refund_id: str
    ) -> RefundResult:
        result = RefundResult(refund_id=refund_id, amount_minor=amount_minor, currency=currency)
        self.refunds[refund_id] = result
        return result

    # ── demo controls, not part of the trait ─────────────────────────────────

    def approve(self, link_id: str, *, amount_minor: int | None = None) -> _Link:
        """The Consumer pressed Approve. `amount_minor` overrides so a test can
        produce the mismatch the reconcile exists to catch."""
        link = self.links[link_id]
        link.paid = True
        link.reference = f"pay_{secrets.token_hex(8)}"
        if amount_minor is not None:
            link.amount_minor = amount_minor
        return link

    def find_by_order(self, order_id: str) -> _Link | None:
        """What adoption uses: after a crash the sidecar has an order id and no
        link id, and must find the link rather than make a second one."""
        for link in self.links.values():
            if link.order_id == order_id:
                return link
        return None
