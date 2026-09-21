"""The Payment Provider trait: four methods, and a seam that stays shut.

The Provider is **the Merchant's own account** (SPEC §2) and is untrusted: it
decides the money-moved fact and never authority. That one fact is load-bearing
well beyond this file — ADR-0021's ECO/TCS boundary and ADR-0023's
payment-orchestration boundary both rest on settlement running Consumer →
Merchant's own account, untouched by us.

`block / capture-block / release-block` are declared here and consumed by
nothing in v1. They are the seam ADR-0024's repeat-purchase work will want, and
explicitly **not** a Reserve Pay COD hold — ADR-0018 retired that, because UPI
has no primitive that holds funds across a delivery and Reserve Pay turned out
to be a prepaid reserve rather than the authorize-then-capture it was taken for.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from openstore.sidecar.core.codes import PaymentMethod, ProviderOp


@dataclass(frozen=True)
class PaymentLink:
    link_id: str
    url: str
    amount_minor: int
    currency: str
    expires_in_seconds: int


@dataclass(frozen=True)
class PaymentStatus:
    link_id: str
    order_id: str
    paid: bool
    amount_minor: int
    currency: str
    reference: str = ""
    cancelled: bool = False


@dataclass(frozen=True)
class WebhookEvent:
    """The two facts a callback body has to yield, and nothing more.

    Deliberately not "what the event says happened": the body is a trigger, and
    the sidecar asks `check_status` what the Provider actually holds. Parsing a
    verdict out of here would make a forged-but-signed replay authoritative.
    """

    event_id: str
    link_id: str
    payer_handle: str = ""
    """The payer's own handle where the rail gives one, for `consumer_id`
    (ADR-0011). Empty is honest; a fabricated one is not."""


@dataclass(frozen=True)
class RefundResult:
    refund_id: str
    amount_minor: int
    currency: str


class PaymentProvider(ABC):
    """Every adapter declares its method set at boot, and the Merchant enables a
    subset in `/agentic` (ADR-0013). Anything outside the enabled set refuses
    `method-not-supported` naming what *is* enabled — at the Gate, not here."""

    name: str

    webhook_signature_header: str = "x-openstore-signature"
    """Where this Provider puts its HMAC. Declared by the adapter because it is
    the adapter's own wire format, and read by the route — a header name guessed
    at the call site is a signature nobody checks."""

    @abstractmethod
    def declared_methods(self) -> frozenset[PaymentMethod]:
        """What this adapter can do at all, before a Merchant enables anything."""

    def declared_ops(self) -> frozenset[ProviderOp]:
        """The four every adapter implements. An adapter that also declares the
        block capability overrides this; nothing in v1 does."""
        return frozenset(
            {ProviderOp.MAKE_LINK, ProviderOp.CHECK_STATUS, ProviderOp.CANCEL, ProviderOp.REFUND}
        )

    @abstractmethod
    async def make_link(
        self, order_id: str, amount_minor: int, currency: str, *, expires_in_seconds: int
    ) -> PaymentLink: ...

    @abstractmethod
    async def check_status(self, link_id: str) -> PaymentStatus:
        """The adoption path. A crash between `make_link` and storing its id is
        recovered by asking the Provider what happened, never by creating a
        second link."""

    @abstractmethod
    async def cancel(self, link_id: str) -> None: ...

    @abstractmethod
    async def refund(
        self, reference: str, amount_minor: int, currency: str, *, refund_id: str
    ) -> RefundResult:
        """Partial amounts are legal, so the Provider is given the refund's own
        id — the same reason the Ledger key carries it."""

    @abstractmethod
    def read_webhook(self, payload: dict[str, Any]) -> WebhookEvent:
        """Pull the event id and the link id out of this Provider's envelope.

        Abstract rather than defaulted: every rail shapes its callback
        differently, and a default that guessed at field names would silently
        yield an empty `link_id` — which the route would read as "no checkout is
        waiting on that payment" rather than as the adapter bug it is.
        """

    def supports(self, method: PaymentMethod) -> bool:
        return method in self.declared_methods()
