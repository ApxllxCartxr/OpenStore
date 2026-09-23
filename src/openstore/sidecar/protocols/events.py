"""Order events, delivered to the agent that placed the order.

Without these an agent learns an order moved by calling `order-status`, and an
agent holding ten shops polls ten shops forever, per order. The cost is not the
round trip — it is that the agent must keep asking to find out that nothing
happened, which is the same fan-out problem the discovery hint was added to
stop, one step further down the funnel.

**Durable and retried, or not worth having.** An event system that drops on a
restart is one nobody can build on: an agent that missed `paid` has to poll
anyway, so the polling never actually goes away and the feature is decoration.
Events are therefore rows, delivery is a sweep, and failure is a backoff rather
than a shrug.

**Signed with the Merchant's own key.** The agent already pinned this shop's
JWKS at registration (TOFU, ADR-0012), so the same signature it uses to verify
a receipt verifies an event — no second trust root, no shared secret to
distribute. The signature covers the canonical body, so a replayed or edited
event fails on arrival.

**The callback URL is attacker-chosen**, so it is treated the way the Agent
Profile URL already is: fetched through `ProfileFetcher`'s hardening, not with a
bare client. An agent that registers `http://169.254.169.254/` is asking this
sidecar to make a request on its behalf, and that is the whole SSRF problem.

An event carries **no PII and no money detail** — an order id, a status, a
reason code, a receipt id. Everything else is behind `order-status`, which is
authenticated as the agent. A webhook body is the easiest thing in the system
to end up in somebody's logging pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Column as Col
from sqlalchemy import DateTime, Index, Integer, String, Table, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.core.canonical import canonical_bytes
from openstore.sidecar.core.db import session_scope
from openstore.sidecar.core.tables import metadata

#: Attempts before an event is parked. Six attempts across the backoff below is
#: a little over an hour — long enough to ride out a deploy of the agent's own
#: service, short enough that a dead endpoint stops costing this shop work.
MAX_ATTEMPTS = 6

#: Backoff per attempt, in seconds. Explicit rather than computed: a table is
#: something an operator can read and predict, and `2 ** n` with a cap is the
#: same numbers with a bug waiting in the cap.
BACKOFF_SECONDS = (10, 30, 120, 300, 900, 1800)

#: How long a delivered or parked row is kept, for the console and for an agent
#: asking what it missed.
RETENTION = timedelta(days=7)

order_events = Table(
    "order_events",
    metadata,
    Col("event_id", String(64), primary_key=True),
    Col("agent_id", String(128), nullable=False),
    Col("order_id", String(64), nullable=False),
    Col("kind", String(64), nullable=False),
    Col("body", Text, nullable=False),
    Col("callback_url", String(2048), nullable=False),
    Col("created_at", DateTime(timezone=True), nullable=False),
    #: `pending`, `delivered`, or `parked` — a closed set like every other in
    #: this system, and the reason there is no `failed`: a failure that will be
    #: retried is still pending, and one that will not is parked.
    Col("state", String(16), nullable=False),
    Col("attempts", Integer, nullable=False, default=0),
    Col("next_attempt_at", DateTime(timezone=True), nullable=True),
    Col("last_error", String(500), nullable=False, default=""),
    Index("ix_order_events_due", "state", "next_attempt_at"),
    Index("ix_order_events_order", "order_id"),
)


@dataclass
class PendingEvent:
    event_id: str
    agent_id: str
    order_id: str
    kind: str
    body: dict[str, Any]
    callback_url: str
    attempts: int


def event_body(
    *,
    event_id: str,
    merchant_domain: str,
    order_id: str,
    kind: str,
    at: str,
    reason_code: str = "",
    receipt_id: str = "",
) -> dict[str, Any]:
    """What is signed and sent.

    Deliberately thin. An agent that wants the order calls `order-status` with
    its own token, which is the surface that already decides what that agent may
    see — putting the same facts in an unauthenticated POST body would be a
    second, quieter answer to the same question.
    """
    body: dict[str, Any] = {
        "event_id": event_id,
        "merchant": merchant_domain,
        "order_id": order_id,
        "kind": kind,
        "at": at,
    }
    if reason_code:
        body["reason_code"] = reason_code
    if receipt_id:
        body["receipt_id"] = receipt_id
    return body


def signing_payload(body: dict[str, Any]) -> bytes:
    """Canonical bytes, the same encoder the receipt chain uses — so an agent
    that can verify a receipt needs nothing new to verify an event."""
    return canonical_bytes(body)


@dataclass
class EventStore:
    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def enabled(self) -> bool:
        return self.sessionmaker is not None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This EventStore has no database, so an event could not survive the "
                "restart it exists to survive. Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    async def enqueue(
        self,
        *,
        event_id: str,
        agent_id: str,
        order_id: str,
        kind: str,
        body: dict[str, Any],
        callback_url: str,
        now: datetime | None = None,
    ) -> None:
        """Queue one event. Due immediately; the sweeper is what sends it.

        Queued rather than sent inline on purpose: a status change must not wait
        on somebody else's HTTP server, and an agent whose endpoint hangs must
        not be able to hold a Merchant's order transition open.
        """
        at = now or datetime.now(UTC)
        async with session_scope(self._maker()) as session:
            await session.execute(
                order_events.insert().values(
                    event_id=event_id,
                    agent_id=agent_id,
                    order_id=order_id,
                    kind=kind,
                    body=json.dumps(body, separators=(",", ":")),
                    callback_url=callback_url,
                    created_at=at,
                    state="pending",
                    attempts=0,
                    next_attempt_at=at,
                    last_error="",
                )
            )

    async def due(self, *, now: datetime | None = None, limit: int = 50) -> list[PendingEvent]:
        at = now or datetime.now(UTC)
        async with session_scope(self._maker()) as session:
            rows = (
                await session.execute(
                    select(order_events)
                    .where(
                        order_events.c.state == "pending",
                        order_events.c.next_attempt_at <= at,
                    )
                    .order_by(order_events.c.created_at)
                    .limit(limit)
                )
            ).all()
        return [
            PendingEvent(
                event_id=row.event_id,
                agent_id=row.agent_id,
                order_id=row.order_id,
                kind=row.kind,
                body=json.loads(row.body),
                callback_url=row.callback_url,
                attempts=row.attempts,
            )
            for row in rows
        ]

    async def delivered(self, event_id: str) -> None:
        async with session_scope(self._maker()) as session:
            await session.execute(
                update(order_events)
                .where(order_events.c.event_id == event_id)
                .values(state="delivered", next_attempt_at=None, last_error="")
            )

    async def failed(self, event_id: str, attempts: int, error: str) -> str:
        """Record a failed attempt and schedule the next, or park it.

        Returns the new state, so the caller logs what actually happened rather
        than what it assumed.
        """
        if attempts >= MAX_ATTEMPTS:
            state, next_at = "parked", None
        else:
            state = "pending"
            # `attempts` is the count INCLUDING the one that just failed, so the
            # first failure waits BACKOFF_SECONDS[0]. Indexing by `attempts`
            # directly skipped the first entry and made every event wait longer
            # than the table says.
            step = min(max(attempts - 1, 0), len(BACKOFF_SECONDS) - 1)
            next_at = datetime.now(UTC) + timedelta(seconds=BACKOFF_SECONDS[step])
        async with session_scope(self._maker()) as session:
            await session.execute(
                update(order_events)
                .where(order_events.c.event_id == event_id)
                .values(
                    state=state,
                    attempts=attempts,
                    next_attempt_at=next_at,
                    last_error=error[:500],
                )
            )
        return state

    async def board(self, limit: int = 50) -> list[dict[str, Any]]:
        """What the console shows: the queue, newest first."""
        async with session_scope(self._maker()) as session:
            rows = (
                await session.execute(
                    select(order_events).order_by(order_events.c.created_at.desc()).limit(limit)
                )
            ).all()
        return [
            {
                "event_id": row.event_id,
                "order_id": row.order_id,
                "kind": row.kind,
                "state": row.state,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in rows
        ]


async def deliver(
    event: PendingEvent,
    *,
    signature: str,
    kid: str,
    fetcher: Any,
    client: Any = None,
    timeout_seconds: int = 5,
) -> None:
    """POST one event, or raise.

    The URL is re-checked on **every** attempt, never only at registration. An
    agent that registered a public callback and later repointed that hostname at
    `127.0.0.1` would otherwise have turned this sidecar into a request forwarder
    for the rest of the queue's life — the check is cheap and the alternative is
    an SSRF hole with a delay fuse.

    Redirects are not followed, for the same reason `ProfileFetcher` does not:
    a 302 is a second URL that passed none of the checks the first one did.
    """
    import httpx

    fetcher.check_url(event.callback_url)
    owns = client is None
    # Seconds as an int: this package refuses float literals (money is paise),
    # and a whole-second timeout needs nothing finer.
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=timeout_seconds)
    try:
        response = await client.post(
            event.callback_url,
            content=signing_payload(event.body),
            headers={
                "content-type": "application/json",
                # Verified against the shop's JWKS, which this agent pinned when
                # it registered. No shared secret, and no second trust root.
                "x-openstore-signature": signature,
                "x-openstore-key-id": kid,
                "x-openstore-event": event.kind,
            },
        )
    finally:
        if owns:
            await client.aclose()
    if response.status_code >= 300:
        # 3xx included: a redirect is not a delivery, and treating one as
        # success would mark an event delivered to a URL nothing checked.
        raise RuntimeError(f"callback answered {response.status_code}")


__all__ = [
    "BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "RETENTION",
    "EventStore",
    "PendingEvent",
    "deliver",
    "event_body",
    "order_events",
    "signing_payload",
]
