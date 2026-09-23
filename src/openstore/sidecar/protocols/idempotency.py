"""Retry safety for the agent tool surface.

An agent whose `add-line` times out has no idea whether the line landed. Without
a key its only choices are to retry and risk two lines, or not retry and risk
none — and a shopper watching either outcome sees the agent get their basket
wrong for a reason that was never about shopping.

The money path has had this since hour 0: door 7 keys on `cart_id:attempt`, and
`acp.py` maps ACP's native `Idempotency-Key` straight onto it. What was missing
was the same guarantee for the fourteen tools an agent actually calls, so a
retry was only safe on the one step that had thought about it.

**The key is scoped to the agent.** Two agents sending `retry-1` are two
different requests; a key that were global would let one agent read another's
stored basket by guessing a string. The stored row therefore carries `agent_id`
and the lookup is keyed on both.

**A key with different arguments is a conflict, not a replay.** Returning the
first result for a second, different request would silently answer a question
nobody asked — so the request is fingerprinted and a mismatch refuses. That is
the one case where doing nothing is worse than failing.

Reads are not stored. `search` and `read-item` change nothing, so a repeat is
already harmless, and keeping their results here would turn an idempotency
table into a catalogue cache with no invalidation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Column as Col
from sqlalchemy import CursorResult, DateTime, Index, String, Table, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.core.db import session_scope
from openstore.sidecar.core.tables import metadata

#: How long a key is honoured. Long enough to cover any retry a client would
#: sensibly make, short enough that the table is not a permanent log of every
#: call. Stripe uses 24h; a shopping basket does not outlive a conversation, so
#: this is shorter on purpose.
RETENTION = timedelta(hours=6)

#: The longest key accepted. A key is a client's own correlation string, not a
#: payload — refusing a long one is cheaper than storing it.
MAX_KEY_LENGTH = 200

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Col("key", String(MAX_KEY_LENGTH), primary_key=True),
    Col("agent_id", String(128), primary_key=True),
    Col("tool", String(64), nullable=False),
    #: SHA-256 over the canonical arguments. Stored rather than the arguments
    #: themselves: this table only ever has to answer "same question?", and a
    #: Destination is PII that has no business living here (ADR-0011).
    Col("request_digest", String(64), nullable=False),
    Col("response", Text, nullable=False),
    Col("stored_at", DateTime(timezone=True), nullable=False),
    Index("ix_idempotency_stored_at", "stored_at"),
)


class IdempotencyConflict(Exception):
    """Same key, different question."""

    code = ReasonCode.IDEMPOTENCY_CONFLICT


def fingerprint(tool: str, args: dict[str, Any]) -> str:
    """A stable digest of what was asked.

    Sorted keys, because argument order is whatever the model emitted and two
    orderings are the same question. The tool name is inside the digest so one
    key reused across two tools is a conflict rather than a replay of the wrong
    one.
    """
    canonical = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class IdempotencyStore:
    """Durable, because the guarantee is worth nothing across a restart.

    An in-memory version would be exactly as correct in tests and would fail in
    the one situation the feature exists for: the process that took the first
    call is the process that died, which is why the client is retrying.
    """

    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This IdempotencyStore has no database, so no retry can be made safe. "
                "Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    @property
    def enabled(self) -> bool:
        """A sidecar with no database still serves its catalogue (§ `_run_tool`),
        so idempotency degrades to absent rather than refusing every call. The
        card reports whether it is on, so nobody has to infer it."""
        return self.sessionmaker is not None

    async def replay(self, key: str, agent_id: str, digest: str) -> dict[str, Any] | None:
        """The stored response, or `None` if this key is new.

        Raises `IdempotencyConflict` when the key is known and the question is
        not the one it answered.
        """
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(
                    select(idempotency_keys.c.request_digest, idempotency_keys.c.response).where(
                        idempotency_keys.c.key == key,
                        idempotency_keys.c.agent_id == agent_id,
                    )
                )
            ).first()
        if row is None:
            return None
        if row.request_digest != digest:
            raise IdempotencyConflict(
                "That idempotency key was used for a different request. Use a new key, or "
                "resend the original arguments."
            )
        replayed: dict[str, Any] = json.loads(row.response)
        return replayed

    async def remember(
        self, key: str, agent_id: str, tool: str, digest: str, response: dict[str, Any]
    ) -> None:
        async with session_scope(self._maker()) as session:
            await session.execute(
                idempotency_keys.insert().values(
                    key=key,
                    agent_id=agent_id,
                    tool=tool,
                    request_digest=digest,
                    response=json.dumps(response, separators=(",", ":")),
                    stored_at=datetime.now(UTC),
                )
            )

    async def sweep(self) -> int:
        """Drop keys past RETENTION. Returns how many went.

        Called by the expiry sweeper rather than on every write: a delete on the
        hot path would make the cheapest call in the system pay for the oldest.
        """
        cutoff = datetime.now(UTC) - RETENTION
        async with session_scope(self._maker()) as session:
            # `CursorResult.rowcount`, which `Result` does not declare — a DELETE
            # always returns the cursor flavour, so the cast is narrowing a
            # static type to what this statement actually produces.
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(idempotency_keys).where(idempotency_keys.c.stored_at < cutoff)
                ),
            )
        return int(result.rowcount or 0)


__all__ = [
    "MAX_KEY_LENGTH",
    "RETENTION",
    "IdempotencyConflict",
    "IdempotencyStore",
    "fingerprint",
    "idempotency_keys",
]
