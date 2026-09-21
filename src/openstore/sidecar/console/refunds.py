"""The refund queue — what `request-refund` puts something *into*.

`request-refund` has been in the closed tool set since hour 0 and refused as
unwired, which was the honest answer while there was nowhere for a request to
go. The reason it could not simply be implemented is the whole shape of the
product: **a refund moves money and belongs to the Merchant.** An agent that
could refund could move money out of a shop it holds no credential for.

So the tool does the only thing it may: it records an ask. This module is the
ask, and `/agentic` → Refunds is where the Merchant sees it. Money moves later,
through `/agentic/refund`, which the Merchant's own admin calls over HMAC — the
same route it already used, now also closing the request that prompted it.

Three rules, each closing a way this becomes a money path by accident:

- **A request holds no amount the agent chose.** The agent asks about an order;
  what is refunded is the Merchant's decision and is carried by the Ledger
  entry, not by the ask.
- **One open request per order.** An agent that could queue a hundred requests
  for one order owns the Merchant's attention, which is a denial of service
  with extra steps.
- **Only an order that has money to give back.** Requesting a refund on a
  `pending` order is a cancellation, which is a different act with a different
  route and no money in it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from openstore.sidecar.core.codes import OrderStatus, ReasonCode, RefundRequestState
from openstore.sidecar.trait.errors import TraitError

#: A refund gives back money that arrived. Everything else is a cancellation.
REFUNDABLE_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.PAID, OrderStatus.COMPLETED, OrderStatus.REFUNDED}
)


@dataclass
class RefundRequest:
    """One ask, and who made it.

    `agent_id` is kept because the Merchant is entitled to know which agent is
    asking — a shop reading its queue is looking at other people's customers,
    and an agent that asks constantly is a fact worth seeing.
    """

    request_id: str
    order_id: str
    agent_id: str
    reason: str
    requested_at: datetime
    state: RefundRequestState = RefundRequestState.REQUESTED
    resolved_at: datetime | None = None
    resolution_note: str = ""

    @property
    def open(self) -> bool:
        return self.state is RefundRequestState.REQUESTED

    def to_row(self) -> dict[str, object]:
        """The console's row shape. No amount: the agent named none."""
        return {
            "request_id": self.request_id,
            "order_id": self.order_id,
            "agent_id": self.agent_id,
            "reason": self.reason,
            "requested_at": self.requested_at.isoformat(),
            "state": self.state.value,
            "resolution_note": self.resolution_note,
        }


@dataclass
class RefundQueue:
    """In-memory, like the Pending Carts beside it. The Merchant's own book is
    the durable record of a refund; this is the inbox in front of it."""

    requests: dict[str, RefundRequest] = field(default_factory=dict)

    def open_for(self, order_id: str) -> RefundRequest | None:
        return next((r for r in self.requests.values() if r.order_id == order_id and r.open), None)

    def request(
        self,
        *,
        order_id: str,
        agent_id: str,
        reason: str,
        order_status: OrderStatus,
        now: datetime | None = None,
    ) -> RefundRequest:
        """Queue an ask, or refuse it. **Moves no money and promises none.**"""
        if order_status not in REFUNDABLE_STATUSES:
            raise TraitError(
                ReasonCode.CANCEL_NOT_ALLOWED,
                f"that order is {order_status.value}; there is no money to give back yet. "
                f"Before it is paid the instrument is a cancellation, not a refund.",
            )

        existing = self.open_for(order_id)
        if existing is not None:
            # Idempotent rather than a second row: asking twice is not two asks,
            # and a queue an agent can flood is the Merchant's attention spent
            # by somebody else.
            return existing

        request = RefundRequest(
            request_id=f"rfrq_{secrets.token_hex(8)}",
            order_id=order_id,
            agent_id=agent_id,
            reason=reason.strip()[:500],
            requested_at=now or datetime.now(UTC),
        )
        self.requests[request.request_id] = request
        return request

    def resolve(
        self,
        order_id: str,
        state: RefundRequestState,
        *,
        note: str = "",
        now: datetime | None = None,
    ) -> RefundRequest | None:
        """Close the open request for an order, if there is one.

        Returns `None` rather than raising when there is none: a Merchant
        refunding an order nobody asked about is the ordinary case, and it must
        not fail because the queue is empty.
        """
        if state is RefundRequestState.REQUESTED:
            raise ValueError("resolving to `requested` is not a resolution")
        request = self.open_for(order_id)
        if request is None:
            return None
        request.state = state
        request.resolved_at = now or datetime.now(UTC)
        request.resolution_note = note.strip()[:500]
        return request

    def rows(self) -> list[dict[str, object]]:
        """Open asks first, then resolved ones, newest first within each. The
        queue is read to find work, so the work sorts to the top."""
        newest = sorted(self.requests.values(), key=lambda r: r.requested_at, reverse=True)
        # Two stable passes rather than one composite key: the second keeps the
        # first's order inside each group, and it reads as the two rules it is.
        return [r.to_row() for r in sorted(newest, key=lambda r: not r.open)]


@lru_cache(maxsize=1)
def get_refund_queue() -> RefundQueue:
    return RefundQueue()
