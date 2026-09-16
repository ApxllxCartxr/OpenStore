# In-process PSP client for `openstore demo` — no Razorpay account required.
#
# This is NOT a second driver. It is a stand-in for the `razorpay.Client`
# object, exposing exactly the five methods razorpay_driver.py calls:
#
#   payment_link.create / .fetch / .all / .cancel      payment.refund
#
# Everything above it — the INV-4 dual write, idempotency records, the
# ledger CAPTURE entries, the allowed-transition table, evidence bundling —
# runs unmodified against it. `_get_client` is the only switch.
#
# Payment itself is driven by the buyer, not by a timer: create() returns a
# short_url pointing at /demo/pay/<link id>, and the button on that page
# feeds a REAL HMAC-signed payment_link.paid webhook back through
# process_incoming_webhook. A demo payment therefore exercises the same
# signature verification and the same state machine a live one does.
#
# Links live in a module-level dict: demo mode is one process, one merchant,
# one sitting, and a restart is meant to start clean.

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from openstore.config import Settings

# link id -> link entity, in Razorpay's response shape.
_LINKS: dict[str, dict[str, Any]] = {}

DEMO_KEY_ID = "rzp_test_demo"
DEMO_WEBHOOK_SECRET = "demo_webhook_secret"


def reset() -> None:
    """Drop every demo link (a fresh `openstore demo`, or a test)."""
    _LINKS.clear()


def get_link(link_id: str) -> dict[str, Any] | None:
    return _LINKS.get(link_id)


def demo_base_url(config: Settings) -> str:
    return (config.public_base_url or config.webauthn.origin or "http://localhost:8000").rstrip("/")


class _PaymentLinkAPI:
    def __init__(self, config: Settings) -> None:
        self._config = config

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        reference_id = str(request.get("reference_id", ""))
        # Mirror the live duplicate-reference rejection: create_payment_link
        # has a recovery path keyed on exactly this error, and a demo that
        # never triggers it would leave that path unexercised.
        for existing in _LINKS.values():
            if existing["reference_id"] == reference_id and existing["status"] == "created":
                raise DemoDuplicateReferenceError(reference_id)
        link_id = f"plink_demo{secrets.token_hex(8)}"
        link = {
            "id": link_id,
            "reference_id": reference_id,
            "amount": int(request.get("amount", 0)),
            "currency": request.get("currency", "INR"),
            "description": request.get("description", ""),
            "notes": dict(request.get("notes", {})),
            "status": "created",
            "short_url": f"{demo_base_url(self._config)}/demo/pay/{link_id}",
            "created_at": int(datetime.now(UTC).timestamp()),
            "payments": [],
        }
        _LINKS[link_id] = link
        return dict(link)

    def fetch(self, link_id: str) -> dict[str, Any]:
        link = _LINKS.get(link_id)
        if link is None:
            raise DemoPspError(f"payment link {link_id} not found")
        return dict(link)

    def all(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        reference_id = (params or {}).get("reference_id")
        items = [
            dict(link)
            for link in _LINKS.values()
            if reference_id is None or link["reference_id"] == reference_id
        ]
        return {"count": len(items), "items": items}

    def cancel(self, link_id: str) -> dict[str, Any]:
        link = _LINKS.get(link_id)
        if link is None:
            raise DemoPspError(f"payment link {link_id} not found")
        link["status"] = "cancelled"
        return dict(link)


class _PaymentAPI:
    def refund(self, payment_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"rfnd_demo{secrets.token_hex(8)}",
            "payment_id": payment_id,
            "amount": int(request.get("amount", 0)),
            "status": "processed",
            "speed_processed": "normal",
        }


class DemoPspError(Exception):
    """Demo-side failure, shaped like the SDK's (str() is what is read)."""


class DemoDuplicateReferenceError(DemoPspError):
    """Carries the code is_duplicate_reference_error matches on."""

    def __init__(self, reference_id: str) -> None:
        super().__init__(f"Payment link with reference_id {reference_id} already exists")
        self.code = "BAD_REQUEST_ERROR"


class DemoClient:
    """Duck-typed stand-in for razorpay.Client."""

    def __init__(self, config: Settings) -> None:
        self.payment_link = _PaymentLinkAPI(config)
        self.payment = _PaymentAPI()


def mark_paid(link_id: str, payment_id: str) -> dict[str, Any]:
    """Flip a link to paid and attach the payment, as the PSP would."""
    link = _LINKS[link_id]
    link["status"] = "paid"
    link["amount_paid"] = link["amount"]
    link["payments"] = [
        {
            "id": payment_id,
            "amount": link["amount"],
            "status": "captured",
            "method": "demo",
        }
    ]
    return dict(link)
