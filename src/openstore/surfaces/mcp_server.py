# OpenStore surfaces — MCP server (14 tools, closed set per PRD §6 / REGISTRY.json)
# Per PRD S6.3 — thin adapters over core/api.py + WebAuthn RP.

from __future__ import annotations

import secrets
from typing import Any

from openstore.config import Settings
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
    def __init__(self, success: bool, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None):
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
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


def get_product(
    config: Settings,
    sku: str,
) -> MCPToolResult:
    """MCP tool: get_product. Returns a single catalog item by SKU."""
    try:
        from openstore.surfaces.catalog import get_catalog_item
        item = get_catalog_item(config, sku)
        if item is None:
            return MCPToolResult(success=False, error={"reason_code": "catalog.sku_not_found", "message": f"SKU {sku} not found"})
        return MCPToolResult(success=True, data={"item": item})
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
        return MCPToolResult(success=True, data={
            "allowed": result.allowed,
            "reason_code": result.reason_code,
            "aal_level": result.aal_level,
            "effective_amount_minor": result.effective_amount_minor,
            "transcript": result.transcript,
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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

        old = session.exec(
            select(Checkout).where(Checkout.id == checkout_id)
        ).first()
        if old and old.state == OrderState.HELD:
            cancel_hold(
                session=session,
                checkout_id=old.id,
                trace_id=trace_id,
                client_id=client_id,
                reason="Replaced by agent update_cart",
            )
        return create_cart(
            config=config, session=session, client_id=client_id, trace_id=trace_id,
            merchant_id=merchant_id, items=items, policy_id=policy_id,
            cart_hash=cart_hash, cart_version=cart_version, token_scopes=token_scopes,
            webauthn_assertion=webauthn_assertion,
        )
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


def checkout_initiate(
    config: Settings,
    session: Any,
    client_id: str,
    trace_id: str,
    checkout_id: str,
    token_scopes: list[str],
) -> MCPToolResult:
    """MCP tool: checkout_initiate. Creates a Razorpay payment link for the checkout."""
    _require_scope(token_scopes, "checkout:initiate")
    try:
        from sqlmodel import select

        from openstore.models import Checkout
        from openstore.psp.razorpay_driver import create_payment_link

        checkout = session.exec(
            select(Checkout).where(Checkout.id == checkout_id)
        ).first()
        if checkout is None:
            return MCPToolResult(success=False, error={"reason_code": "checkout.not_found", "message": f"Checkout {checkout_id} not found"})

        checkout = create_payment_link(
            config=config, session=session, trace_id=trace_id, client_id=client_id,
            checkout_id=checkout_id, amount_minor=checkout.amount_minor, currency=checkout.currency,
        )
        return MCPToolResult(success=True, data={
            "checkout_id": checkout.id,
            "payment_link_id": checkout.psp_payment_link_id,
            "short_url": checkout.short_url,
            "cancel_token": checkout.cancel_token,
            "amount_minor": checkout.amount_minor,
            "currency": checkout.currency,
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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

        stored = session.exec(
            select(Checkout).where(Checkout.id == checkout_id)
        ).first()
        psp_order_id = stored.psp_order_id if stored else checkout_id
        psp_payment_link_id = stored.psp_payment_link_id if stored else ""

        checkout = confirm_checkout(
            config=config, session=session, trace_id=trace_id, client_id=client_id,
            checkout_id=checkout_id, psp_order_id=psp_order_id,
            psp_payment_link_id=psp_payment_link_id, webauthn_assertion=webauthn_assertion,
        )
        return MCPToolResult(success=True, data={
            "checkout_id": checkout.id,
            "state": checkout.state.value,
            "cancel_token": checkout.cancel_token,
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
        checkout = get_checkout(session=session, trace_id=trace_id, client_id=client_id, checkout_id=checkout_id)
        if checkout is None:
            return MCPToolResult(success=False, error={"reason_code": "checkout.not_found", "message": f"Checkout {checkout_id} not found"})
        return MCPToolResult(success=True, data={
            "checkout_id": checkout.id,
            "state": checkout.state.value,
            "amount_minor": checkout.amount_minor,
            "currency": checkout.currency,
            "aal_level": checkout.aal_level,
            "cart_hash": checkout.cart_hash,
            "created_at": checkout.created_at.isoformat() if checkout.created_at else None,
            "expires_at": checkout.expires_at.isoformat() if checkout.expires_at else None,
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
        return MCPToolResult(success=True, data={
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
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
            config=config, user_handle=user_id, user_name=user_id, display_name=user_id,
        )
        return MCPToolResult(success=True, data={
            "challenge": result.get("challenge"),
            "rp": result.get("rp"),
            "user": result.get("user"),
            "pub_key_cred_params": result.get("pubKeyCredParams"),
            "timeout": result.get("timeout"),
            "exclude_credentials": result.get("excludeCredentials", []),
            "authenticator_selection": result.get("authenticatorSelection"),
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
            session=session, config=config, user_handle=user_id,
            credential_id=credential.get("id", ""),
            client_data_json=credential.get("response", {}).get("clientDataJSON", ""),
            attestation_object=credential.get("response", {}).get("attestationObject", ""),
            challenge_b64url=credential.get("challenge", ""),
        )
        return MCPToolResult(success=True, data={"credential_id": cred.credential_id})
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
        return MCPToolResult(success=True, data={
            "challenge": result.get("challenge"),
            "rp_id": result.get("rpId"),
            "timeout": result.get("timeout"),
            "allow_credentials": result.get("allowCredentials", []),
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
            session=session, config=config, user_handle=user_id,
            credential_id=credential_id,
            client_data_json=assertion.get("clientDataJSON", ""),
            authenticator_data=assertion.get("authenticatorData", ""),
            signature=assertion.get("signature", ""),
            challenge_b64url=assertion.get("challenge", ""),
            binding=assertion.get("binding"),
        )
        return MCPToolResult(success=True, data={"verified": verified, "sign_count": sign_count})
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


def list_campaigns(
    config: Settings,
    session: Any,
    merchant_id: str,
) -> MCPToolResult:
    """MCP tool: list_campaigns. Returns ACTIVE campaigns for the merchant."""
    try:
        from sqlmodel import select

        from openstore.models import Campaign, CampaignState
        campaigns = list(session.exec(
            select(Campaign).where(
                Campaign.merchant_id == merchant_id,
                Campaign.state == CampaignState.ACTIVE,
            )
        ).all())
        return MCPToolResult(success=True, data={
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
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


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
            return MCPToolResult(success=False, error={"reason_code": "campaign.not_found", "message": f"Campaign {campaign_id} not found"})
        return MCPToolResult(success=True, data={
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
        })
    except CommerceError as e:
        return MCPToolResult(success=False, error={"reason_code": e.reason_code, "message": e.message})
    except Exception as e:
        return MCPToolResult(success=False, error={"reason_code": "internal_error", "message": str(e)})


TOOL_NAMES = frozenset({
    "search_products", "get_product", "create_cart", "update_cart",
    "checkout_initiate", "checkout_confirm", "get_order", "get_audit_log",
    "webauthn_register_begin", "webauthn_register_complete",
    "webauthn_begin_assertion", "webauthn_complete_assertion",
    "list_campaigns", "get_campaign",
})


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
    Handle an MCP JSON-RPC request.
    Returns the tool result dict. Tools are dispatched from TOOL_NAMES (closed set).
    Unknown tool name → CommerceError with auth.unknown_tool.
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
        result = search_products(config, query=arguments.get("query", ""), tags=arguments.get("tags"), limit=arguments.get("limit", 20))
    elif tool_name == "get_product":
        result = get_product(config, sku=arguments["sku"])
    elif tool_name == "create_cart":
        result = create_cart(
            config=config, session=session, client_id=cid, trace_id=tid,
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
            config=config, session=session, client_id=cid, trace_id=tid,
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
            config=config, session=session, client_id=cid, trace_id=tid,
            checkout_id=arguments.get("checkout_id", ""),
            token_scopes=token_scopes,
        )
    elif tool_name == "checkout_confirm":
        result = checkout_confirm(
            config=config, session=session, client_id=cid, trace_id=tid,
            checkout_id=arguments.get("checkout_id", ""),
            token_scopes=token_scopes,
            webauthn_assertion=arguments.get("webauthn_assertion"),
        )
    elif tool_name == "get_order":
        result = get_order(
            config=config, session=session, trace_id=tid, client_id=cid,
            checkout_id=arguments.get("checkout_id", ""),
        )
    elif tool_name == "get_audit_log":
        result = get_audit_log(
            config=config, session=session, trace_id=tid, client_id=cid,
            checkout_id=arguments.get("checkout_id"),
            limit=arguments.get("limit", 100),
        )
    elif tool_name == "webauthn_register_begin":
        result = webauthn_register_begin(
            config=config, session=session, trace_id=tid,
            user_id=arguments.get("user_id", ""),
        )
    elif tool_name == "webauthn_register_complete":
        result = webauthn_register_complete(
            config=config, session=session, trace_id=tid,
            user_id=arguments.get("user_id", ""),
            credential=arguments.get("credential", {}),
        )
    elif tool_name == "webauthn_begin_assertion":
        result = webauthn_begin_assertion(
            config=config, session=session, trace_id=tid,
            user_id=arguments.get("user_id", ""),
            challenge_binding=arguments.get("challenge_binding"),
        )
    elif tool_name == "webauthn_complete_assertion":
        result = webauthn_complete_assertion(
            config=config, session=session, trace_id=tid,
            user_id=arguments.get("user_id", ""),
            credential_id=arguments.get("credential_id", ""),
            assertion=arguments.get("assertion", {}),
        )
    elif tool_name == "list_campaigns":
        result = list_campaigns(
            config=config, session=session,
            merchant_id=arguments.get("merchant_id", ""),
        )
    elif tool_name == "get_campaign":
        result = get_campaign(
            config=config, session=session,
            campaign_id=arguments.get("campaign_id", ""),
        )
    else:
        return {"success": False, "error": {"reason_code": "auth.unknown_tool", "message": f"Unreachable tool: {tool_name}"}}

    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
    }
