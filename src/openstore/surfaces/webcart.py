# OpenStore — web buyer surface (S22 / Q-042): browser-native checkout.
#
# Closes the loop Stage 19 left open: /chat goes from browse-only to buy
# without Discord or a buyer agent. Three ungated, same-origin routes:
#   POST /web/cart                     -> {state: signin|approval, signin_url|approval_url}
#   GET  /web/order/{checkout_id}      -> order status (buyer_key ownership check)
#   POST /web/order/{checkout_id}/cancel -> cancel (ownership check, cancel_token never exposed)
#
# Browser-safe auth design (Q-042): no cookies, no sessions, no secrets in the
# browser. Identity is chat_platform="web" + a buyer_key (token_urlsafe(16) in
# localStorage, validated, fits chat_user_id max 64). Authority is ALWAYS a
# passkey tap — this surface mints POLICY/CART handoffs and the existing
# /intent/studio ceremonies do the rest. It NEVER calls create_cart /
# checkout_initiate, so a stolen buyer_key alone authorizes nothing: without
# the authenticator there is no checkout, only a signin link.

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session

from openstore.agents.buyer_agent import compute_cart_hash
from openstore.config import Settings
from openstore.config import merchant_id as _merchant_id
from openstore.core.database import get_session
from openstore.models import Checkout, OrderState

_BUYER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_WEB_PLATFORM = "web"
_WEB_CHANNEL = "web"

_STATE_LABELS = {
    OrderState.HELD: "awaiting_payment",
    OrderState.PAID: "done",
    OrderState.RELEASED: "done",
    OrderState.CANCELLED: "terminal",
    OrderState.FAILED: "terminal",
    OrderState.REFUNDED: "terminal",
    OrderState.CREATED: "awaiting_payment",
}


def _check_buyer_key(buyer_key: str) -> str:
    """Fail loud (R0.5) on a malformed buyer identity — never coerce."""
    if not _BUYER_KEY_RE.match(buyer_key):
        raise HTTPException(
            status_code=422,
            detail={
                "reason_code": "checkout.invalid_state",
                "message": "buyer_key must match ^[A-Za-z0-9_-]{16,64}$",
            },
        )
    return buyer_key


def _buyer_handle(buyer_key: str) -> str:
    return f"{_WEB_PLATFORM}:{buyer_key}"


def _lookup_owned(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    buyer_key: str,
) -> Checkout:
    """get_checkout + ownership check (the cancel_order mirror, over HTTP:
    unknown id -> 404 checkout.not_found, wrong owner -> 403
    checkout.not_owned)."""
    from openstore.core.api import get_checkout

    checkout = get_checkout(
        session=session, trace_id=trace_id, client_id=client_id, checkout_id=checkout_id
    )
    if checkout is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "checkout.not_found",
                "message": f"Checkout {checkout_id} not found",
            },
        )
    if checkout.chat_platform != _WEB_PLATFORM or checkout.chat_user_id != buyer_key:
        raise HTTPException(
            status_code=403,
            detail={
                "reason_code": "checkout.not_owned",
                "message": "This checkout does not belong to the calling identity",
            },
        )
    return checkout


