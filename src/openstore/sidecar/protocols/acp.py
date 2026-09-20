"""ACP — five operations across four paths, targeting spec **2026-04-17**.

The version is named everywhere it appears because this specification has had
five dated releases in under a year. "ACP conformant" unqualified ages badly
against a quarterly revision, so the badge carries the date and a test asserts
the vendored OpenAPI is the one we built against.

Four of the five operations work normally. `completeCheckoutSession` is **the
refusal point**: it is where the buyer's delegated credential (a Shared Payment
Token, or a `vt_…` vault token under the Delegate Payment spec) is handed to the
merchant, and that is exactly the authority we removed from agents. It refuses
with a named code and returns the approve URL — ADR-0008 and ADR-0013 enforced
at the envelope boundary rather than left as an unimplemented gap.

Two things fall out of the spec that cost us nothing: ACP has a **native
`Idempotency-Key`**, which maps straight onto our per-attempt keys, and its
delegate authentication is OAuth 2.0, which is already our allowlisted-agent
admission route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openstore.sidecar.core.codes import OrderStatus, Protocol, ReasonCode
from openstore.sidecar.trait.models import Quote

SPEC_VERSION = "2026-04-17"

#: Per-operation, not blanket (§16.12). `getCheckoutSession` takes no
#: `Idempotency-Key` because it mutates nothing, and pretending otherwise would
#: be inventing a requirement the spec does not have.
REQUIRED_HEADERS: dict[str, tuple[str, ...]] = {
    "createCheckoutSession": ("Authorization", "API-Version", "Idempotency-Key", "Content-Type"),
    "updateCheckoutSession": ("Authorization", "API-Version", "Idempotency-Key", "Content-Type"),
    "getCheckoutSession": ("Authorization", "API-Version"),
    "completeCheckoutSession": (
        "Authorization",
        "API-Version",
        "Idempotency-Key",
        "Content-Type",
    ),
    "cancelCheckoutSession": ("Authorization", "API-Version", "Idempotency-Key"),
}

OPERATION_PATHS: dict[str, tuple[str, str]] = {
    "createCheckoutSession": ("POST", "/checkout_sessions"),
    "updateCheckoutSession": ("POST", "/checkout_sessions/{checkout_session_id}"),
    "getCheckoutSession": ("GET", "/checkout_sessions/{checkout_session_id}"),
    "completeCheckoutSession": ("POST", "/checkout_sessions/{checkout_session_id}/complete"),
    "cancelCheckoutSession": ("POST", "/checkout_sessions/{checkout_session_id}/cancel"),
}


class AcpError(Exception):
    def __init__(self, code: ReasonCode, detail: str, *, status: int = 400) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status

    def to_payload(self) -> dict[str, Any]:
        """ACP's own `Error` schema shape."""
        return {"type": "invalid_request", "code": self.code.value, "message": self.detail}


def check_headers(operation: str, headers: dict[str, str]) -> None:
    """Fail loud on a missing header rather than defaulting one.

    `API-Version` in particular: a request that does not say which version it
    speaks is one we would have to guess for, and guessing against a quarterly
    spec is how a silent incompatibility ships.
    """
    provided = {k.lower() for k in headers}
    missing = [h for h in REQUIRED_HEADERS[operation] if h.lower() not in provided]
    if missing:
        raise AcpError(
            ReasonCode.SIGNATURE_INVALID,
            f"{operation} requires {', '.join(missing)}",
            status=400,
        )
    version = next((v for k, v in headers.items() if k.lower() == "api-version"), "")
    if version and version != SPEC_VERSION:
        raise AcpError(
            ReasonCode.METHOD_NOT_SUPPORTED,
            f"this sidecar implements ACP {SPEC_VERSION}; the request asked for {version}",
            status=400,
        )


@dataclass
class CheckoutSession:
    """Our Pending Cart, wearing ACP's shape."""

    id: str
    status: str
    currency: str = "INR"
    line_items: list[dict[str, Any]] = field(default_factory=list)
    totals: list[dict[str, Any]] = field(default_factory=list)
    fulfillment_options: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "currency": self.currency,
            "line_items": self.line_items,
            "totals": self.totals,
            "fulfillment_options": self.fulfillment_options,
            "messages": self.messages,
        }


def session_from_quote(
    session_id: str, quote: Quote, *, status: str = "ready_for_payment"
) -> CheckoutSession:
    """Read a Quote into ACP's session shape. No arithmetic — the Merchant's
    sums are carried across, never recomputed."""
    totals = [
        {"type": "items_base_amount", "display_text": "Subtotal", "amount": quote.subtotal_minor},
    ]
    discount = sum(d.amount_minor for d in quote.discount_lines)
    if discount:
        totals.append({"type": "items_discount", "display_text": "Discount", "amount": discount})
    totals.append(
        {
            "type": "fulfillment",
            "display_text": "Delivery",
            "amount": quote.fulfillment_chosen.cost_minor,
        }
    )
    totals.append(
        {
            "type": "tax",
            "display_text": "GST",
            "amount": sum(t.amount_minor for t in quote.tax_lines),
        }
    )
    totals.append({"type": "total", "display_text": "Total", "amount": quote.total_minor})

    return CheckoutSession(
        id=session_id,
        status=status,
        currency=quote.currency,
        line_items=[
            {
                "id": line.sku,
                "item": {"id": line.sku, "quantity": line.qty},
                "base_amount": line.unit_price_minor * line.qty,
                "total_amount": line.line_total_minor,
            }
            for line in quote.lines
        ],
        totals=totals,
        fulfillment_options=[
            {
                "type": "shipping",
                "id": option.id,
                "title": option.label,
                "total": option.cost_minor,
                # A day count, never a date: no clock crosses door 9.
                "earliest_delivery_days": option.eta_days,
            }
            for option in quote.fulfillment_options
        ],
    )


def complete(
    session: CheckoutSession, *, approve_url: str, payment_data: dict[str, Any] | None
) -> dict[str, Any]:
    """**The refusal point.**

    A `complete` carrying a delegated payment credential is refused with
    `method-not-supported` and handed the approve URL instead. The refusal is
    the product: an agent-held credential is the authority we removed from
    agents, and its absence is not a gap (ADR-0008, ADR-0013, ADR-0016).

    A `complete` with no credential is refused the same way and for the same
    reason — there is no completion path here that does not go through a human.
    """
    carried = sorted(payment_data.keys()) if payment_data else []
    return {
        "type": "invalid_request",
        "code": ReasonCode.METHOD_NOT_SUPPORTED.value,
        "message": (
            "This merchant does not accept delegated payment credentials. Completion is a "
            "same-domain handoff: send the buyer to the approve URL, where they authorize "
            "the exact amount themselves."
        ),
        "approve_url": approve_url,
        "checkout_session_id": session.id,
        "acp_version": SPEC_VERSION,
        "refused_step": "completeCheckoutSession",
        "credential_fields_ignored": carried,
    }


def cancel(session: CheckoutSession) -> dict[str, Any]:
    """Pre-money only. `cancelled` already means stock returned and nothing
    taken, so ACP's cancel maps onto it with nothing left over."""
    session.status = OrderStatus.CANCELLED.value
    return session.to_payload()


def envelope(session: CheckoutSession) -> dict[str, Any]:
    return {"protocol": Protocol.ACP.value, "acp_version": SPEC_VERSION, **session.to_payload()}
