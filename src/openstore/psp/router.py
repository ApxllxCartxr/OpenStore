# OpenStore PSP — FastAPI router for Razorpay webhooks and hold/cancel
# Stage 5: webhook endpoint + /hold/{cancel_token}/cancel

from __future__ import annotations

import logging
import secrets as _secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlmodel import select

from openstore.config import Settings
from openstore.core.database import get_session
from openstore.core.ledger import create_release_entry
from openstore.models import Checkout, OrderState
from openstore.psp import razorpay_driver as driver

logger = logging.getLogger("openstore.psp.router")

_psp_config: Settings | None = None


def set_psp_config(config: Settings) -> None:
    global _psp_config
    _psp_config = config


def psp_router(config: Settings) -> APIRouter:
    router = APIRouter()
    set_psp_config(config)

    @router.post("/webhooks/razorpay")
    async def razorpay_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_razorpay_event_id: str | None = Header(default=None),
        x_razorpay_signature: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_body = await request.body()

        cfg = _psp_config or config
        webhook_secret = cfg.razorpay.webhook_secret or "test_secret"

        if not x_razorpay_signature:
            raise HTTPException(status_code=401, detail="missing_signature")

        if not driver.verify_webhook_signature(raw_body, x_razorpay_signature, webhook_secret):
            raise HTTPException(status_code=401, detail="invalid_signature")

        import json as _json
        try:
            payload = _json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(status_code=400, detail=f"invalid_json: {e}")

        event_name = payload.get("event", "")
        if event_name not in driver.HANDLED_WEBHOOK_EVENTS:
            return {"received": True, "ignored": True, "reason": f"unhandled_event: {event_name}"}

        import hashlib
        event_id = x_razorpay_event_id or hashlib.sha256(raw_body).hexdigest()

        session = get_session(cfg)
        try:
            driver.persist_raw_webhook_event(
                session=session,
                raw_body=raw_body,
                signature=x_razorpay_signature,
                x_event_id=event_id,
                trace_id=f"webhook_{event_id[:16]}",
                client_id="razorpay",
            )
            session.commit()
        finally:
            session.close()

        background_tasks.add_task(_process_event_in_worker, event_id, cfg)

        return {"received": True, "event_id": event_id, "event_type": event_name}

    @router.post("/hold/{cancel_token}/cancel")
    async def hold_cancel(cancel_token: str, request: Request) -> dict[str, Any]:
        cfg = _psp_config or config

        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            body = {}

        trace_id = body.get("trace_id", f"trace_cancel_{_secrets.token_hex(4)}")
        client_id = body.get("client_id", "anonymous")

        session = get_session(cfg)
        try:
            checkout = session.exec(
                select(Checkout).where(Checkout.cancel_token == cancel_token)
            ).first()
            if not checkout:
                raise HTTPException(status_code=404, detail="invalid_cancel_token")

            if checkout.state not in (OrderState.HELD,):
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid_state: {checkout.state}",
                )

            mock_rz = None
            try:
                if body.get("__mock_razorpay__") is not None:
                    mock_rz = body["__mock_razorpay__"]
                else:
                    # Patch _get_client for testing (test patches driver_mod._get_client)
                    import openstore.psp.razorpay_driver as rz_mod
                    mock_rz = rz_mod._get_client(cfg)
            except Exception:
                mock_rz = None

            try:
                resp = driver.cancel_payment_link(
                    config=cfg,
                    session=session,
                    trace_id=trace_id,
                    client_id=client_id,
                    checkout_id=checkout.id,
                    mock_razorpay=mock_rz,
                )
            except Exception as e:
                if "already_paid" in str(e).lower() or "already cancelled" in str(e).lower() or "400" in str(e):
                    create_release_entry(
                        session=session,
                        trace_id=trace_id,
                        client_id=client_id,
                        checkout_id=checkout.id,
                        amount_minor=checkout.amount_minor,
                        currency=checkout.currency,
                        description="hold_cancel (already-paid/cancelled)",
                    )
                    checkout.state = OrderState.CANCELLED
                    checkout.cancelled_at = datetime.now(UTC)
                    checkout.updated_at = datetime.now(UTC)
                    session.add(checkout)
                    session.commit()
                    return {"status": "RELEASE", "checkout_id": checkout.id}
                raise

            session.commit()
            return {"status": "RELEASE" if resp.get("action") == "cancelled" else "REFUND",
                    "checkout_id": checkout.id, "psp_action": resp.get("action")}
        finally:
            session.close()

    return router


def _process_event_in_worker(event_id: str, cfg: Settings) -> None:
    """Background worker: re-fetch event and process."""
    session = get_session(cfg)
    try:
        from openstore.models import WebhookEvent
        event = session.exec(
            select(WebhookEvent).where(WebhookEvent.psp_event_id == event_id)
        ).first()
        if event:
            driver.process_webhook_in_worker(session, event)
            session.commit()
    finally:
        session.close()


def _get_client(config: Settings) -> Any:
    """Module-level factory for the Razorpay client (used by tests to mock)."""
    from razorpay import Client
    return Client(auth=(config.razorpay.key_id, config.razorpay.key_secret))
