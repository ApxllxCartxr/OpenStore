# OpenStore surfaces — MCP server (20 tools, closed set per PRD §6 / REGISTRY.json)
# Per PRD S6.3 — thin adapters over core/api.py + WebAuthn RP.
# Transport: JSON-RPC 2.0 wire protocol, MCP 2025-06-18 (DECISION-036, hard
# cutover). POST /agent/mcp accepts ONLY the wire envelope; the legacy
# {"tool","arguments"} shape is answered -32600 and never executed.

from __future__ import annotations

import json
import secrets
from typing import Any

from openstore import __version__ as _server_version
from openstore.config import Settings
from openstore.config import merchant_id as _merchant_id
from openstore.core.api import CommerceError


def _require_scope(token_scopes: list[str], required: str) -> None:
    """Enforce scope (hard error per R0.3)."""
    if required not in token_scopes:
        raise CommerceError(
            "auth.insufficient_scope",
            f"Token missing required scope: {required}",
            403,
        )


class MCPToolResult:
    def __init__(
        self, success: bool, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None
    ):
        self.success = success
        self.data = data or {}
        self.error = error or {}


def search_products(
    config: Settings,
    query: str,
    tags: list[str] | None = None,
    limit: int = 20,
) -> MCPToolResult:
    """MCP tool: search_products. Returns matching catalog items."""
    try:
        from openstore.surfaces.catalog import search_catalog_items

        items = search_catalog_items(config, query, tags=tags, limit=limit)
        return MCPToolResult(success=True, data={"items": items, "count": len(items)})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def get_product(
    config: Settings,
    sku: str,
) -> MCPToolResult:
    """MCP tool: get_product. Returns a single catalog item by SKU."""
    try:
        from openstore.surfaces.catalog import get_catalog_item

        item = get_catalog_item(config, sku)
        if item is None:
            return MCPToolResult(
                success=False,
                error={"reason_code": "catalog.sku_not_found", "message": f"SKU {sku} not found"},
            )
        return MCPToolResult(success=True, data={"item": item})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def create_cart(
    config: Settings,
    session: Any,
    client_id: str,
    trace_id: str,
    merchant_id: str,
    items: list[dict[str, Any]],
    policy_id: str,
    cart_hash: str,
    cart_version: int,
    token_scopes: list[str],
    webauthn_assertion: dict[str, Any] | None = None,
) -> MCPToolResult:
    """MCP tool: create_cart. Initiates checkout, returns CompilerResult."""
    _require_scope(token_scopes, "cart:write")
    try:
        from openstore.core.api import create_checkout

        result = create_checkout(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            merchant_id=merchant_id,
            cart_items=items,
            cart_hash=cart_hash,
            cart_version=cart_version,
            policy_id=policy_id,
            webauthn_assertion=webauthn_assertion,
        )
        return MCPToolResult(
            success=True,
            data={
                "allowed": result.allowed,
                "reason_code": result.reason_code,
                "aal_level": result.aal_level,
                "effective_amount_minor": result.effective_amount_minor,
                "transcript": result.transcript,
                "checkout_id": result.checkout_id,
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def update_cart(
    config: Settings,
    session: Any,
    client_id: str,
    trace_id: str,
    merchant_id: str,
    checkout_id: str,
    items: list[dict[str, Any]],
    cart_hash: str,
    cart_version: int,
    policy_id: str,
    token_scopes: list[str],
    webauthn_assertion: dict[str, Any] | None = None,
) -> MCPToolResult:
    """MCP tool: update_cart. Replaces an existing checkout (cancel old hold, create new)."""
    _require_scope(token_scopes, "cart:write")
    try:
        from sqlmodel import select

        from openstore.core.holdcancel import cancel_hold
        from openstore.models import Checkout, OrderState

        old = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        if old and old.state == OrderState.HELD:
            cancel_hold(
                session=session,
                checkout_id=old.id,
                trace_id=trace_id,
                client_id=client_id,
                reason="Replaced by agent update_cart",
            )
        return create_cart(
            config=config,
            session=session,
            client_id=client_id,
            trace_id=trace_id,
            merchant_id=merchant_id,
            items=items,
            policy_id=policy_id,
            cart_hash=cart_hash,
            cart_version=cart_version,
            token_scopes=token_scopes,
            webauthn_assertion=webauthn_assertion,
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def checkout_initiate(
    config: Settings,
    session: Any,
    client_id: str,
    trace_id: str,
    checkout_id: str,
    token_scopes: list[str],
    chat_platform: str | None = None,
    chat_user_id: str | None = None,
    chat_channel_id: str | None = None,
    request_text: str | None = None,
) -> MCPToolResult:
    """MCP tool: checkout_initiate. Creates a Razorpay payment link for the checkout.

    chat_platform/chat_user_id/chat_channel_id (S11 Phase 3 / Q-018) are
    optional: only chat-originated checkouts (BuyerBot._handle_shop) carry
    them. When present they are stamped onto the Checkout row before the
    payment link is created, so the webhook worker and hold-release loop
    know which Discord user to DM, and a minimal `customer` object (name
    only — no fabricated email/phone, R0.3) is sent to Razorpay.

    request_text (S11 Phase 4 / Q-020) is the original chat goal, stamped
    alongside the chat-identity columns so a completed purchase's evidence
    bundle can populate PoAI human_intent.
    """
    _require_scope(token_scopes, "checkout:initiate")
    try:
        from sqlmodel import select

        from openstore.models import Checkout
        from openstore.psp.razorpay_driver import RazorpayError, create_payment_link

        checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        if checkout is None:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_found",
                    "message": f"Checkout {checkout_id} not found",
                },
            )

        if chat_user_id:
            checkout.chat_platform = chat_platform
            checkout.chat_user_id = chat_user_id
            checkout.chat_channel_id = chat_channel_id
            checkout.request_text = request_text
            session.add(checkout)
            session.flush()

        customer = {"name": f"Discord user {chat_user_id}"} if chat_user_id else None

        checkout = create_payment_link(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout_id,
            amount_minor=checkout.amount_minor,
            currency=checkout.currency,
            customer=customer,
        )
        return MCPToolResult(
            success=True,
            data={
                "checkout_id": checkout.id,
                "state": checkout.state.value,
                "payment_link_id": checkout.psp_payment_link_id,
                "short_url": checkout.short_url,
                "cancel_token": checkout.cancel_token,
                "amount_minor": checkout.amount_minor,
                "currency": checkout.currency,
                "expires_at": checkout.expires_at.isoformat(),
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except RazorpayError as e:
        # psp.* is a registered REGISTRY namespace (R0.2) — RazorpayError's
        # error_code already lives there, it was just falling into the
        # generic-Exception branch below and flattening to "internal_error"
        # because RazorpayError doesn't subclass CommerceError.
        return MCPToolResult(
            success=False, error={"reason_code": e.error_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def checkout_confirm(
    config: Settings,
    session: Any,
    client_id: str,
    trace_id: str,
    checkout_id: str,
    token_scopes: list[str],
    webauthn_assertion: dict[str, Any] | None = None,
) -> MCPToolResult:
    """MCP tool: checkout_confirm. Confirms the checkout after payment link is paid.

    Real authorization re-check: for AAL1+ flows it re-verifies the caller's
    WebAuthn assertion via confirm_checkout; AAL0 (no_human_authority) skips the
    per-cart human assertion. The spend cap is always re-checked server-side.
    """
    _require_scope(token_scopes, "checkout:confirm")
    try:
        from sqlmodel import select

        from openstore.core.api import confirm_checkout
        from openstore.models import Checkout

        stored = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        psp_order_id = stored.psp_order_id if stored else checkout_id
        psp_payment_link_id = stored.psp_payment_link_id if stored else ""

        checkout = confirm_checkout(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout_id,
            psp_order_id=psp_order_id,
            psp_payment_link_id=psp_payment_link_id,
            webauthn_assertion=webauthn_assertion,
        )
        return MCPToolResult(
            success=True,
            data={
                "checkout_id": checkout.id,
                "state": checkout.state.value,
                "cancel_token": checkout.cancel_token,
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def get_order(
    config: Settings,
    session: Any,
    trace_id: str,
    client_id: str,
    checkout_id: str,
) -> MCPToolResult:
    """MCP tool: get_order. Returns the current state of an order/checkout."""
    try:
        from openstore.core.api import get_checkout

        checkout = get_checkout(
            session=session, trace_id=trace_id, client_id=client_id, checkout_id=checkout_id
        )
        if checkout is None:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_found",
                    "message": f"Checkout {checkout_id} not found",
                },
            )
        return MCPToolResult(
            success=True,
            data={
                "checkout_id": checkout.id,
                "state": checkout.state.value,
                "amount_minor": checkout.amount_minor,
                "currency": checkout.currency,
                "aal_level": checkout.aal_level,
                "cart_hash": checkout.cart_hash,
                "created_at": checkout.created_at.isoformat() if checkout.created_at else None,
                "expires_at": checkout.expires_at.isoformat() if checkout.expires_at else None,
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def list_orders(
    config: Settings,
    session: Any,
    chat_platform: str,
    chat_user_id: str,
    limit: int = 5,
) -> MCPToolResult:
    """MCP tool: list_orders (S14). Recent checkouts belonging to ONE chat
    identity, newest first — read-only, no scope required (matches
    get_order/list_campaigns). This is what closes the federated buyer's
    "did my order go through" / "which store has my open order" gap:
    get_order needs an exact checkout_id, which a remote buyer process
    never learns on its own."""
    try:
        from openstore.core.api import list_checkouts_for_buyer

        checkouts = list_checkouts_for_buyer(session, chat_platform, chat_user_id, limit=limit)
        return MCPToolResult(
            success=True,
            data={
                "orders": [
                    {
                        "checkout_id": c.id,
                        "state": c.state.value,
                        "amount_minor": c.amount_minor,
                        "currency": c.currency,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                    }
                    for c in checkouts
                ]
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def cancel_order(
    config: Settings,
    session: Any,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    chat_platform: str,
    chat_user_id: str,
    token_scopes: list[str],
) -> MCPToolResult:
    """MCP tool: cancel_order (S14). Wraps the existing cancel_checkout_by_id
    (psp/razorpay_driver.py — same function POST /hold/{cancel_token}/cancel
    and the in-process BuyerBot._handle_cancel already use), the first thing
    this checkout_id-taking path adds that get_order/get_checkout never had:
    an ownership check. checkout_id is caller-supplied explicitly rather
    than "find my latest" here — deciding WHICH order across however many
    merchants a federated buyer reaches is the buyer agent's job (it can see
    every merchant it's enrolled with; this tool only ever sees one)."""
    try:
        _require_scope(token_scopes, "checkout:initiate")
        from openstore.core.api import get_checkout
        from openstore.psp.razorpay_driver import RazorpayError, cancel_checkout_by_id

        checkout = get_checkout(
            session=session, trace_id=trace_id, client_id=client_id, checkout_id=checkout_id
        )
        if checkout is None:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_found",
                    "message": f"Checkout {checkout_id} not found",
                },
            )
        if checkout.chat_platform != chat_platform or checkout.chat_user_id != chat_user_id:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_owned",
                    "message": "This checkout does not belong to the calling identity",
                },
            )
        try:
            result = cancel_checkout_by_id(
                config=config,
                session=session,
                trace_id=trace_id,
                client_id=client_id,
                checkout=checkout,
            )
        except RazorpayError as e:
            return MCPToolResult(
                success=False, error={"reason_code": e.error_code, "message": e.message}
            )
        return MCPToolResult(success=True, data=result)
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def set_order_message(
    config: Settings,
    session: Any,
    checkout_id: str,
    chat_platform: str,
    chat_user_id: str,
    discord_message_id: str,
    token_scopes: list[str],
) -> MCPToolResult:
    """MCP tool: set_order_message (S16 / Q-032a). Records the Discord message
    carrying the pay embed so the webhook worker / hold loop can edit it in
    place. Same ownership check as cancel_order (chat_platform + chat_user_id
    must match); never overwrites an existing id (first writer wins)."""
    try:
        _require_scope(token_scopes, "checkout:initiate")
        from sqlmodel import select

        from openstore.models import Checkout

        checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        if checkout is None:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_found",
                    "message": f"Checkout {checkout_id} not found",
                },
            )
        if checkout.chat_platform != chat_platform or checkout.chat_user_id != chat_user_id:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "checkout.not_owned",
                    "message": "This checkout does not belong to the calling identity",
                },
            )
        if not checkout.discord_message_id:
            checkout.discord_message_id = str(discord_message_id)
            session.add(checkout)
            session.commit()
        return MCPToolResult(success=True, data={"checkout_id": checkout_id})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def get_audit_log(
    config: Settings,
    session: Any,
    trace_id: str,
    client_id: str,
    checkout_id: str | None = None,
    limit: int = 100,
) -> MCPToolResult:
    """MCP tool: get_audit_log. Returns audit log entries for a checkout."""
    try:
        from sqlmodel import select

        from openstore.models import AuditLog

        query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
        if checkout_id:
            query = query.where(AuditLog.resource_id == checkout_id)
        logs = list(session.exec(query).all())
        return MCPToolResult(
            success=True,
            data={
                "entries": [
                    {
                        "id": log.id,
                        "action": log.action,
                        "resource_type": log.resource_type,
                        "resource_id": log.resource_id,
                        "trace_id": log.trace_id,
                        "client_id": log.client_id,
                        "response_status": log.response_status,
                        "created_at": log.created_at.isoformat() if log.created_at else None,
                    }
                    for log in logs
                ],
                "count": len(logs),
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def webauthn_register_begin(
    config: Settings,
    session: Any,
    trace_id: str,
    user_id: str,
) -> MCPToolResult:
    """MCP tool: webauthn_register_begin. Starts WebAuthn registration ceremony."""
    try:
        from openstore.core.webauthn_rp import begin_registration

        result = begin_registration(
            config=config,
            user_handle=user_id,
            user_name=user_id,
            display_name=user_id,
        )
        return MCPToolResult(
            success=True,
            data={
                "challenge": result.get("challenge"),
                "rp": result.get("rp"),
                "user": result.get("user"),
                "pub_key_cred_params": result.get("pubKeyCredParams"),
                "timeout": result.get("timeout"),
                "exclude_credentials": result.get("excludeCredentials", []),
                "authenticator_selection": result.get("authenticatorSelection"),
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def webauthn_register_complete(
    config: Settings,
    session: Any,
    trace_id: str,
    user_id: str,
    credential: dict[str, Any],
) -> MCPToolResult:
    """MCP tool: webauthn_register_complete. Completes WebAuthn registration."""
    try:
        from openstore.core.webauthn_rp import complete_registration

        cred = complete_registration(
            session=session,
            config=config,
            user_handle=user_id,
            credential_id=credential.get("id", ""),
            client_data_json=credential.get("response", {}).get("clientDataJSON", ""),
            attestation_object=credential.get("response", {}).get("attestationObject", ""),
            challenge_b64url=credential.get("challenge", ""),
        )
        return MCPToolResult(success=True, data={"credential_id": cred.credential_id})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def webauthn_begin_assertion(
    config: Settings,
    session: Any,
    trace_id: str,
    user_id: str,
    challenge_binding: dict[str, Any] | None = None,
) -> MCPToolResult:
    """MCP tool: webauthn_begin_assertion. Starts assertion ceremony."""
    try:
        from openstore.core.webauthn_rp import begin_assertion

        result = begin_assertion(config=config, user_handle=user_id, binding=challenge_binding)
        return MCPToolResult(
            success=True,
            data={
                "challenge": result.get("challenge"),
                "rp_id": result.get("rpId"),
                "timeout": result.get("timeout"),
                "allow_credentials": result.get("allowCredentials", []),
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def webauthn_complete_assertion(
    config: Settings,
    session: Any,
    trace_id: str,
    user_id: str,
    credential_id: str,
    assertion: dict[str, Any],
) -> MCPToolResult:
    """MCP tool: webauthn_complete_assertion. Completes assertion ceremony."""
    try:
        from openstore.core.webauthn_rp import complete_assertion

        verified, sign_count = complete_assertion(
            session=session,
            config=config,
            user_handle=user_id,
            credential_id=credential_id,
            client_data_json=assertion.get("clientDataJSON", ""),
            authenticator_data=assertion.get("authenticatorData", ""),
            signature=assertion.get("signature", ""),
            challenge_b64url=assertion.get("challenge", ""),
            binding=assertion.get("binding"),
        )
        return MCPToolResult(success=True, data={"verified": verified, "sign_count": sign_count})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def list_campaigns(
    config: Settings,
    session: Any,
    merchant_id: str,
) -> MCPToolResult:
    """MCP tool: list_campaigns. Returns ACTIVE, in-window campaigns.

    The window filter matters: it used to return every ACTIVE row regardless of
    starts_at/ends_at, so a buyer agent could discover an offer that compiler
    check 12 then rejected with policy.campaign_outside_window. INV-13 says a
    buyer MUST treat an out-of-window offer as non-existent — so it is not
    shown, exactly as the signed feed already does.
    """
    try:
        from datetime import UTC, datetime

        from sqlmodel import select

        from openstore.models import Campaign, CampaignState

        now = datetime.now(UTC).replace(tzinfo=None)
        campaigns = [
            c
            for c in session.exec(
                select(Campaign).where(
                    Campaign.merchant_id == merchant_id,
                    Campaign.state == CampaignState.ACTIVE,
                )
            ).all()
            if c.starts_at <= now < c.ends_at
        ]
        return MCPToolResult(
            success=True,
            data={
                "campaigns": [
                    {
                        "campaign_id": c.id,
                        "title": c.title,
                        "discount_bps": c.discount_bps,
                        "applies_to_skus": c.applies_to_skus,
                        "starts_at": c.starts_at.isoformat() if c.starts_at else None,
                        "ends_at": c.ends_at.isoformat() if c.ends_at else None,
                        "state": c.state.value,
                    }
                    for c in campaigns
                ],
                "count": len(campaigns),
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def get_campaign(
    config: Settings,
    session: Any,
    campaign_id: str,
) -> MCPToolResult:
    """MCP tool: get_campaign. Returns a single campaign by ID."""
    try:
        from sqlmodel import select

        from openstore.models import Campaign

        campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
        if campaign is None:
            return MCPToolResult(
                success=False,
                error={
                    "reason_code": "campaign.not_found",
                    "message": f"Campaign {campaign_id} not found",
                },
            )
        return MCPToolResult(
            success=True,
            data={
                "campaign": {
                    "campaign_id": campaign.id,
                    "merchant_id": campaign.merchant_id,
                    "title": campaign.title,
                    "discount_bps": campaign.discount_bps,
                    "applies_to_skus": campaign.applies_to_skus,
                    "starts_at": campaign.starts_at.isoformat() if campaign.starts_at else None,
                    "ends_at": campaign.ends_at.isoformat() if campaign.ends_at else None,
                    "state": campaign.state.value,
                    "rationale": campaign.rationale,
                }
            },
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def resolve_policy(
    config: Settings,
    session: Any,
    user_id: str,
    token_scopes: list[str],
) -> MCPToolResult:
    """MCP tool: resolve_policy. Thin adapter over core.handoff.require_active_policy
    (S11: a remote buyer process resolving its per-merchant intent policy over
    MCP instead of importing core/handoff.py directly). Returns exactly the 5
    fields buyer_agent._load_policy_fields reads, plus policy_id. No active
    signed policy → authority.policy_unsigned (R0.5), the same closed-set
    reason_code require_active_policy already raises."""
    _require_scope(token_scopes, "catalog:read")
    try:
        from openstore.core.handoff import HandoffError, require_active_policy

        policy = require_active_policy(session, user_id)
        return MCPToolResult(
            success=True,
            data={
                "policy_id": policy.id,
                "allowed_tags": policy.allowed_tags,
                "tag_mode": policy.tag_mode,
                "blocked_skus": policy.blocked_skus,
                "max_spend_per_tx_minor": policy.max_spend_per_tx_minor,
                "policy_hash": policy.policy_hash,
            },
        )
    except HandoffError as e:
        # HandoffError doesn't subclass CommerceError (same reason the
        # RazorpayError branch exists in checkout_initiate above) — it needs
        # its own except clause or its authority.* reason_code flattens to
        # "internal_error" in the generic branch below.
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def create_cart_handoff(
    config: Settings,
    session: Any,
    merchant_id: str,
    chat_platform: str,
    chat_user_id: str,
    chat_channel_id: str,
    request_text: str,
    cart: list[dict[str, Any]],
    cart_hash: str,
    policy_id: str,
    token_scopes: list[str],
    resume_url: str | None = None,
) -> MCPToolResult:
    """MCP tool: create_cart_handoff (S16 / Q-033). Parks a pending cart for a
    per-cart passkey tap: verifies the cart hash server-side (R0.8), mints a
    kind=CART handoff carrying {cart_id, cart, cart_hash, policy_id}, and
    returns the token + cart_id so the caller can render
    /intent/studio?token=.... Caller commits (create_handoff only flushes)."""
    _require_scope(token_scopes, "catalog:read")
    try:
        import secrets as _secrets

        from openstore.agents.buyer_agent import compute_cart_hash as _cart_hash
        from openstore.core.handoff import create_handoff
        from openstore.models import HandoffKind

        if _cart_hash(cart) != cart_hash:
            return MCPToolResult(
                success=False,
                error={"reason_code": "assertion_required", "message": "cart hash mismatch"},
            )
        cart_id = f"cart_{_secrets.token_hex(8)}"
        handoff = create_handoff(
            session,
            kind=HandoffKind.CART,
            merchant_id=merchant_id,
            chat_platform=chat_platform,
            chat_user_id=chat_user_id,
            chat_channel_id=chat_channel_id,
            request_text=request_text,
            resume_url=resume_url,
            cart_payload={
                "cart_id": cart_id,
                "cart": cart,
                "cart_hash": cart_hash,
                "policy_id": policy_id,
            },
        )
        session.commit()
        return MCPToolResult(success=True, data={"token": handoff.token, "cart_id": cart_id})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


def create_policy_handoff(
    config: Settings,
    session: Any,
    merchant_id: str,
    chat_platform: str,
    chat_user_id: str,
    chat_channel_id: str,
    request_text: str,
    token_scopes: list[str],
    resume_url: str | None = None,
) -> MCPToolResult:
    """MCP tool: create_policy_handoff. Thin adapter over core.handoff.create_handoff
    (kind=POLICY) — bootstraps a signing link for a buyer with no active
    policy, mirroring what buyer_agent._handle_shop does directly today when
    buyer and merchant share a process. Caller commits (create_handoff only
    flushes).

    resume_url (S12 step 8, optional): a federated buyer's own
    /internal/signing-complete endpoint. Stored verbatim, never validated as
    a proof of anything — it just tells studio.py where to POST a best-effort
    ping once this handoff is consumed (surfaces/buyer_internal.py does the
    actual re-verification on receipt). Absent = today's behavior exactly."""
    _require_scope(token_scopes, "catalog:read")
    try:
        from openstore.core.handoff import create_handoff
        from openstore.models import HandoffKind

        handoff = create_handoff(
            session,
            kind=HandoffKind.POLICY,
            merchant_id=merchant_id,
            chat_platform=chat_platform,
            chat_user_id=chat_user_id,
            chat_channel_id=chat_channel_id,
            request_text=request_text,
            resume_url=resume_url,
        )
        session.commit()
        return MCPToolResult(success=True, data={"token": handoff.token})
    except CommerceError as e:
        return MCPToolResult(
            success=False, error={"reason_code": e.reason_code, "message": e.message}
        )
    except Exception as e:
        return MCPToolResult(
            success=False, error={"reason_code": "internal_error", "message": str(e)}
        )


TOOL_NAMES = frozenset(
    {
        "search_products",
        "get_product",
        "create_cart",
        "update_cart",
        "checkout_initiate",
        "checkout_confirm",
        "get_order",
        "get_audit_log",
        "webauthn_register_begin",
        "webauthn_register_complete",
        "webauthn_begin_assertion",
        "webauthn_complete_assertion",
        "list_campaigns",
        "get_campaign",
        "resolve_policy",
        "create_policy_handoff",
        "create_cart_handoff",
        "list_orders",
        "cancel_order",
        "set_order_message",
    }
)


def handle_mcp_request(
    config: Settings,
    session: Any,
    tool_name: str,
    arguments: dict[str, Any],
    token_scopes: list[str],
    trace_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute one MCP tool (transport-agnostic core).

    Dispatched from TOOL_NAMES (closed set). Unknown tool name returns a
    failure payload with auth.unknown_tool (a *business* rejection carried in
    tools/call isError content — never a JSON-RPC envelope error).
    Scope gates raise CommerceError to the caller: the wire handler maps them
    to isError content; InProcessMCPClient lets them propagate (R0.5).
    """
    if tool_name not in TOOL_NAMES:
        return {
            "success": False,
            "error": {
                "reason_code": "auth.unknown_tool",
                "message": f"Unknown MCP tool: {tool_name}. Known tools: {sorted(TOOL_NAMES)}",
            },
        }

    tid = trace_id or f"mcp_{secrets.token_hex(8)}"
    cid = client_id or "anonymous"

    if tool_name == "search_products":
        result = search_products(
            config,
            query=arguments.get("query", ""),
            tags=arguments.get("tags"),
            limit=arguments.get("limit", 20),
        )
    elif tool_name == "get_product":
        result = get_product(config, sku=arguments["sku"])
    elif tool_name == "create_cart":
        result = create_cart(
            config=config,
            session=session,
            client_id=cid,
            trace_id=tid,
            merchant_id=arguments.get("merchant_id", ""),
            items=arguments.get("items", []),
            policy_id=arguments.get("policy_id", ""),
            cart_hash=arguments.get("cart_hash", ""),
            cart_version=arguments.get("cart_version", 1),
            token_scopes=token_scopes,
            webauthn_assertion=arguments.get("webauthn_assertion"),
        )
    elif tool_name == "update_cart":
        result = update_cart(
            config=config,
            session=session,
            client_id=cid,
            trace_id=tid,
            merchant_id=arguments.get("merchant_id", ""),
            checkout_id=arguments.get("checkout_id", ""),
            items=arguments.get("items", []),
            policy_id=arguments.get("policy_id", ""),
            cart_hash=arguments.get("cart_hash", ""),
            cart_version=arguments.get("cart_version", 1),
            token_scopes=token_scopes,
            webauthn_assertion=arguments.get("webauthn_assertion"),
        )
    elif tool_name == "checkout_initiate":
        result = checkout_initiate(
            config=config,
            session=session,
            client_id=cid,
            trace_id=tid,
            checkout_id=arguments.get("checkout_id", ""),
            token_scopes=token_scopes,
            chat_platform=arguments.get("chat_platform"),
            chat_user_id=arguments.get("chat_user_id"),
            chat_channel_id=arguments.get("chat_channel_id"),
            request_text=arguments.get("request_text"),
        )
    elif tool_name == "checkout_confirm":
        result = checkout_confirm(
            config=config,
            session=session,
            client_id=cid,
            trace_id=tid,
            checkout_id=arguments.get("checkout_id", ""),
            token_scopes=token_scopes,
            webauthn_assertion=arguments.get("webauthn_assertion"),
        )
    elif tool_name == "get_order":
        result = get_order(
            config=config,
            session=session,
            trace_id=tid,
            client_id=cid,
            checkout_id=arguments.get("checkout_id", ""),
        )
    elif tool_name == "get_audit_log":
        result = get_audit_log(
            config=config,
            session=session,
            trace_id=tid,
            client_id=cid,
            checkout_id=arguments.get("checkout_id"),
            limit=arguments.get("limit", 100),
        )
    elif tool_name == "webauthn_register_begin":
        result = webauthn_register_begin(
            config=config,
            session=session,
            trace_id=tid,
            user_id=arguments.get("user_id", ""),
        )
    elif tool_name == "webauthn_register_complete":
        result = webauthn_register_complete(
            config=config,
            session=session,
            trace_id=tid,
            user_id=arguments.get("user_id", ""),
            credential=arguments.get("credential", {}),
        )
    elif tool_name == "webauthn_begin_assertion":
        result = webauthn_begin_assertion(
            config=config,
            session=session,
            trace_id=tid,
            user_id=arguments.get("user_id", ""),
            challenge_binding=arguments.get("challenge_binding"),
        )
    elif tool_name == "webauthn_complete_assertion":
        result = webauthn_complete_assertion(
            config=config,
            session=session,
            trace_id=tid,
            user_id=arguments.get("user_id", ""),
            credential_id=arguments.get("credential_id", ""),
            assertion=arguments.get("assertion", {}),
        )
    elif tool_name == "list_campaigns":
        # merchant_id comes from THIS sidecar's config, never from the caller's
        # arguments (R0.8, and DECISION-015 makes each process single-tenant).
        # Taking it from the payload let an agent scope the query to a merchant
        # it does not represent, and an omitted argument silently returned [].
        result = list_campaigns(
            config=config,
            session=session,
            merchant_id=_merchant_id(config),
        )
    elif tool_name == "get_campaign":
        result = get_campaign(
            config=config,
            session=session,
            campaign_id=arguments.get("campaign_id", ""),
        )
    elif tool_name == "resolve_policy":
        result = resolve_policy(
            config=config,
            session=session,
            user_id=arguments.get("user_id", ""),
            token_scopes=token_scopes,
        )
    elif tool_name == "create_policy_handoff":
        result = create_policy_handoff(
            config=config,
            session=session,
            merchant_id=arguments.get("merchant_id", ""),
            chat_platform=arguments.get("chat_platform", ""),
            chat_user_id=arguments.get("chat_user_id", ""),
            chat_channel_id=arguments.get("chat_channel_id", ""),
            request_text=arguments.get("request_text", ""),
            token_scopes=token_scopes,
            resume_url=arguments.get("resume_url"),
        )
    elif tool_name == "create_cart_handoff":
        result = create_cart_handoff(
            config=config,
            session=session,
            merchant_id=arguments.get("merchant_id", ""),
            chat_platform=arguments.get("chat_platform", ""),
            chat_user_id=arguments.get("chat_user_id", ""),
            chat_channel_id=arguments.get("chat_channel_id", ""),
            request_text=arguments.get("request_text", ""),
            cart=arguments.get("cart", []),
            cart_hash=arguments.get("cart_hash", ""),
            policy_id=arguments.get("policy_id", ""),
            token_scopes=token_scopes,
            resume_url=arguments.get("resume_url"),
        )
    elif tool_name == "list_orders":
        result = list_orders(
            config=config,
            session=session,
            chat_platform=arguments.get("chat_platform", ""),
            chat_user_id=arguments.get("chat_user_id", ""),
            limit=arguments.get("limit", 5),
        )
    elif tool_name == "cancel_order":
        result = cancel_order(
            config=config,
            session=session,
            trace_id=tid,
            client_id=cid,
            checkout_id=arguments.get("checkout_id", ""),
            chat_platform=arguments.get("chat_platform", ""),
            chat_user_id=arguments.get("chat_user_id", ""),
            token_scopes=token_scopes,
        )
    elif tool_name == "set_order_message":
        result = set_order_message(
            config=config,
            session=session,
            checkout_id=arguments.get("checkout_id", ""),
            chat_platform=arguments.get("chat_platform", "discord"),
            chat_user_id=arguments.get("chat_user_id", ""),
            discord_message_id=str(arguments.get("discord_message_id", "")),
            token_scopes=token_scopes,
        )
    else:
        return {
            "success": False,
            "error": {
                "reason_code": "auth.unknown_tool",
                "message": f"Unreachable tool: {tool_name}",
            },
        }

    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
    }


# ---------------------------------------------------------------------------
# MCP wire protocol (JSON-RPC 2.0, MCP 2025-06-18) — DECISION-036.
# Hard cutover: POST /agent/mcp accepts ONLY this envelope. handle_mcp_request
# above stays the tool-execution core (tool semantics unchanged, R0.9); the
# wire handler below maps the lifecycle methods onto it.
# ---------------------------------------------------------------------------

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "openstore"


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_OBJ = {"type": "object"}
_ARR_STR = {"type": "array", "items": {"type": "string"}}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_products": {
        "description": "Search the merchant catalog by name/description and tags.",
        "inputSchema": _obj(
            {"query": _STR, "tags": _ARR_STR, "limit": _INT},
            [],
        ),
    },
    "get_product": {
        "description": "Fetch one catalog item by SKU.",
        "inputSchema": _obj({"sku": _STR}, ["sku"]),
    },
    "create_cart": {
        "description": "Compile a cart against the signed policy; returns ALLOW/DENY plus checkout_id.",
        "inputSchema": _obj(
            {
                "merchant_id": _STR,
                "items": {"type": "array", "items": _OBJ},
                "policy_id": _STR,
                "cart_hash": _STR,
                "cart_version": _INT,
                "webauthn_assertion": _OBJ,
            },
            ["merchant_id", "items", "policy_id", "cart_hash"],
        ),
    },
    "update_cart": {
        "description": "Replace a HELD checkout's cart (cancels the old hold, creates a new one).",
        "inputSchema": _obj(
            {
                "merchant_id": _STR,
                "checkout_id": _STR,
                "items": {"type": "array", "items": _OBJ},
                "policy_id": _STR,
                "cart_hash": _STR,
                "cart_version": _INT,
                "webauthn_assertion": _OBJ,
            },
            ["merchant_id", "checkout_id", "items", "policy_id", "cart_hash"],
        ),
    },
    "checkout_initiate": {
        "description": "Create the Razorpay payment link for a compiled checkout.",
        "inputSchema": _obj(
            {
                "checkout_id": _STR,
                "chat_platform": _STR,
                "chat_user_id": _STR,
                "chat_channel_id": _STR,
                "request_text": _STR,
            },
            ["checkout_id"],
        ),
    },
    "checkout_confirm": {
        "description": "Confirm a checkout after its payment link is paid.",
        "inputSchema": _obj(
            {"checkout_id": _STR, "webauthn_assertion": _OBJ},
            ["checkout_id"],
        ),
    },
    "get_order": {
        "description": "Read the current state of one checkout.",
        "inputSchema": _obj({"checkout_id": _STR}, ["checkout_id"]),
    },
    "get_audit_log": {
        "description": "Read audit log entries, optionally filtered by checkout.",
        "inputSchema": _obj({"checkout_id": _STR, "limit": _INT}, []),
    },
    "webauthn_register_begin": {
        "description": "Start a WebAuthn registration ceremony for a user handle.",
        "inputSchema": _obj({"user_id": _STR}, ["user_id"]),
    },
    "webauthn_register_complete": {
        "description": "Complete a WebAuthn registration ceremony.",
        "inputSchema": _obj({"user_id": _STR, "credential": _OBJ}, ["user_id", "credential"]),
    },
    "webauthn_begin_assertion": {
        "description": "Start a WebAuthn assertion ceremony, optionally bound to a cart/campaign/amendment.",
        "inputSchema": _obj({"user_id": _STR, "challenge_binding": _OBJ}, ["user_id"]),
    },
    "webauthn_complete_assertion": {
        "description": "Complete a WebAuthn assertion ceremony.",
        "inputSchema": _obj(
            {"user_id": _STR, "credential_id": _STR, "assertion": _OBJ},
            ["user_id", "credential_id", "assertion"],
        ),
    },
    "list_campaigns": {
        "description": "List ACTIVE, in-window campaigns for this merchant.",
        "inputSchema": _obj({}, []),
    },
    "get_campaign": {
        "description": "Fetch one campaign by ID.",
        "inputSchema": _obj({"campaign_id": _STR}, ["campaign_id"]),
    },
    "resolve_policy": {
        "description": "Resolve the active signed policy for a user handle.",
        "inputSchema": _obj({"user_id": _STR}, ["user_id"]),
    },
    "create_policy_handoff": {
        "description": "Mint a signing link for a buyer with no active policy.",
        "inputSchema": _obj(
            {
                "merchant_id": _STR,
                "chat_platform": _STR,
                "chat_user_id": _STR,
                "chat_channel_id": _STR,
                "request_text": _STR,
                "resume_url": _STR,
            },
            ["merchant_id", "chat_platform", "chat_user_id", "chat_channel_id", "request_text"],
        ),
    },
    "create_cart_handoff": {
        "description": "Park a pending cart for a per-cart passkey tap.",
        "inputSchema": _obj(
            {
                "merchant_id": _STR,
                "chat_platform": _STR,
                "chat_user_id": _STR,
                "chat_channel_id": _STR,
                "request_text": _STR,
                "cart": {"type": "array", "items": _OBJ},
                "cart_hash": _STR,
                "policy_id": _STR,
                "resume_url": _STR,
            },
            [
                "merchant_id",
                "chat_platform",
                "chat_user_id",
                "chat_channel_id",
                "request_text",
                "cart",
                "cart_hash",
                "policy_id",
            ],
        ),
    },
    "list_orders": {
        "description": "List recent checkouts for one chat identity, newest first.",
        "inputSchema": _obj(
            {"chat_platform": _STR, "chat_user_id": _STR, "limit": _INT},
            ["chat_platform", "chat_user_id"],
        ),
    },
    "cancel_order": {
        "description": "Cancel one checkout owned by the calling chat identity.",
        "inputSchema": _obj(
            {"checkout_id": _STR, "chat_platform": _STR, "chat_user_id": _STR},
            ["checkout_id", "chat_platform", "chat_user_id"],
        ),
    },
    "set_order_message": {
        "description": "Record the chat message carrying a pay embed (first writer wins).",
        "inputSchema": _obj(
            {
                "checkout_id": _STR,
                "chat_platform": _STR,
                "chat_user_id": _STR,
                "discord_message_id": _STR,
            },
            ["checkout_id", "chat_platform", "chat_user_id", "discord_message_id"],
        ),
    },
}

if set(TOOL_SCHEMAS) != set(TOOL_NAMES):
    raise RuntimeError(
        "TOOL_SCHEMAS drifted from TOOL_NAMES: "
        f"missing={sorted(set(TOOL_NAMES) - set(TOOL_SCHEMAS))} "
        f"extra={sorted(set(TOOL_SCHEMAS) - set(TOOL_NAMES))}"
    )


def _wire_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _wire_err(
    req_id: Any, code: int, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_text_envelope(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Map the {success,data,error} tool payload onto MCP content.

    Business rejections (DENY, unknown tool, scope gates) ride isError
    content with their closed-set reason codes — never envelope errors."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(tool_result, sort_keys=True),
            }
        ],
        "isError": not tool_result.get("success", False),
    }


def handle_mcp_wire_request(
    config: Settings,
    session: Any,
    body: Any,
    token_scopes: list[str],
    client_id: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Handle one POST /agent/mcp body. Returns (http_status, response).

    A None response means a JSON-RPC notification (no id): the caller answers
    HTTP 202 with an empty body. The legacy {"tool","arguments"} shape is
    rejected -32600 and never executed (DECISION-036 hard cutover).
    """
    if isinstance(body, list):
        return 400, _wire_err(None, -32600, "Invalid Request: batch requests are not supported")
    if not isinstance(body, dict):
        return 400, _wire_err(None, -32600, "Invalid Request: body must be a JSON-RPC object")
    if body.get("jsonrpc") != "2.0":
        legacy_id = body.get("id")
        return (
            400,
            _wire_err(
                legacy_id,
                -32600,
                "Invalid Request: MCP wire protocol required "
                "(JSON-RPC 2.0 with method initialize/tools/list/tools/call)",
            ),
        )

    method = body.get("method")
    req_id = body.get("id")
    is_notification = "id" not in body
    if req_id is not None and (isinstance(req_id, bool) or not isinstance(req_id, str | int)):
        return 400, _wire_err(None, -32600, "Invalid Request: id must be a string or integer")

    params = body.get("params", {})
    if not isinstance(params, dict):
        if is_notification:
            return 202, None
        return 200, _wire_err(req_id, -32602, "Invalid params: params must be an object")

    if not isinstance(method, str):
        if is_notification:
            return 202, None
        return 200, _wire_err(req_id, -32600, "Invalid Request: method must be a string")

    if method == "initialize":
        if is_notification:
            return 202, None
        return 200, _wire_ok(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": MCP_SERVER_NAME, "version": _server_version},
            },
        )

    if method == "ping":
        if is_notification:
            return 202, None
        return 200, _wire_ok(req_id, {})

    if method.startswith("notifications/"):
        return 202, None

    if method == "tools/list":
        if is_notification:
            return 202, None
        return 200, _wire_ok(
            req_id,
            {
                "tools": [
                    {
                        "name": name,
                        "description": TOOL_SCHEMAS[name]["description"],
                        "inputSchema": TOOL_SCHEMAS[name]["inputSchema"],
                    }
                    for name in sorted(TOOL_NAMES)
                ],
            },
        )

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            if is_notification:
                return 202, None
            return (
                200,
                _wire_err(
                    req_id,
                    -32602,
                    "Invalid params: tools/call requires {name: string, arguments: object}",
                ),
            )
        schema = TOOL_SCHEMAS.get(name)
        if schema is not None:
            missing = [
                field
                for field in schema["inputSchema"].get("required", [])
                if field not in arguments
            ]
            if missing:
                if is_notification:
                    return 202, None
                return (
                    200,
                    _wire_err(
                        req_id,
                        -32602,
                        f"Invalid params: missing required fields for {name}: "
                        + ", ".join(sorted(missing)),
                    ),
                )
        try:
            tool_result = handle_mcp_request(
                config=config,
                session=session,
                tool_name=name,
                arguments=arguments,
                token_scopes=token_scopes,
                trace_id=None,
                client_id=client_id or "anonymous",
            )
        except CommerceError as e:
            # Scope gates sit outside the per-tool try blocks by design (fail
            # loud, R0.5) — on the wire they become isError content, not a 500.
            tool_result = {
                "success": False,
                "error": {"reason_code": e.reason_code, "message": e.message},
            }
        except Exception as e:
            if is_notification:
                return 202, None
            return (
                500,
                _wire_err(
                    req_id,
                    -32603,
                    "Internal error: tool execution failed",
                    {"message": str(e)},
                ),
            )
        if is_notification:
            return 202, None
        return 200, _wire_ok(req_id, _tool_text_envelope(tool_result))

    if is_notification:
        return 202, None
    return 200, _wire_err(req_id, -32601, f"Method not found: {method}")
