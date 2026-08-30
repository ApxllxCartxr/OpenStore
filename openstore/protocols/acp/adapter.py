"""ACP (Agentic Commerce Protocol) adapter (INTEROP_SPEC §6.2).

ACP is the checkout-lifecycle protocol: `create checkout session` → `update
session` → `complete checkout` → `cancel checkout`. A delegated payment
credential from a PSP caps the transaction at AAL1 (§4, §6.2c); if the merchant
policy requires a higher tier for the basket, the adapter returns a step-up
response rather than proceeding.

Currency/amount: ACP expresses monetary values in integer minor units (paise),
matching our internal representation, so conversion is the identity in both
directions (R6.2b). `test_acp_amounts_round_trip` proves this.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple

from openstore.core.api import CommerceCore
from openstore.core.types import (
    Actor,
    AuthorityPresentation,
    DeliveryAddress,
    LineItemRequest,
    normalize_scopes,
)

# External field names on the ACP boundary. Every one MUST appear in mapping.md.
ACP_EXTERNAL_FIELDS: Tuple[str, ...] = (
    "checkout_session", "cart_items", "buyer_context", "delegated_payment_credential",
    "status", "amount", "currency", "complete_checkout", "cancel_checkout",
)

# ACP checkout lifecycle states -> our CheckoutView status. KeyError on anything
# unmapped: an unmapped state is a bug to surface, not to paper over (R6.2a).
ACP_STATE_BY_CHECKOUT_STATUS: dict[str, str] = {
    "created": "CREATED",
    "ready_for_payment": "READY",
    "completed": "COMPLETED",
    "cancelled": "CANCELLED",
    "expired": "EXPIRED",
}


def to_acp_amount(minor: int) -> dict:
    return {"currency": "INR", "value": minor}


def from_acp_amount(acp_amount: Mapping[str, Any]) -> int:
    # Identity under ACP's integer-minor-unit convention. Other conventions are
    # converted here ONLY, never in the core.
    return int(acp_amount["value"])


class AcpAdapter:
    def __init__(self, core: CommerceCore):
        self.core = core

    def _actor(self, session_id: str, buyer: Mapping[str, Any]) -> Actor:
        opaque = buyer.get("agent_id") or session_id
        return Actor(
            subject=f"acp:{opaque}",
            display_name=str(buyer.get("name", "acp-agent")),
            scopes=normalize_scopes(("catalog:read", "cart:write", "checkout:initiate", "checkout:confirm")),
            protocol="acp",
            protocol_session_id=session_id,
            auth_method=buyer.get("auth_method", "oauth2_bearer"),
        )

    def create_checkout_session(self, session_id: str, cart_items: Sequence[Mapping[str, Any]],
                                buyer: Mapping[str, Any], delivery: str = "") -> dict:
        reqs = tuple(LineItemRequest(sku=i["sku"], qty=int(i["qty"])) for i in cart_items)
        actor = self._actor(session_id, buyer)
        cart = self.core.create_cart(actor, reqs)
        checkout = self.core.initiate_checkout(actor, cart.cart_id, DeliveryAddress(raw=delivery))
        return {
            "checkout_session": session_id,
            "status": ACP_STATE_BY_CHECKOUT_STATUS["created"],
            "checkout_id": checkout.checkout_id,
            "amount": to_acp_amount(checkout.total_minor),
            "required_aal": checkout.required_aal,
        }

    def complete_checkout(self, session_id: str, checkout_id: str,
                          delegated_token: Mapping[str, Any], buyer: Mapping[str, Any],
                          idempotency_key: str = "") -> dict:
        actor = self._actor(session_id, buyer)
        required_aal = self.core._checkouts[checkout_id].required_aal
        authority = AuthorityPresentation(
            scheme="acp_delegated_token",
            raw={"token_present": True, "credential": dict(delegated_token)},
            policy_json=None,
        )
        result = self.core.confirm_checkout(actor, checkout_id, authority, idempotency_key)
        # §6.2c — a delegated credential caps at AAL1. If the basket required more,
        # do not proceed; return a step-up/decline response instead.
        if result.aal_level < required_aal:
            return {"status": "STEP_UP_REQUIRED", "checkout_id": checkout_id,
                    "detail": "delegated payment credential caps at AAL1; human authorisation required"}
        return {
            "status": ACP_STATE_BY_CHECKOUT_STATUS["completed"],
            "checkout_id": checkout_id,
            "order": result.status,
            "aal_level": result.aal_level,
            "amount": to_acp_amount(self.core._checkouts[checkout_id].total_minor),
        }
