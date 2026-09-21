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
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Column as Col
from sqlalchemy import DateTime, Index, String, Table, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.core.codes import OrderStatus, ReasonCode, RefundRequestState
from openstore.sidecar.core.db import as_utc, rows_affected, session_scope
from openstore.sidecar.core.tables import metadata
from openstore.sidecar.trait.errors import TraitError

#: One row per ask. Durable since 09-21: the queue is the Merchant's inbox, and
#: an inbox that empties itself on restart loses a Consumer's request with no
#: trace that it was ever made.
refund_requests = Table(
    "refund_requests",
    metadata,
    Col("request_id", String(64), primary_key=True),
    Col("order_id", String(64), nullable=False),
    Col("agent_id", String(128), nullable=False),
    Col("reason", String(500), nullable=False),
    Col("requested_at", DateTime(timezone=True), nullable=False),
    Col("state", String(16), nullable=False),
    Col("resolved_at", DateTime(timezone=True), nullable=True),
    Col("resolution_note", String(500), nullable=False, default=""),
    Index("ix_refund_order", "order_id"),
    # **The index is what makes "one open request per order" true**, not the
    # lookup above the insert: two concurrent asks both read an empty queue and
    # both decide to write. Partial, because resolved rows for one order are
    # ordinary history and there may be many.
    Index(
        "uq_refund_open_per_order",
        "order_id",
        unique=True,
        sqlite_where=text("state = 'requested'"),
        postgresql_where=text("state = 'requested'"),
    ),
)

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


def _required(moment: datetime | None) -> datetime:
    """`requested_at` is `nullable=False`; this is the type checker's copy of
    that fact, not a runtime possibility."""
    assert moment is not None
    return moment


def _row_to_request(row: object) -> RefundRequest:
    return RefundRequest(
        request_id=row.request_id,  # type: ignore[attr-defined]
        order_id=row.order_id,  # type: ignore[attr-defined]
        agent_id=row.agent_id,  # type: ignore[attr-defined]
        reason=row.reason,  # type: ignore[attr-defined]
        requested_at=_required(as_utc(row.requested_at)),  # type: ignore[attr-defined]
        state=RefundRequestState(row.state),  # type: ignore[attr-defined]
        resolved_at=as_utc(row.resolved_at),  # type: ignore[attr-defined]
        resolution_note=row.resolution_note,  # type: ignore[attr-defined]
    )


@dataclass
class RefundQueue:
    """The Merchant's inbox, in the sidecar's own database.

    The Merchant's book is still the durable record of a *refund* — money that
    moved. This is the durable record of the **ask**, which is a different fact
    and one only the sidecar ever sees: an agent's request lives nowhere else,
    so a restart used to erase it along with the Consumer's reason for making
    it.
    """

    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This RefundQueue has no database, so an ask has nowhere to go. "
                "Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    async def open_for(self, order_id: str) -> RefundRequest | None:
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(
                    select(refund_requests).where(
                        refund_requests.c.order_id == order_id,
                        refund_requests.c.state == RefundRequestState.REQUESTED.value,
                    )
                )
            ).first()
        return _row_to_request(row) if row is not None else None

    async def get(self, request_id: str) -> RefundRequest | None:
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(
                    select(refund_requests).where(refund_requests.c.request_id == request_id)
                )
            ).first()
        return _row_to_request(row) if row is not None else None

    async def request(
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

        request = RefundRequest(
            request_id=f"rfrq_{secrets.token_hex(8)}",
            order_id=order_id,
            agent_id=agent_id,
            reason=reason.strip()[:500],
            requested_at=now or datetime.now(UTC),
        )
        try:
            async with session_scope(self._maker()) as session:
                await session.execute(
                    refund_requests.insert().values(
                        request_id=request.request_id,
                        order_id=request.order_id,
                        agent_id=request.agent_id,
                        reason=request.reason,
                        requested_at=request.requested_at,
                        state=request.state.value,
                        resolved_at=None,
                        resolution_note="",
                    )
                )
        except IntegrityError:
            # The partial unique index refused a second open ask for this order.
            # Idempotent rather than an error: asking twice is not two asks, and
            # the agent is told about the request that already exists.
            existing = await self.open_for(order_id)
            if existing is None:  # pragma: no cover - the index refused for another reason
                raise
            return existing
        return request

    async def resolve(
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
        moment = now or datetime.now(UTC)
        pending = await self.open_for(order_id)
        if pending is None:
            return None
        async with session_scope(self._maker()) as session:
            # Conditional on the row still being open, so two Merchants acting
            # at once produce one resolution rather than overwriting each
            # other's note. The lookup above finds the work; this decides it.
            result = await session.execute(
                refund_requests.update()
                .where(
                    refund_requests.c.request_id == pending.request_id,
                    refund_requests.c.state == RefundRequestState.REQUESTED.value,
                )
                .values(
                    state=state.value,
                    resolved_at=moment,
                    resolution_note=note.strip()[:500],
                )
            )
            if rows_affected(result) == 0:
                return None
        return await self.get(pending.request_id)

    async def rows(self) -> list[dict[str, object]]:
        """Open asks first, then resolved ones, newest first within each. The
        queue is read to find work, so the work sorts to the top."""
        async with session_scope(self._maker()) as session:
            found = (
                await session.execute(
                    select(refund_requests).order_by(refund_requests.c.requested_at.desc())
                )
            ).all()
        requests = [_row_to_request(r) for r in found]
        # Two stable passes rather than one composite key: the second keeps the
        # first's order inside each group, and it reads as the two rules it is.
        return [r.to_row() for r in sorted(requests, key=lambda r: not r.open)]

    async def clear(self) -> None:
        async with session_scope(self._maker()) as session:
            await session.execute(refund_requests.delete())


_queue = RefundQueue()


def configure_refunds(sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
    """Point the process-wide queue at this deploy's database."""
    _queue.sessionmaker = sessionmaker


def get_refund_queue() -> RefundQueue:
    return _queue


async def close_open_request(
    order_id: str, state: RefundRequestState, *, note: str = ""
) -> RefundRequest | None:
    """Close the ask a Merchant action has just answered, if there is one.

    **Never raises because the queue has no database.** This runs *after* money
    has moved, and failing a completed refund because its inbox is missing would
    report a failure for something that already happened. A sidecar with no
    database has no asks to close, which is checked rather than caught.
    """
    queue = get_refund_queue()
    if queue.sessionmaker is None:
        return None
    return await queue.resolve(order_id, state, note=note)