def webcart_router(
    config: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> APIRouter:
    """Build the web-buyer APIRouter (evidence.py router-factory shape)."""
    make_session = session_factory or (lambda: get_session(config))

    router = APIRouter()

    @router.post("/web/cart")
    async def web_cart(body: dict[str, Any]) -> dict[str, Any]:
        from openstore.core.handoff import HandoffError, create_handoff, require_active_policy
        from openstore.models import HandoffKind
        from openstore.surfaces.catalog import load_catalog

        buyer_key = _check_buyer_key(str(body.get("buyer_key", "")))
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise HTTPException(
                status_code=422,
                detail={"reason_code": "policy.qty_invalid", "message": "items: non-empty array required"},
            )
        request_text = body.get("request_text", "")
        if not isinstance(request_text, str) or len(request_text) > 4096:
            raise HTTPException(
                status_code=422,
                detail={
                    "reason_code": "policy.qty_invalid",
                    "message": "request_text: string <= 4096 chars",
                },
            )

        catalog = {item["sku"]: item for item in load_catalog(config)}
        lines: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise HTTPException(
                    status_code=422,
                    detail={"reason_code": "policy.qty_invalid", "message": "item: {sku, qty} required"},
                )
            sku = raw.get("sku")
            qty = raw.get("qty")
            if not isinstance(sku, str) or sku not in catalog:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "reason_code": "catalog.sku_not_found",
                        "message": f"SKU {sku} not found",
                    },
                )
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "reason_code": "policy.qty_invalid",
                        "message": f"qty for {sku}: positive integer required",
                    },
                )
            # Prices/tags stamped server-side from the catalog (R0.8) — the
            # client sends sku+qty only; anything else it sends is ignored.
            # No campaign_id attachment this slice (full price, Q-042).
            lines.append(
                {
                    "sku": sku,
                    "qty": qty,
                    "unit_minor": int(catalog[sku]["unit_minor"]),
                    "tags": list(catalog[sku].get("tags", [])),
                }
            )
        cart_hash = compute_cart_hash(lines)

        session = make_session()
        try:
            try:
                policy = require_active_policy(session, _buyer_handle(buyer_key))
            except HandoffError:
                handoff = create_handoff(
                    session,
                    kind=HandoffKind.POLICY,
                    merchant_id=_merchant_id(config),
                    chat_platform=_WEB_PLATFORM,
                    chat_user_id=buyer_key,
                    chat_channel_id=_WEB_CHANNEL,
                    request_text=request_text or f"web order ({len(lines)} line(s))",
                )
                session.commit()
                return {
                    "state": "signin",
                    "signin_url": f"/intent/studio?token={handoff.token}",
                }
            cart_id = f"cart_{secrets.token_hex(8)}"
            handoff = create_handoff(
                session,
                kind=HandoffKind.CART,
                merchant_id=_merchant_id(config),
                chat_platform=_WEB_PLATFORM,
                chat_user_id=buyer_key,
                chat_channel_id=_WEB_CHANNEL,
                request_text=request_text or f"web order ({len(lines)} line(s))",
                cart_payload={
                    "cart_id": cart_id,
                    "cart": lines,
                    "cart_hash": cart_hash,
                    "policy_id": policy.id,
                },
            )
            session.commit()
            return {
                "state": "approval",
                "approval_url": f"/intent/studio?token={handoff.token}",
                "cart_id": cart_id,
            }
        finally:
            session.close()

    @router.get("/web/order/{checkout_id}")
    async def web_order(
        checkout_id: str,
        buyer_key: str = Query(pattern=r"^[A-Za-z0-9_-]{16,64}$"),
    ) -> dict[str, Any]:
        session = make_session()
        try:
            checkout = _lookup_owned(
                session,
                trace_id=f"trace_web_{checkout_id[:8]}",
                client_id=f"web:{buyer_key}",
                checkout_id=checkout_id,
                buyer_key=buyer_key,
            )
            return {
                "checkout_id": checkout.id,
                "state": _STATE_LABELS.get(checkout.state, "terminal"),
                "order_state": checkout.state.value,
                "amount_minor": checkout.amount_minor,
                "currency": checkout.currency,
                "aal_level": checkout.aal_level,
                "short_url": checkout.short_url,
                "expires_at": checkout.expires_at.isoformat(),
                "evidence_url": (
                    f"/orders/{checkout.id}/evidence/view"
                    if checkout.poai_bundle is not None
                    else None
                ),
                "cancelable": checkout.state == OrderState.HELD,
            }
        finally:
            session.close()

    @router.post("/web/order/{checkout_id}/cancel")
    async def web_cancel(
        checkout_id: str,
        buyer_key: str = Query(pattern=r"^[A-Za-z0-9_-]{16,64}$"),
    ) -> dict[str, Any]:
        from openstore.psp.razorpay_driver import RazorpayError, cancel_checkout_by_id

        session = make_session()
        try:
            checkout = _lookup_owned(
                session,
                trace_id=f"trace_web_cancel_{checkout_id[:8]}",
                client_id=f"web:{buyer_key}",
                checkout_id=checkout_id,
                buyer_key=buyer_key,
            )
            try:
                result = cancel_checkout_by_id(
                    config=config,
                    session=session,
                    trace_id=f"trace_web_cancel_{checkout_id[:8]}",
                    client_id=f"web:{buyer_key}",
                    checkout=checkout,
                )
            except RazorpayError as e:
                raise HTTPException(
                    status_code=e.http_status or 400,
                    detail={"reason_code": e.error_code, "message": e.message},
                )
            session.commit()
            return {"cancelled": True, "checkout_id": checkout_id, "result": result}
        finally:
            session.close()

    return router
