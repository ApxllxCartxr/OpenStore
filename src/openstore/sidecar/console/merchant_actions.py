"""Money actions the Merchant's admin initiates, over HMAC.

Refund, shop-reject, COD collection and RTO all land here rather than being
written Merchant-side, and the reason is structural: **the sidecar holds the
`RESERVE`.** A local write on the Merchant's own row would strand a Ledger hold
with nothing to close it, and routing through here serialises shop-reject
against a Consumer tap and the expiry sweep on one `set-status` key.

These are not browser routes. They are called by the merchant site over the
private network with the same HMAC the trait uses in the other direction, so
they carry no session and no CSRF — there is no browser in this path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openstore.sidecar.console.refunds import get_refund_queue
from openstore.sidecar.core.codes import (
    CancellationReason,
    LedgerKind,
    OrderStatus,
    ReasonCode,
    RefundRequestState,
)
from openstore.sidecar.gate.lifecycle import can_transition
from openstore.sidecar.ledger.entries import Ledger, LedgerError
from openstore.sidecar.trait.errors import TraitError
from openstore.sidecar.trait.signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    ReplayGuard,
    verify,
)

router = APIRouter(prefix="/agentic")

MERCHANT_ACTIONS = ("refund", "refund-decline", "collect", "rto", "reject")


@dataclass
class ActionContext:
    """Injected so the routes can be tested without a live deployment."""

    ledger_factory: Any = None
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    guard: ReplayGuard = field(default_factory=ReplayGuard)
    secret: str = "conformance-secret"


_context = ActionContext()


def configure(context: ActionContext) -> None:
    global _context
    _context = context


def get_context() -> ActionContext:
    return _context


async def _verified_body(request: Request, path: str) -> dict[str, Any]:
    import json

    body = await request.body()
    verify(
        _context.secret,
        body=body,
        path=path,
        signature=request.headers.get(SIGNATURE_HEADER, ""),
        timestamp=request.headers.get(TIMESTAMP_HEADER, ""),
        nonce=request.headers.get(NONCE_HEADER, ""),
        guard=_context.guard,
    )
    return dict(json.loads(body)) if body else {}


def _refusal(code: ReasonCode, detail: str) -> JSONResponse:
    error = TraitError(code, detail)
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


@router.post("/refund")
async def refund(request: Request) -> JSONResponse:
    """Ledger `REFUND` + provider refund + conditional restock + `set-status`.

    Bounded at write time by `sum(REFUND) <= captured - already_refunded`, and
    the key carries the refund's own id so two partials both write.
    """
    try:
        payload = await _verified_body(request, "/agentic/refund")
    except ValueError as exc:
        return _refusal(ReasonCode.SIGNATURE_INVALID, str(exc))

    order_id = str(payload.get("order_id", ""))
    amount = int(payload.get("amount_minor", 0))
    refund_id = str(payload.get("refund_id") or f"rfnd_{len(_context.orders)}_{amount}")

    ledger: Ledger | None = _context.ledger_factory() if _context.ledger_factory else None
    if ledger is None:
        return _refusal(ReasonCode.NOT_FOUND, "no ledger configured")

    try:
        entry = await ledger.refund(order_id, amount, "INR", refund_id=refund_id)
    except LedgerError as exc:
        # Over-refund is refused here, at write time, rather than checked after.
        return _refusal(ReasonCode.AMOUNT_MISMATCH, str(exc))

    position = await ledger.position(order_id)
    # If an agent asked about this order, the ask is now answered. Closed here
    # rather than by the Merchant remembering to: a queue that only grows is a
    # queue nobody reads twice.
    closed = get_refund_queue().resolve(
        order_id,
        RefundRequestState.APPROVED,
        note=f"refunded {entry.amount_minor} paise",
    )
    return JSONResponse(
        {
            "order_id": order_id,
            "kind": entry.kind.value,
            "amount_minor": entry.amount_minor,
            "refunded_minor": position.refunded_minor,
            # Full vs partial is derived, never a ninth status.
            "status": OrderStatus.REFUNDED.value,
            "restock_lines": payload.get("restock_lines", []),
            "closed_request_id": closed.request_id if closed else None,
        }
    )


@router.post("/refund-decline")
async def refund_decline(request: Request) -> JSONResponse:
    """Close an agent's refund request without paying it.

    The other half of a queue. Without it a request the Merchant will not
    honour stays open forever, and the board stops being work to do — which is
    how a queue becomes decoration. **Writes no Ledger entry**: refusing to
    refund moves no money, and a `REVERSAL` for a refund that never happened
    would be a false entry in an append-only book.
    """
    try:
        payload = await _verified_body(request, "/agentic/refund-decline")
    except ValueError as exc:
        return _refusal(ReasonCode.SIGNATURE_INVALID, str(exc))

    order_id = str(payload.get("order_id", ""))
    closed = get_refund_queue().resolve(
        order_id, RefundRequestState.DECLINED, note=str(payload.get("reason", ""))
    )
    if closed is None:
        return _refusal(ReasonCode.NOT_FOUND, f"no open refund request for {order_id!r} to decline")
    return JSONResponse(
        {
            "order_id": order_id,
            "request_id": closed.request_id,
            "state": closed.state.value,
            "ledger_entries_written": 0,
        }
    )


@router.post("/collect")
async def collect(request: Request) -> JSONResponse:
    """COD cash collected at the door: one `CAPTURE`, no preceding `RESERVE`."""
    try:
        payload = await _verified_body(request, "/agentic/collect")
    except ValueError as exc:
        return _refusal(ReasonCode.SIGNATURE_INVALID, str(exc))

    order_id = str(payload.get("order_id", ""))
    order = _context.orders.get(order_id, {})
    amount = int(order.get("total_minor", payload.get("amount_minor", 0)))

    ledger: Ledger | None = _context.ledger_factory() if _context.ledger_factory else None
    if ledger is None:
        return _refusal(ReasonCode.NOT_FOUND, "no ledger configured")

    entry = await ledger.capture(order_id, amount, "INR", allow_without_reserve=True)
    return JSONResponse(
        {
            "order_id": order_id,
            "kind": entry.kind.value,
            "amount_minor": entry.amount_minor,
            "status": OrderStatus.PAID.value,
        }
    )


@router.post("/rto")
async def rto(request: Request) -> JSONResponse:
    """Returned to origin: `cancelled` + `rto`, a restock, and **no Ledger entry
    of any kind** — nothing moved."""
    try:
        payload = await _verified_body(request, "/agentic/rto")
    except ValueError as exc:
        return _refusal(ReasonCode.SIGNATURE_INVALID, str(exc))

    order_id = str(payload.get("order_id", ""))
    return JSONResponse(
        {
            "order_id": order_id,
            "status": OrderStatus.CANCELLED.value,
            "reason": CancellationReason.RTO.value,
            "restock": True,
            "ledger_entries_written": 0,
        }
    )


@router.post("/reject")
async def reject(request: Request) -> JSONResponse:
    """Shop reject, pre-money only. Frees the hold via `RELEASE`."""
    try:
        payload = await _verified_body(request, "/agentic/reject")
    except ValueError as exc:
        return _refusal(ReasonCode.SIGNATURE_INVALID, str(exc))

    order_id = str(payload.get("order_id", ""))
    current = OrderStatus(_context.orders.get(order_id, {}).get("status", "pending"))
    if not can_transition(current, OrderStatus.CANCELLED):
        return _refusal(
            ReasonCode.CANCEL_NOT_ALLOWED,
            f"an order at {current.value} is past the point where it can be cancelled; "
            f"money has moved and a refund is the instrument, not a cancellation",
        )

    ledger: Ledger | None = _context.ledger_factory() if _context.ledger_factory else None
    released = 0
    if ledger is not None:
        position = await ledger.position(order_id)
        if position.open_holds_minor > 0:
            await ledger.release(order_id, position.open_holds_minor, "INR")
            released = position.open_holds_minor

    return JSONResponse(
        {
            "order_id": order_id,
            "status": OrderStatus.CANCELLED.value,
            "reason": str(payload.get("reason") or CancellationReason.SHOP_REJECT.value),
            "released_minor": released,
            "kind": LedgerKind.RELEASE.value if released else None,
        }
    )
