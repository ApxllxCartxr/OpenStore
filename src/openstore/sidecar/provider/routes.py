"""`POST /provider/webhook` — the Provider's own callback, and the money path it
does not get to decide.

`provider/webhooks.py` has held the HMAC check, the `event_id` dedupe and the
reconcile since A3, and nothing was ever mounted in front of them: the only way
money finished moving was the demo's own Approve button calling `complete()`
directly. That is the one shape a real Provider never uses.

Three properties, and the route is arranged so that none of them is optional:

- **Verify before parse.** `WebhookVerifier` HMACs the raw bytes and only then
  reads JSON, because acting on the shape of a document before knowing it is
  ours is how a forged body reaches a money path.
- **The event is a trigger, never an instruction.** Nothing in the body decides
  anything: the handler reads the `link_id` out of it and then asks the Provider
  what it says happened, through the same `check_status` the adoption path uses.
  A body claiming `paid` on an order the Provider has not settled moves nothing.
- **Out-of-order safe and terminal-safe.** Providers do not promise ordering and
  do retry. A `paid` arriving after a `failed`, or twice, finds an order already
  in a terminal status and is acknowledged without acting — a webhook never
  resurrects an order.

**The Provider decides the money-moved fact only, never authority** (SPEC §8).
Authority was settled at the tap and `settle()` resolves whatever `decide()`
deferred; nothing here can grant one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openstore.sidecar.core.codes import OrderStatus, ReasonCode
from openstore.sidecar.provider.webhooks import WebhookRejected, WebhookVerifier
from openstore.sidecar.trait.errors import TraitError

router = APIRouter(prefix="/provider")

_log = logging.getLogger("openstore.webhook")

#: Statuses a webhook may never move an order out of. `paid` is here because a
#: second delivery must capture once; the rest because money has already
#: finished moving one way or the other and a late event is news, not a change.
TERMINAL = frozenset(
    {
        OrderStatus.PAID,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.REFUNDED,
        OrderStatus.COMPLETED,
    }
)


@dataclass
class WebhookContext:
    """The one secret this deploy verifies with, and the adapter that reads the
    envelope.

    `verifier` is `None` when no secret is configured, and then **every webhook
    is refused**. There is no unauthenticated webhook path, not even in demo
    mode: a route that accepts an unsigned body is a route anyone can use to
    tell the sidecar that money arrived.
    """

    verifier: WebhookVerifier | None = None
    provider: Any = None
    signature_header: str = "x-openstore-signature"
    _context_source: str = field(default="unconfigured", repr=False)


_context = WebhookContext()


def configure(context: WebhookContext) -> None:
    global _context
    _context = context


def get_context() -> WebhookContext:
    return _context


@dataclass(frozen=True)
class WebhookOutcome:
    """What one delivery did. `acted` is false for every acknowledged no-op, so
    a caller can tell "we captured" from "we had already captured"."""

    status: str
    acted: bool
    order_id: str = ""
    receipt_id: str = ""
    reason_code: ReasonCode | None = None
    detail: str = ""


async def deliver(raw_body: bytes, signature: str) -> WebhookOutcome:
    """Verify one delivery and act on it. Raises only `WebhookRejected`.

    Separate from the route so the demo Provider's Approve button goes through
    exactly this path rather than around it — a callback path the demo does not
    use is a callback path nobody has watched work.
    """
    from openstore.sidecar import checkout as flow

    ctx = _context
    if ctx.verifier is None or ctx.provider is None:
        raise WebhookRejected(
            "this sidecar has no webhook secret configured, so it cannot tell a Provider's "
            "callback from anyone else's POST"
        )

    payload = ctx.verifier.verify(raw_body=raw_body, signature=signature)
    event = ctx.provider.read_webhook(payload)
    if ctx.verifier.is_duplicate(event.event_id):
        # Correct behaviour by the Provider, absorbed here. Retries are how a
        # webhook survives our downtime, and the dedupe is what makes them free.
        return WebhookOutcome(status="duplicate", acted=False, order_id=event.link_id)

    checkout_ctx = flow.get_context()
    order_id = checkout_ctx.by_link.get(event.link_id, "")
    checkout = checkout_ctx.pending.get(order_id)
    if checkout is None:
        return WebhookOutcome(
            status="unknown-link",
            acted=False,
            reason_code=ReasonCode.NOT_FOUND,
            detail="No checkout is waiting on that payment.",
        )

    if checkout.status in TERMINAL:
        # A `paid` arriving after a `failed` must not resurrect it, and a second
        # `paid` must not capture twice.
        _log.warning("webhook for %s ignored: already %s", checkout.order_id, checkout.status.value)
        return WebhookOutcome(
            status=f"already-{checkout.status.value}",
            acted=False,
            order_id=checkout.order_id,
            receipt_id=checkout.receipt_id,
        )

    try:
        settled = await flow.complete(checkout_ctx, event.link_id, payer_handle=event.payer_handle)
    except flow.CheckoutRefused as refusal:
        # The delivery was processed correctly and the money did not settle.
        # That is an answer, not a failure to acknowledge: a Provider told to
        # retry would re-deliver an event whose outcome will not change.
        return WebhookOutcome(
            status="not-settled",
            acted=True,
            order_id=checkout.order_id,
            reason_code=refusal.code,
            detail=refusal.detail,
        )
    except TraitError as exc:
        return WebhookOutcome(
            status="not-settled",
            acted=True,
            order_id=checkout.order_id,
            reason_code=exc.code,
            detail=exc.detail,
        )

    return WebhookOutcome(
        status=settled.status.value,
        acted=True,
        order_id=settled.order_id,
        receipt_id=settled.receipt_id,
    )


@router.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    """The public callback. Refuses anything it cannot verify, and says nothing
    about what it holds — a refusal that named orders would be a lookup oracle
    for whoever guessed a body."""
    raw = await request.body()
    signature = request.headers.get(_context.signature_header, "")
    try:
        outcome = await deliver(raw, signature)
    except WebhookRejected as exc:
        _log.warning("webhook refused: %s", exc)
        error = TraitError(ReasonCode.SIGNATURE_INVALID, str(exc))
        return JSONResponse(status_code=error.status_code, content=error.to_payload())

    body: dict[str, Any] = {"status": outcome.status, "acted": outcome.acted}
    if outcome.order_id:
        body["order_id"] = outcome.order_id
    if outcome.receipt_id:
        body["receipt_id"] = outcome.receipt_id
    if outcome.reason_code is not None:
        body["reason"] = {"code": outcome.reason_code.value, "detail": outcome.detail}
    # 200 on every verified delivery, including one that did not settle: the
    # Provider is being told "received and processed", and a non-2xx would buy
    # a retry of an event whose outcome cannot change.
    return JSONResponse(status_code=200, content=body)
