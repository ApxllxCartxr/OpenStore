"""Razorpay, over its Payment Links API.

Cards and netbanking are enableable without touching the money core (ADR-0013):
the adapter declares what it can do, the Merchant enables a subset, and the Gate
refuses the rest with `method-not-supported` naming what is enabled.

The live-key refusal is duplicated here on purpose. `core/settings.py` already
refuses the dangerous combinations at boot, and this checks again at
construction, because the two guard different mistakes: settings guards the
deployment, this guards a caller that built an adapter by hand.

**Over `httpx`, not Razorpay's own SDK.** The SDK is synchronous, and a
synchronous HTTP call inside a FastAPI handler blocks the whole event loop for
the length of Razorpay's round trip — the same reason the trait client is async.

**No Consumer PII crosses this seam.** Razorpay's create call accepts a customer
name, email and phone and will happily notify them; none of it is sent. The
sidecar holds commitments to the Destination and Contact Point that are supposed
to become unopenable on erasure (ADR-0011), and a copy sitting in a Provider's
dashboard is a copy erasure cannot reach.

**Amounts are already paise**, which is what Razorpay wants — `amount` is in the
smallest unit of the currency. No conversion happens here, and that is the point:
a money path with a multiply in it has a rounding rule nobody wrote down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from openstore.sidecar.core.codes import PaymentMethod
from openstore.sidecar.core.settings import BootRefused
from openstore.sidecar.provider.trait import (
    PaymentLink,
    PaymentProvider,
    PaymentStatus,
    RefundResult,
    WebhookEvent,
)

TEST_KEY_PREFIX = "rzp_test_"

API_BASE = "https://api.razorpay.com/v1"

#: Razorpay refuses an `expire_by` that is not **more than** 15 minutes out, and
#: §16.7's payment window is exactly 15 minutes. Taken literally, every link this
#: sidecar asked for would be refused at creation — so the request is padded, and
#: the padded lifetime is what comes back in `PaymentLink.expires_in_seconds`.
#: The checkout already treats the Provider's own lifetime as the ceiling on the
#: stock hold rather than assuming the window it asked for, so a link that lives
#: slightly longer than requested shortens nothing and surprises nobody.
MIN_EXPIRY_SECONDS = 16 * 60

#: Razorpay statuses that mean the link will never be paid.
DEAD_STATUSES = frozenset({"cancelled", "expired"})


class RazorpayRefused(Exception):
    """Razorpay answered, and the answer was no.

    Distinct from a transport failure on purpose: a refused call is a fact about
    this payment, and a timeout is a fact about the network. The money path
    retries one and not the other.
    """

    def __init__(self, operation: str, status: int, detail: str) -> None:
        super().__init__(f"razorpay {operation} failed ({status}): {detail}")
        self.operation = operation
        self.status = status
        self.detail = detail


@dataclass
class RazorpayProvider(PaymentProvider):
    """One Merchant's own Razorpay account. Untrusted, and never an authority."""

    key_id: str
    key_secret: str
    demo_mode: bool = True
    name: str = "razorpay"
    webhook_signature_header: str = "x-razorpay-signature"
    timeout_seconds: int = 20
    """Whole seconds, because the money lint is right that a float in this file
    is worth a second look — and a timeout has no business being fractional."""
    client: httpx.AsyncClient | None = field(default=None, repr=False)
    """Injected in tests against a transport. `None` means one per call, which
    is right for a seam touched a few times per order."""

    def __post_init__(self) -> None:
        if self.demo_mode and not self.key_id.startswith(TEST_KEY_PREFIX):
            raise BootRefused(
                f"razorpay key {self.key_id[:12]!r} is not a {TEST_KEY_PREFIX} key and demo "
                f"mode is on. Every demo receipt is marked demo, and a demo that can move "
                f"real money is not a demo."
            )

    def declared_methods(self) -> frozenset[PaymentMethod]:
        """UPI in v1. Cards and netbanking are a Merchant toggle away and need
        no money-core change."""
        return frozenset({PaymentMethod.UPI})

    # ── the wire ─────────────────────────────────────────────────────────────

    async def _call(
        self, operation: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One Razorpay call, with its refusal separated from its failure.

        A non-2xx is raised as `RazorpayRefused` carrying Razorpay's own
        description, because that string is what an operator needs at 2am and
        inventing our own would lose it. A transport failure raises whatever
        httpx raised: the caller must be able to tell "Razorpay said no" from
        "Razorpay did not answer", since only one of them is safe to retry.
        """
        client = self.client
        owned = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.request(
                method,
                f"{API_BASE}{path}",
                json=body,
                auth=(self.key_id, self.key_secret),
                headers={"content-type": "application/json"},
            )
        finally:
            if owned:
                await client.aclose()

        if response.status_code >= 400:
            raise RazorpayRefused(operation, response.status_code, _describe(response))
        parsed: dict[str, Any] = response.json()
        return parsed

    # ── the four calls ───────────────────────────────────────────────────────

    async def make_link(
        self, order_id: str, amount_minor: int, currency: str, *, expires_in_seconds: int = 900
    ) -> PaymentLink:
        """Create the link, or adopt the one this order already has.

        `reference_id` is the `order_id`, and Razorpay refuses a duplicate. That
        refusal is **the recovery path, not an error**: it means a previous
        attempt created the link and crashed before storing its id, so the link
        is fetched and returned rather than a second one being created. A second
        link for one order is two ways for a Consumer to pay for it.
        """
        lifetime = max(expires_in_seconds, MIN_EXPIRY_SECONDS)
        body = {
            "amount": amount_minor,
            "currency": currency,
            "reference_id": order_id,
            "expire_by": int(time.time()) + lifetime,
            "description": f"Order {order_id}",
            # Razorpay would email and SMS the Consumer if it had their details.
            # It does not have them, and this says so rather than relying on
            # that: a notification we did not send is one the Merchant cannot
            # explain.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
        }

        try:
            created = await self._call("make_link", "POST", "/payment_links", body)
        except RazorpayRefused:
            existing = await self._link_for(order_id)
            if existing is None:
                raise
            created = existing

        return PaymentLink(
            link_id=str(created["id"]),
            url=str(created["short_url"]),
            amount_minor=int(created["amount"]),
            currency=str(created["currency"]),
            # What Razorpay actually granted, not what was asked for. The stock
            # hold takes this as its ceiling.
            expires_in_seconds=max(0, int(created.get("expire_by", 0)) - int(time.time())),
        )

    async def check_status(self, link_id: str) -> PaymentStatus:
        """The adoption path. A crash between `make_link` and storing its id is
        recovered by asking the Provider what happened, never by creating a
        second link.

        **`paid` means paid in full.** Razorpay's `partially_paid` is not a
        success: settling against it would capture a hold the Consumer has not
        covered, and `settle` compares this amount against the total the Gate
        decided precisely so that cannot pass silently.
        """
        link = await self._call("check_status", "GET", f"/payment_links/{link_id}")
        status = str(link.get("status", ""))
        payments = link.get("payments") or []
        captured = next(
            (p for p in payments if str(p.get("status", "")) == "captured"),
            None,
        )

        return PaymentStatus(
            link_id=str(link.get("id", link_id)),
            order_id=str(link.get("reference_id", "")),
            paid=status == "paid",
            # `amount_paid`, never `amount`: the first is what arrived and the
            # second is what was asked for, and reconciling against the ask
            # would make every underpayment look like a settlement.
            amount_minor=int(link.get("amount_paid", 0)),
            currency=str(link.get("currency", "INR")),
            # The payment id, which is what a refund is issued against. Without
            # it a paid order cannot be refunded at all.
            reference=str(captured["payment_id"]) if captured else "",
            cancelled=status in DEAD_STATUSES,
        )

    async def cancel(self, link_id: str) -> None:
        """Close a link nobody is going to pay.

        A link that is already cancelled, expired or paid refuses, and that is
        not a failure of this call: cancelling is idempotent from the money
        path's point of view, and the states it refuses from are exactly the
        states where there is nothing left to cancel. A *paid* link is confirmed
        rather than assumed — cancelling one silently would be this adapter
        deciding money did not arrive.
        """
        try:
            await self._call("cancel", "POST", f"/payment_links/{link_id}/cancel")
        except RazorpayRefused:
            current = await self.check_status(link_id)
            if current.paid or current.cancelled:
                return
            raise

    async def refund(
        self, reference: str, amount_minor: int, currency: str, *, refund_id: str
    ) -> RefundResult:
        """Give money back against the payment, not against the link.

        `reference` is the `payment_id` `check_status` reported. Partial amounts
        are legal, which is why the Provider is given the refund's own id:
        Razorpay's `receipt` is its idempotency key, so a retry of one partial
        refund returns the first one rather than issuing a second — the same
        property the Ledger key has, enforced on both sides of the seam.
        """
        if not reference:
            raise RazorpayRefused(
                "refund",
                400,
                "no payment reference for this order; a refund is issued against a "
                "payment, and this order has none recorded",
            )
        result = await self._call(
            "refund",
            "POST",
            f"/payments/{reference}/refund",
            {"amount": amount_minor, "speed": "normal", "receipt": refund_id},
        )
        return RefundResult(
            refund_id=str(result["id"]),
            amount_minor=int(result["amount"]),
            currency=str(result.get("currency", currency)),
        )

    async def _link_for(self, order_id: str) -> dict[str, Any] | None:
        """The link already created for this order, if there is one."""
        found = await self._call("adopt", "GET", f"/payment_links?reference_id={order_id}")
        links = found.get("payment_links") or []
        return dict(links[0]) if links else None

    # ── the callback ─────────────────────────────────────────────────────────

    def read_webhook(self, payload: dict[str, Any]) -> WebhookEvent:
        """Razorpay's envelope: `id` on the event, and the payment link id under
        `payload.payment_link.entity.id`.

        Only the two identifiers are read. `event` ("payment_link.paid",
        "payment_link.expired") is deliberately ignored — the route asks
        `check_status` what Razorpay holds, so a replayed `paid` event for a
        link Razorpay has since expired settles nothing.
        """
        entity = (
            payload.get("payload", {}).get("payment_link", {}).get("entity", {})
            if isinstance(payload.get("payload"), dict)
            else {}
        )
        contact = entity.get("customer", {}) if isinstance(entity.get("customer"), dict) else {}
        return WebhookEvent(
            event_id=str(payload.get("id", "")),
            link_id=str(entity.get("id", "")),
            payer_handle=str(contact.get("contact", "")),
        )


def _describe(response: httpx.Response) -> str:
    """Razorpay's own description of the refusal, or the body if it has none.

    Kept verbatim. An adapter that replaced it with a friendlier sentence would
    be discarding the one string that says which field was wrong.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:400]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("description") or error.get("code") or body)[:400]
    return str(body)[:400]
