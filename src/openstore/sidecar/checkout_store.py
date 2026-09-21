"""Checkouts in flight, and the Decision each one is waiting to settle against.

**This is the row a lost restart used to cost real money.** The in-flight
checkout lived in a dict, so a deploy between the tap and the Provider's
callback left the sidecar with nothing to match the payment to: `complete`
answered "No checkout is waiting on that payment", the Consumer had paid, the
stock stayed held, and the order sat `confirmed` until the sweeper expired it.
Everything else durability bought is convenience next to that.

Two things are stored per checkout and they are different kinds of fact:

- **What the Consumer was shown** — lines, Destination, Contact Point, Quote,
  `cart_hash`, total. Written at `start` and never edited afterwards except by
  the money path's own transitions.
- **The Decision the Gate permitted** — its Transcript, the pinned Quote and
  its bytes. Held from the tap until the money lands, because `settle`
  reconciles what the Provider reports against what the Gate decided, and a
  checkout with no Decision has nothing to reconcile *to*.

`order_salt` is still never stored (§6.3a), and neither is a payment
credential. What is here is what the Consumer agreed to, which is exactly what
the receipt will say.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Column as Col
from sqlalchemy import DateTime, Index, Integer, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.authority.kinds import HandleSource
from openstore.sidecar.core.codes import OrderStatus, PaymentMethod
from openstore.sidecar.core.db import as_utc, session_scope
from openstore.sidecar.core.tables import metadata
from openstore.sidecar.gate.decide import Decision
from openstore.sidecar.gate.transcript import transcript_from_dict
from openstore.sidecar.trait.models import Destination, Line, Quote

if TYPE_CHECKING:  # pragma: no cover - the import exists for the annotation only
    from openstore.sidecar.checkout import Pending

#: One row per checkout, from `start` until it reaches a terminal status.
checkouts = Table(
    "checkouts",
    metadata,
    Col("order_id", String(64), primary_key=True),
    Col("cart_id", String(64), nullable=False),
    Col("agent_id", String(128), nullable=False),
    Col("method", String(32), nullable=False),
    Col("status", String(16), nullable=False),
    Col("expiry_utc", String(32), nullable=False),
    Col("lines", Text, nullable=False),
    Col("destination", Text, nullable=False),
    Col("contact", Text, nullable=False),
    Col("fulfillment_option_id", String(64), nullable=False),
    Col("discount_code", String(64), nullable=True),
    Col("quote", Text, nullable=False),
    # The Quote's canonical bytes, base64 so the column is text and the bytes
    # come back identical. They are half of the `cart_hash` preimage: decoding
    # and re-encoding them through a JSON round trip would be free to reorder
    # keys, and the hash would then be over something else.
    Col("quote_bytes_b64", Text, nullable=False),
    Col("cart_hash", String(64), nullable=False),
    Col("total_minor", Integer, nullable=False),
    Col("tap_token", String(64), nullable=False, default=""),
    Col("link_id", String(128), nullable=False, default=""),
    Col("payer_handle", String(255), nullable=False, default=""),
    Col("handle_source", String(32), nullable=False),
    Col("receipt_id", String(64), nullable=False, default=""),
    Col("created_at", DateTime(timezone=True), nullable=False),
    Col("confirmed_at", DateTime(timezone=True), nullable=True),
    Col("link_expires_at", DateTime(timezone=True), nullable=True),
    Col("decision", Text, nullable=True),
    Col("updated_at", DateTime(timezone=True), nullable=False),
    # The two lookups the money path does by something other than the order id.
    # Indexed rather than scanned because both are on the request path: the
    # Consumer's tap and the Provider's callback.
    Index("ix_checkouts_tap_token", "tap_token"),
    Index("ix_checkouts_link_id", "link_id"),
    Index("ix_checkouts_agent", "agent_id"),
)


def _decision_to_json(decision: Decision) -> str:
    return json.dumps(
        {
            "transcript": decision.transcript.to_dict(),
            "quote": decision.quote.model_dump(mode="json"),
            "quote_bytes_b64": base64.b64encode(decision.quote_bytes).decode("ascii"),
            "cart_hash": decision.cart_hash,
            "permitted": decision.permitted,
        },
        sort_keys=True,
    )


def _decision_from_json(document: str) -> Decision:
    body = json.loads(document)
    return Decision(
        transcript=transcript_from_dict(body["transcript"]),
        quote=Quote(**body["quote"]),
        quote_bytes=base64.b64decode(body["quote_bytes_b64"]),
        cart_hash=body["cart_hash"],
        permitted=body["permitted"],
    )


def _to_pending(row: Any) -> Pending:
    from openstore.sidecar.checkout import Pending

    created_at = as_utc(row.created_at)
    assert created_at is not None
    return Pending(
        cart_id=row.cart_id,
        order_id=row.order_id,
        lines=[Line(**line) for line in json.loads(row.lines)],
        destination=Destination(**json.loads(row.destination)),
        contact=json.loads(row.contact),
        fulfillment_option_id=row.fulfillment_option_id,
        expiry_utc=row.expiry_utc,
        agent_id=row.agent_id,
        method=PaymentMethod(row.method),
        quote=Quote(**json.loads(row.quote)),
        quote_bytes=base64.b64decode(row.quote_bytes_b64),
        cart_hash=row.cart_hash,
        total_minor=row.total_minor,
        discount_code=row.discount_code,
        tap_token=row.tap_token,
        link_id=row.link_id,
        payer_handle=row.payer_handle,
        status=OrderStatus(row.status),
        receipt_id=row.receipt_id,
        created_at=created_at,
        confirmed_at=as_utc(row.confirmed_at),
        link_expires_at=as_utc(row.link_expires_at),
        handle_source=HandleSource(row.handle_source),
    )


def _values(checkout: Pending, decision: Decision | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_id": checkout.order_id,
        "cart_id": checkout.cart_id,
        "agent_id": checkout.agent_id,
        "method": checkout.method.value,
        "status": checkout.status.value,
        "expiry_utc": checkout.expiry_utc,
        "lines": json.dumps([line.model_dump(mode="json") for line in checkout.lines]),
        "destination": json.dumps(checkout.destination.model_dump(mode="json")),
        "contact": json.dumps(checkout.contact),
        "fulfillment_option_id": checkout.fulfillment_option_id,
        "discount_code": checkout.discount_code,
        "quote": json.dumps(checkout.quote.model_dump(mode="json")),
        "quote_bytes_b64": base64.b64encode(checkout.quote_bytes).decode("ascii"),
        "cart_hash": checkout.cart_hash,
        "total_minor": checkout.total_minor,
        "tap_token": checkout.tap_token,
        "link_id": checkout.link_id,
        "payer_handle": checkout.payer_handle,
        "handle_source": checkout.handle_source.value,
        "receipt_id": checkout.receipt_id,
        "created_at": checkout.created_at,
        "confirmed_at": checkout.confirmed_at,
        "link_expires_at": checkout.link_expires_at,
        "updated_at": datetime.now(UTC),
    }
    if decision is not None:
        body["decision"] = _decision_to_json(decision)
    return body


@dataclass
class CheckoutStore:
    """Every checkout this sidecar has in flight.

    Each method takes its own short transaction. The money path's own
    transaction — the Ledger's — stays separate on purpose: a Ledger write and
    a checkout write are different facts about different books, and sharing one
    unit of work would let a bookkeeping failure roll back the record of what
    the Consumer agreed to.
    """

    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This CheckoutStore has no database, so no checkout can be kept between "
                "the tap and the money arriving. Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    async def save(self, checkout: Pending, decision: Decision | None = None) -> None:
        """Write the checkout, and the Decision when there is one to keep.

        A `None` decision leaves whatever is stored alone rather than clearing
        it: most saves are a status moving, and a status move must not throw
        away the thing the money will be settled against.
        """
        values = _values(checkout, decision)
        async with session_scope(self._maker()) as session:
            existing = (
                await session.execute(
                    select(checkouts.c.order_id).where(checkouts.c.order_id == checkout.order_id)
                )
            ).first()
            if existing is None:
                await session.execute(checkouts.insert().values(**values))
            else:
                await session.execute(
                    checkouts.update()
                    .where(checkouts.c.order_id == checkout.order_id)
                    .values(**values)
                )

    async def get(self, order_id: str) -> Pending | None:
        if not order_id:
            return None
        return await self._one(checkouts.c.order_id == order_id)

    async def by_token(self, token: str) -> Pending | None:
        """The checkout a tap token belongs to. Empty matches nothing — a blank
        token must never select the row of a checkout that has none."""
        if not token:
            return None
        return await self._one(checkouts.c.tap_token == token)

    async def by_link(self, link_id: str) -> Pending | None:
        if not link_id:
            return None
        return await self._one(checkouts.c.link_id == link_id)

    async def _one(self, condition: Any) -> Pending | None:
        async with session_scope(self._maker()) as session:
            row = (await session.execute(select(checkouts).where(condition))).first()
        return _to_pending(row) if row is not None else None

    async def decision_for(self, order_id: str) -> Decision | None:
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(
                    select(checkouts.c.decision).where(checkouts.c.order_id == order_id)
                )
            ).first()
        if row is None or row.decision is None:
            return None
        return _decision_from_json(row.decision)

    async def live(self) -> list[Pending]:
        """Checkouts that have not reached a terminal status — what the sweeper
        walks and what `place-order` looks through."""
        live_statuses = [OrderStatus.PENDING.value, OrderStatus.CONFIRMED.value]
        async with session_scope(self._maker()) as session:
            rows = (
                await session.execute(
                    select(checkouts)
                    .where(checkouts.c.status.in_(live_statuses))
                    .order_by(checkouts.c.created_at)
                )
            ).all()
        return [_to_pending(r) for r in rows]

    async def all(self) -> list[Pending]:
        async with session_scope(self._maker()) as session:
            rows = (await session.execute(select(checkouts).order_by(checkouts.c.created_at))).all()
        return [_to_pending(r) for r in rows]

    async def clear(self) -> None:
        async with session_scope(self._maker()) as session:
            await session.execute(checkouts.delete())
