"""Webhook intake: HMAC on raw bytes, `event_id` dedupe, out-of-order safe.

Three properties, and each one exists because the alternative has a name:

- **HMAC over the raw bytes.** Not over a re-serialization. Two JSON encoders
  disagree about key order and whitespace, so a signature verified against a
  round-trip verifies nothing.
- **`event_id` dedupe.** Providers retry. A webhook delivered twice must
  capture once.
- **Out-of-order safe.** Providers do not promise ordering. A `paid` arriving
  after a `failed` for the same order must not resurrect it, and the way to get
  that is to treat events as triggers and re-read state, never as instructions.

The Provider decides the money-moved fact only, never authority (SPEC §8).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any


class WebhookRejected(Exception):
    """Refused before anything was acted on. Never logged-and-continued: a
    webhook the sidecar cannot verify is one it must not believe."""


@dataclass
class WebhookVerifier:
    """One per Provider, holding that Provider's own secret."""

    secret: str
    _seen: set[str] = field(default_factory=set)

    def verify(self, *, raw_body: bytes, signature: str) -> dict[str, Any]:
        """Verify, then parse. Parsing first would mean acting on the shape of a
        document before knowing it is ours."""
        import json

        expected = hmac.new(self.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            raise WebhookRejected("signature does not verify")

        try:
            payload = json.loads(raw_body)
        except ValueError as exc:
            raise WebhookRejected(f"verified body is not JSON: {exc}") from None
        if not isinstance(payload, dict):
            raise WebhookRejected("webhook payload must be an object")
        return payload

    def is_duplicate(self, event_id: str) -> bool:
        """`event_id` dedupe. Idempotent by remembering, because a Provider that
        retries is behaving correctly and the sidecar must absorb it."""
        if not event_id:
            raise WebhookRejected("webhook carries no event_id; dedupe is not optional")
        if event_id in self._seen:
            return True
        self._seen.add(event_id)
        return False


@dataclass(frozen=True)
class Reconcile:
    """The only post-payment check there is.

    A Merchant price edit between tap and webhook must not fail a paid order —
    that strands real money against no order. So this compares the Provider's
    amount and currency against the **pinned** Quote and nothing else.
    """

    matches: bool
    detail: str = ""


def reconcile(
    *,
    provider_amount_minor: int,
    provider_currency: str,
    pinned_amount_minor: int,
    pinned_currency: str,
) -> Reconcile:
    if provider_currency != pinned_currency:
        return Reconcile(
            False, f"provider settled in {provider_currency}, order is {pinned_currency}"
        )
    if provider_amount_minor != pinned_amount_minor:
        return Reconcile(
            False,
            f"provider moved {provider_amount_minor} paise against an order of "
            f"{pinned_amount_minor}",
        )
    return Reconcile(True)
