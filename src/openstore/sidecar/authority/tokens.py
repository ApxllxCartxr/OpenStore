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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.trait.errors import TraitError

#: §16.7. The tap token is 5 minutes because it is the window between being
#: shown a total and agreeing to it; the resume token is 30 because it spans a
#: PSP app switch on a phone that may be slow.
TAP_TOKEN_TTL = timedelta(minutes=5)
RESUME_TOKEN_TTL = timedelta(minutes=30)


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
    """In-memory for A4; the console and install phases give it a home.

    Single-use is enforced by marking rather than deleting, so a replay can be
    told apart from a token that never existed — the two deserve different
    answers to an operator reading logs, even though both refuse.
    """

    taps: dict[str, TapToken] = field(default_factory=dict)
    resumes: dict[str, ResumeToken] = field(default_factory=dict)

    def issue_tap(
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
        self.taps[token.token] = token
        return token

    def spend_tap(
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
        record = self.taps.get(token)
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
        record.spent = True
        return record

    def issue_resume(
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
        self.resumes[token.token] = token
        return token

    def redeem_resume(
        self, token: str, *, session_id: str, now: datetime | None = None
    ) -> ResumeToken:
        """Session-bound: holding the link is not enough, you must be the
        session it was issued to."""
        moment = now or datetime.now(UTC)
        record = self.resumes.get(token)
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
        record.spent = True
        return record
