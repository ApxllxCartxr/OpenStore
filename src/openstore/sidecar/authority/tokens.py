"""Tap tokens and resume tokens. Both unguessable, single-use, short-lived.

A tap token is what makes "a hold cannot be created without spending an approve
token" true: the approve page issues one, the Gate spends it, and a second spend
of the same token is refused. Without that, an agent that could reach the
confirm endpoint directly could take a hold nobody tapped for.

A resume token exists because the alternative — putting `order_id` and
`chat_thread_id` in the return URL — hands anyone holding the link someone
else's checkout. It is the same IDOR the storefront's order lookup was fixed
for, and it is worth stating twice because the return URL looks harmless.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Boolean, DateTime, Integer, String, Table, select
from sqlalchemy import Column as Col
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.core.db import as_utc, rows_affected, session_scope
from openstore.sidecar.core.tables import metadata
from openstore.sidecar.trait.errors import TraitError

#: §16.7. The tap token is 5 minutes because it is the window between being
#: shown a total and agreeing to it; the resume token is 30 because it spans a
#: PSP app switch on a phone that may be slow.
TAP_TOKEN_TTL = timedelta(minutes=5)
RESUME_TOKEN_TTL = timedelta(minutes=30)


#: One row per tap token. Durable since 09-21: a restart between the approve
#: page rendering and the Consumer tapping used to answer "that approval link is
#: not valid" for a link the sidecar had issued seconds earlier.
tap_tokens = Table(
    "tap_tokens",
    metadata,
    Col("token", String(64), primary_key=True),
    Col("order_id", String(64), nullable=False),
    Col("cart_hash", String(64), nullable=False),
    Col("total_minor", Integer, nullable=False),
    Col("issued_at", DateTime(timezone=True), nullable=False),
    Col("expires_at", DateTime(timezone=True), nullable=False),
    Col("spent", Boolean, nullable=False, default=False),
    Col("rendered_digest", String(64), nullable=False, default=""),
)

#: One row per resume token. Same story, and the same single-use rule.
resume_tokens = Table(
    "resume_tokens",
    metadata,
    Col("token", String(64), primary_key=True),
    Col("order_id", String(64), nullable=False),
    Col("chat_thread_id", String(128), nullable=False),
    Col("session_id", String(128), nullable=False),
    Col("expires_at", DateTime(timezone=True), nullable=False),
    Col("spent", Boolean, nullable=False, default=False),
)


def _new_token() -> str:
    """128 bits from `secrets`, never `random` and never a timestamp (§16.1)."""
    return secrets.token_urlsafe(16)


@dataclass
class TapToken:
    token: str
    order_id: str
    cart_hash: str
    total_minor: int
    issued_at: datetime
    expires_at: datetime
    spent: bool = False
    #: What the approve page actually rendered. A change after render invalidates
    #: the token even when the total did not move — otherwise a Destination edit
    #: could redirect a parcel somebody already paid for.
    rendered_digest: str = ""


@dataclass
class ResumeToken:
    token: str
    order_id: str
    chat_thread_id: str
    session_id: str
    expires_at: datetime
    spent: bool = False


@dataclass
class TokenStore:
    """Tokens in the sidecar's own database.

    **Single-use is enforced by the UPDATE, not by the check above it.** The
    dict version marked `spent` after reading it, which two concurrent taps of
    one link both pass — the same read-then-act shape the Ledger's unique
    constraint exists to close. Here the spend is `UPDATE ... WHERE spent =
    false`, and the loser sees `rowcount == 0` and is refused.

    Spending marks rather than deletes, so a replay can be told apart from a
    token that never existed — the two deserve different answers to an operator
    reading logs, even though both refuse.
    """

    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This TokenStore has no database, so no approval link can be issued or "
                "spent. Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    async def issue_tap(
        self,
        order_id: str,
        cart_hash: str,
        total_minor: int,
        *,
        rendered_digest: str = "",
        now: datetime | None = None,
    ) -> TapToken:
        moment = now or datetime.now(UTC)
        token = TapToken(
            token=_new_token(),
            order_id=order_id,
            cart_hash=cart_hash,
            total_minor=total_minor,
            issued_at=moment,
            expires_at=moment + TAP_TOKEN_TTL,
            rendered_digest=rendered_digest,
        )
        async with session_scope(self._maker()) as session:
            await session.execute(
                tap_tokens.insert().values(
                    token=token.token,
                    order_id=token.order_id,
                    cart_hash=token.cart_hash,
                    total_minor=token.total_minor,
                    issued_at=token.issued_at,
                    expires_at=token.expires_at,
                    spent=False,
                    rendered_digest=token.rendered_digest,
                )
            )
        return token

    async def tap(self, token: str) -> TapToken | None:
        """Read a tap token without spending it — what the approve page renders
        from. Never a substitute for `spend_tap`: reading does not authorize."""
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(select(tap_tokens).where(tap_tokens.c.token == token))
            ).first()
        return _row_to_tap(row) if row is not None else None

    async def spend_tap(
        self,
        token: str,
        *,
        cart_hash: str,
        rendered_digest: str = "",
        now: datetime | None = None,
    ) -> TapToken:
        """Spend a tap token, or refuse.

        The `cart_hash` is checked here as well as at the Gate. Two checks of
        one fact is not redundancy: this one catches a token being spent against
        a *different* cart, which is a replay, while the Gate's catches a cart
        that moved under a valid token, which is staleness.
        """
        moment = now or datetime.now(UTC)
        record = await self.tap(token)
        if record is None:
            raise TraitError(ReasonCode.NOT_FOUND, "that approval link is not valid")
        if record.spent:
            raise TraitError(ReasonCode.AUTHORITY_STALE, "that approval link has already been used")
        if moment > record.expires_at:
            raise TraitError(ReasonCode.AUTHORITY_STALE, "that approval link expired; start again")
        if record.cart_hash != cart_hash:
            raise TraitError(
                ReasonCode.AUTHORITY_STALE, "that approval link belongs to a different basket"
            )
        if record.rendered_digest and rendered_digest and record.rendered_digest != rendered_digest:
            raise TraitError(
                ReasonCode.AUTHORITY_STALE,
                "something changed after this page was shown; check the details and tap again",
            )

        async with session_scope(self._maker()) as session:
            result = await session.execute(
                tap_tokens.update()
                .where(tap_tokens.c.token == token, tap_tokens.c.spent.is_(False))
                .values(spent=True)
            )
            if rows_affected(result) == 0:
                # Another tap of the same link won the race between the read
                # above and this write. Refused identically to a replay,
                # because that is what it is.
                raise TraitError(
                    ReasonCode.AUTHORITY_STALE, "that approval link has already been used"
                )
        record.spent = True
        return record

    async def issue_resume(
        self, order_id: str, chat_thread_id: str, session_id: str, *, now: datetime | None = None
    ) -> ResumeToken:
        moment = now or datetime.now(UTC)
        token = ResumeToken(
            token=_new_token(),
            order_id=order_id,
            chat_thread_id=chat_thread_id,
            session_id=session_id,
            expires_at=moment + RESUME_TOKEN_TTL,
        )
        async with session_scope(self._maker()) as session:
            await session.execute(
                resume_tokens.insert().values(
                    token=token.token,
                    order_id=token.order_id,
                    chat_thread_id=token.chat_thread_id,
                    session_id=token.session_id,
                    expires_at=token.expires_at,
                    spent=False,
                )
            )
        return token

    async def resume(self, token: str) -> ResumeToken | None:
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(select(resume_tokens).where(resume_tokens.c.token == token))
            ).first()
        return _row_to_resume(row) if row is not None else None

    async def redeem_resume(
        self, token: str, *, session_id: str, now: datetime | None = None
    ) -> ResumeToken:
        """Session-bound: holding the link is not enough, you must be the
        session it was issued to."""
        moment = now or datetime.now(UTC)
        record = await self.resume(token)
        if record is None:
            raise TraitError(ReasonCode.NOT_FOUND, "that resume link is not valid")
        if record.spent:
            raise TraitError(ReasonCode.NOT_FOUND, "that resume link has already been used")
        if moment > record.expires_at:
            raise TraitError(ReasonCode.NOT_FOUND, "that resume link expired")
        if record.session_id != session_id:
            # `not-found`, deliberately: telling a stranger that the link is real
            # but belongs to someone else is the oracle this token exists to close.
            raise TraitError(ReasonCode.NOT_FOUND, "that resume link is not valid")

        async with session_scope(self._maker()) as session:
            result = await session.execute(
                resume_tokens.update()
                .where(resume_tokens.c.token == token, resume_tokens.c.spent.is_(False))
                .values(spent=True)
            )
            if rows_affected(result) == 0:
                raise TraitError(ReasonCode.NOT_FOUND, "that resume link has already been used")
        record.spent = True
        return record


def _required(moment: datetime | None) -> datetime:
    """A deadline column is `nullable=False`; this is the type checker's copy of
    that fact, not a runtime possibility."""
    assert moment is not None
    return moment


def _row_to_tap(row: object) -> TapToken:
    return TapToken(
        token=row.token,  # type: ignore[attr-defined]
        order_id=row.order_id,  # type: ignore[attr-defined]
        cart_hash=row.cart_hash,  # type: ignore[attr-defined]
        total_minor=row.total_minor,  # type: ignore[attr-defined]
        issued_at=_required(as_utc(row.issued_at)),  # type: ignore[attr-defined]
        expires_at=_required(as_utc(row.expires_at)),  # type: ignore[attr-defined]
        spent=row.spent,  # type: ignore[attr-defined]
        rendered_digest=row.rendered_digest,  # type: ignore[attr-defined]
    )


def _row_to_resume(row: object) -> ResumeToken:
    return ResumeToken(
        token=row.token,  # type: ignore[attr-defined]
        order_id=row.order_id,  # type: ignore[attr-defined]
        chat_thread_id=row.chat_thread_id,  # type: ignore[attr-defined]
        session_id=row.session_id,  # type: ignore[attr-defined]
        expires_at=_required(as_utc(row.expires_at)),  # type: ignore[attr-defined]
        spent=row.spent,  # type: ignore[attr-defined]
    )
