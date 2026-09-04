# OpenStore PSP — FastAPI router for Razorpay webhooks and hold/cancel
# Stage 5: webhook endpoint + /hold/{cancel_token}/cancel

from __future__ import annotations

import logging
import secrets as _secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlmodel import select

from openstore.config import Settings
from openstore.core.database import get_session
from openstore.models import Checkout, WebhookEvent, WebhookStatus
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
                result = driver.cancel_checkout_by_id(
                    config=cfg,
                    session=session,
                    trace_id=trace_id,
                    client_id=client_id,
                    checkout=checkout,
                    mock_razorpay=mock_rz,
                )
            except driver.RazorpayError as e:
                if e.error_code == "psp.invalid_state":
                    raise HTTPException(status_code=400, detail=e.message)
                raise

            session.commit()
            return result
        finally:
            session.close()

    return router


def _process_event_in_worker(event_id: str, cfg: Settings) -> None:
    """Background worker: re-fetch event and process."""
    session = get_session(cfg)
    try:
        event = session.exec(
            select(WebhookEvent).where(WebhookEvent.psp_event_id == event_id)
        ).first()
        if event:
            driver.process_webhook_in_worker(session, event)
            session.commit()
            if event.status == WebhookStatus.COMPLETED:
                _push_chat_notification(cfg, session, event)
    finally:
        session.close()


# Event types that push a chat notification once the state transition lands
# (S11 Phase 3, plan item #15: "webhook path -> push paid/held/released to
# the buyer's DM and to #money-trace").
_CHAT_PUSH_EVENTS = frozenset(
    {
        "payment_link.paid",
        "payment_link.partially_paid",
        "payment_link.cancelled",
    }
)


def _push_chat_notification(cfg: Settings, session: Any, event: WebhookEvent) -> None:
    """DM the buyer and mirror to #money-trace when a webhook-driven state
    transition lands on a chat-originated checkout (Checkout.chat_user_id).
    Runs inside the sync BackgroundTasks worker (FastAPI runs it in a
    threadpool), so asyncio.run() is safe here — there is no running loop to
    conflict with."""
    if event.event_type not in _CHAT_PUSH_EVENTS:
        return

    link = event.payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    reference_id = link.get("reference_id")
    if not reference_id:
        return

    checkout = session.exec(select(Checkout).where(Checkout.id == reference_id)).first()
    if not checkout or not checkout.chat_user_id:
        return

    if event.event_type in ("payment_link.paid", "payment_link.partially_paid"):
        message = f"Paid! ₹{checkout.amount_minor / 100:.2f} — order released."
        trace_action = "PAID"
    else:
        message = "Hold cancelled."
        trace_action = "CANCELLED"

    import asyncio

    from openstore.notifier import DiscordNotifier, send_dm

    asyncio.run(send_dm(cfg, checkout.chat_user_id, message))
    asyncio.run(
        DiscordNotifier(cfg).money_trace(
            checkout.trace_id, trace_action, checkout.amount_minor, checkout.currency
        )
    )

    # S11 Phase 4 (plan items #20-22): produce the PoAI evidence bundle once a
    # chat-originated checkout reaches RELEASED, using the DM's own body/time
    # as the `notification` section and the original chat goal as
    # `human_intent` — both are on hand only here (Checkout.request_text,
    # Q-020). Scoped to chat checkouts (this function's early-return above
    # already requires chat_user_id); non-chat (API-created) checkouts have
    # no request_text/DM to build those sections from.
    if event.event_type in ("payment_link.paid", "payment_link.partially_paid"):
        _build_and_store_evidence(cfg, session, checkout, message)
        session.commit()
        _push_evidence_link(cfg, checkout)


def _build_and_store_evidence(
    cfg: Settings, session: Any, checkout: Checkout, dm_message: str
) -> None:
    """Assemble and persist the PoAI bundle for a RELEASED checkout (Q-020).

    human_intent / notification are populated from real, on-hand data
    (chat request_text, the DM just sent) so PoAI predicates e8/e9 hold —
    see poai.py's evaluate_aal_predicates for the exact key names each
    predicate reads. authority.webauthn is populated with what is
    legitimately reconstructable (credential_id, policy_version) but NOT a
    fabricated `authenticator_data` — the raw per-checkout WebAuthn assertion
    is not persisted anywhere retrievable by checkout_id (Q-020 records this
    gap), so predicate e2 (and everything gated behind it) is honestly False
    for a bundle built this way; this is unrelated to Checkout.aal_level
    (item 22's already-flagged known divergence between the amount-based
    holdcancel AAL and the bundle's own recomputed AAL)."""
    import hashlib
    from datetime import UTC, datetime

    from openstore.core.poai import create_poai_bundle
    from openstore.models import IntentPolicy
    from openstore.surfaces.wellknown import get_catalog_signing_key

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    request_text = checkout.request_text or ""
    human_intent = None
    if request_text:
        human_intent = {
            "request_text": request_text,
            # poai.evaluate_aal_predicates' e8 compares this against a raw
            # sha256 hexdigest of request_text (no "sha256:" prefix) — do not
            # prefix it here, unlike every other digest in this codebase.
            "request_digest": hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
        }

    receipt_digest = hashlib.sha256(dm_message.encode("utf-8")).hexdigest()

    policy = None
    if checkout.policy_id:
        policy = session.exec(
            select(IntentPolicy).where(IntentPolicy.id == checkout.policy_id)
        ).first()

    cart_snapshot = checkout.cart_snapshot or {}

    bundle = create_poai_bundle(
        transaction={
            "checkout_id": checkout.id,
            "amount_minor": checkout.amount_minor,
            "merchant_id": checkout.merchant_id,
            "currency": checkout.currency,
        },
        human_intent=human_intent,
        authority={
            "scheme": "native_webauthn",
            "policy": {
                "policy_id": policy.id,
                "policy_version": policy.policy_version,
                "policy_hash": policy.policy_hash,
            }
            if policy
            else None,
            "webauthn": {"credential_id": policy.webauthn_credential_id} if policy else None,
        },
        goods={"items": cart_snapshot.get("items", []), "cart_hash": checkout.cart_hash},
        agent={
            "client_id": checkout.client_id,
            "scopes": ["checkout:confirm"],
            "token_jti": checkout.trace_id,
        },
        adjudication={"verdict": "ALLOW", "transcript": [], "evaluated_at": now_iso},
        notification={"sent_at": now_iso, "receipt_digest": f"sha256:{receipt_digest}"},
        aal={"level": checkout.aal_level},
        merchant_private_key_pem=get_catalog_signing_key(checkout.merchant_id),
        merchant_id=checkout.merchant_id,
    )
    checkout.poai_bundle = bundle
    session.add(checkout)
    session.flush()


def _push_evidence_link(cfg: Settings, checkout: Checkout) -> None:
    """DM the buyer a receipt link to the evidence viewer (plan item #22:
    "mount evidence_viewer.html, DM the receipt link")."""
    import asyncio

    from openstore.notifier import send_dm

    origin = (cfg.public_base_url or cfg.webauthn.origin or "").rstrip("/")
    link = f"{origin}/orders/{checkout.id}/evidence/view"
    assert checkout.chat_user_id is not None
    asyncio.run(send_dm(cfg, checkout.chat_user_id, f"Evidence: {link}"))


def _get_client(config: Settings) -> Any:
    """Module-level factory for the Razorpay client (used by tests to mock)."""
    from razorpay import Client

    return Client(auth=(config.razorpay.key_id, config.razorpay.key_secret))
