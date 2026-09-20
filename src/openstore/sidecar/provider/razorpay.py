"""Razorpay, declaring UPI, refusing live keys at boot in demo mode.

Cards and netbanking are enableable without touching the money core (ADR-0013):
the adapter declares what it can do, the Merchant enables a subset, and the Gate
refuses the rest with `method-not-supported` naming what is enabled.

The live-key refusal is duplicated here on purpose. `core/settings.py` already
refuses the dangerous combinations at boot, and this checks again at
construction, because the two guard different mistakes: settings guards the
deployment, this guards a caller that built an adapter by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from openstore.sidecar.core.codes import PaymentMethod
from openstore.sidecar.core.settings import BootRefused
from openstore.sidecar.provider.trait import (
    PaymentLink,
    PaymentProvider,
    PaymentStatus,
    RefundResult,
)

TEST_KEY_PREFIX = "rzp_test_"


@dataclass
class RazorpayProvider(PaymentProvider):
    """The network calls land in A7 with the real install; the shape is fixed
    here so the Gate and the webhook path are written against it."""

    key_id: str
    key_secret: str
    demo_mode: bool = True
    name: str = "razorpay"

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

    async def make_link(
        self, order_id: str, amount_minor: int, currency: str, *, expires_in_seconds: int = 900
    ) -> PaymentLink:
        raise NotImplementedError("razorpay network calls land with A7")

    async def check_status(self, link_id: str) -> PaymentStatus:
        raise NotImplementedError("razorpay network calls land with A7")

    async def cancel(self, link_id: str) -> None:
        raise NotImplementedError("razorpay network calls land with A7")

    async def refund(
        self, reference: str, amount_minor: int, currency: str, *, refund_id: str
    ) -> RefundResult:
        raise NotImplementedError("razorpay network calls land with A7")
