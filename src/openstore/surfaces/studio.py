# OpenStore — Policy Studio surface (S3.5). Standalone APIRouter so it is
# testable via TestClient without touching server.py (which is out of scope).
#
# Routes (all under registered REGISTRY prefixes):
#   GET  /intent/studio              -> renders templates/policy_studio.html,
#                                        or (kind=amendment) templates/amendment_studio.html
#   POST /internal/webauthn/register/begin
#   POST /internal/webauthn/register/complete   (INV-10: user_id from session)
#   POST /internal/webauthn/assertion/begin     (challenge bound {"mode":"policy"})
#   POST /internal/webauthn/assertion/complete  (assertion + optional policy sign)
#   GET  /internal/policy/blast-radius          (per signed-in operator)
#   POST /intent/amendment/<amendment_id>/approve  (S11 Phase 4 / Q-017, Q-020)
#   POST /intent/amendment/<amendment_id>/reject   (S11 Phase 4 / Q-017, Q-020)
#   POST /intent/cart/<cart_id>/approve  (S16 / Q-033: per-cart passkey tap → AAL2)
#   POST /intent/cart/<cart_id>/reject   (S16 / Q-033)
#
# Amendment approval (S11 Phase 4) needs no separate "begin assertion" route:
# the WebAuthn challenge (bound to {"mode":"amendment","amendment_id":...})
# is issued inline while rendering /intent/studio?token=... for a
# kind=amendment handoff, and embedded in the page — the two approve/reject
# routes above are the only amendment-specific routes this stage adds
# (Q-017's exact reservation; no third route is invented).
#
# Signing-time validation (PRD §3.2a + Q-006 resolution): a policy whose
# max_spend_total_minor would push the enrolled user's aggregate above the
# per-user cap is rejected with policy.aggregate_cap_exceeded; a policy_version
# != 2 is rejected with policy.policy_version_unsupported (closed set). All
# totals are recomputed server-side (R0.8); the user_id / credential selection
# comes from the session, never the request body (INV-10).
#
# Q-005 AMENDMENT: every WebAuthn rejection is mirrored into the evidence trail
# (AuditLogEntry detail + #alerts) with its precise failure_type, so security-
# relevant failures (e.g. sign_count_regression) are never flattened.
from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from openstore.agents.buyer_agent import (
    _friendly_denial_reason,
    build_shop_result_embed,
    compute_cart_hash,
    render_shop_result,
    resume_after_signing,
)
from openstore.agents.mcp_client import InProcessMCPClient
from openstore.config import Settings, merchant_id
from openstore.core.api import create_checkout_from_policy
from openstore.core.campaigns import (
    CampaignValidationError,
    activate_campaign,
    get_analytics_view,
    pause_campaign,
    reject_campaign,
)
from openstore.core.database import get_session
from openstore.core.handoff import HandoffError, buyer_handle, consume_handoff, resolve_handoff
from openstore.core.holdcancel import AAL_HOLD_SECONDS
from openstore.core.policy_signing import (
    PER_USER_AGGREGATE_CAP_MINOR,
    blast_radius,
    complete_policy_signing,
)
from openstore.core.webauthn_rp import (
    ChallengeStore,
    WebAuthnError,
    begin_assertion,
    begin_registration,
    complete_assertion,
    complete_registration,
    get_user_credentials,
)
from openstore.models import (
    AuditLog,
    Campaign,
    Checkout,
    Handoff,
    HandoffKind,
    IntentPolicy,
)
from openstore.notifier import send_dm, sync_alert
from openstore.psp.razorpay_driver import create_payment_link
from openstore.core.session import (
    SESSION_COOKIE,
    validate_session,
    csrf_token_for,
)
from openstore.core.api import CommerceError

TEMPLATES = Path(__file__).resolve().parent / "templates"

_NONCE_HEADER = "X-Operator-Id"


class RegistrationBegin(BaseModel):
    user_name: str
    display_name: str | None = None


class RegistrationComplete(BaseModel):
    credential_id: str
    client_data_json: str
    attestation_object: str
    challenge: str


class AssertionBegin(BaseModel):
    # DECISION-024: when present, the challenge is bound to
    # {"mode": "campaign", "campaign_id": ...} instead of {"mode": "policy"},
    # so an assertion approving one campaign cannot be replayed onto another.
    campaign_id: str | None = None


class CampaignDecision(BaseModel):
    """Body for POST /campaign/<id>/approve. The assertion fields are mandatory
    (PRD §9.7: the orchestrator cannot publish without a WebAuthn approval);
    /reject and /pause take no assertion."""

    approver_credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str
    challenge: str


class CampaignReject(BaseModel):
    reason: str = ""


class AssertionComplete(BaseModel):
    credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str
    challenge: str
    policy: dict[str, Any] | None = None
    # S11 Phase 2: carried by the template only when the page was opened via a
    # chat-issued ?token=... link, so a successful policy signing can consume
    # the handoff and auto-resume the parked errand (push + auto-resume).
    handoff_token: str | None = None


class AmendmentDecision(BaseModel):
    """S11 Phase 4 (Q-020): body for POST /intent/amendment/<id>/approve and
    /reject. `token` is the buyer's handoff token — identity is resolved
    through it (never a raw header, mirroring the policy-signing handoff
    path); the assertion fields are required for approve (R0.5: NO
    self-approval, a fresh WebAuthn assertion is mandatory) and absent for
    reject."""

    token: str
    credential_id: str | None = None
    client_data_json: str | None = None
    authenticator_data: str | None = None
    signature: str | None = None
    challenge: str | None = None


class CartDecision(BaseModel):
    """S16 (Q-033): body for POST /intent/cart/<id>/approve and /reject.
    Mirrors AmendmentDecision: `token` resolves identity, assertion fields are
    required for approve (fresh per-cart WebAuthn assertion bound to the cart
    hash) and absent for reject."""

    token: str
    credential_id: str | None = None
    client_data_json: str | None = None
    authenticator_data: str | None = None
    signature: str | None = None
    challenge: str | None = None


class _Operator:
    """Session-carried operator identity (INV-10). user_id is never read from a
    request body; it comes from the operator session header, which stands in for
    the authenticated merchant-operator session here (no auth framework is in
    this stage's scope). `authenticated` tells routes which case they got,
    so money-adjacent surfaces can later demand a real session without
    changing the resolution chain."""

    def __init__(self, user_id: str, authenticated: bool = False):
        self.user_id = user_id
        self.authenticated = authenticated


_CAMPAIGN_STATUS = {
    "campaign.not_found": 404,
    "campaign.invalid_state_transition": 409,
    "campaign.max_active_exceeded": 409,
    "campaign.no_webauthn_approval": 401,
    "campaign.webauthn_verification_failed": 401,
}


def _campaign_txn(
    session_factory: Callable[[], Session], op: Callable[[Session], Campaign]
) -> Campaign:
    """Run one campaign transition in its own transaction, mapping the closed-set
    reason code onto an HTTP status. A validation failure rolls back — a rejected
    approval must never leave a half-transitioned row (R0.5)."""
    session = session_factory()
    try:
        try:
            campaign = op(session)
            session.commit()
            session.refresh(campaign)
            return campaign
        except CampaignValidationError as e:
            session.rollback()
            raise HTTPException(
                status_code=_CAMPAIGN_STATUS.get(e.reason_code, 422),
                detail={"reason_code": e.reason_code, "message": e.message},
            ) from e
    finally:
        session.close()


def _campaign_rows(session: Session) -> list[dict[str, Any]]:
    """Every campaign the merchant can act on, newest first. Unlike the public
    feed (INV-13: ACTIVE and in-window only), the review surface deliberately
    shows PENDING_APPROVAL, PAUSED, and EXPIRED too — a merchant cannot approve
    what the feed refuses to show."""
    campaigns = list(session.exec(select(Campaign).order_by(Campaign.created_at.desc())).all())  # type: ignore[attr-defined]
    return [
        {
            "campaign_id": c.id,
            "title": c.title,
            "rationale": c.rationale,
            "discount_bps": c.discount_bps,
            "applies_to_skus": c.applies_to_skus,
            "starts_at": c.starts_at.isoformat() if c.starts_at else None,
            "ends_at": c.ends_at.isoformat() if c.ends_at else None,
            "state": c.state.value,
            "source_signals": c.source_signals,
            "approver_credential_id": c.approver_credential_id,
            "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        }
        for c in campaigns
    ]


def _order_rows(session: Session, limit: int) -> list[dict[str, Any]]:
    orders = list(
        session.exec(
            select(Checkout).order_by(Checkout.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
        ).all()
    )
    return [
        {
            "checkout_id": o.id,
            "state": o.state.value if hasattr(o.state, "value") else str(o.state),
            "amount_minor": o.amount_minor,
            "policy_id": o.policy_id,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            "chat_user_id": o.chat_user_id,
            "evidence_url": f"/orders/{o.id}/evidence/view" if o.poai_bundle else None,
        }
        for o in orders
    ]


def _operator(
    x_operator_id: str | None = Header(default=None, alias=_NONCE_HEADER),
    operator: str | None = Query(default=None),
) -> _Operator:
    """Legacy unauthenticated namespace selection (header or query param).

    operator_id carries no authority of its own (see _Operator's
    docstring) — it only selects which WebAuthn credential set a session
    uses, so accepting it as a query param alongside the header weakens
    nothing. Without this, a plain browser navigation (which cannot set a
    custom header) could never open a GET route behind this dependency at
    all — every fetch() the page itself makes afterward already sends the
    header correctly (JS can set headers; a top-level navigation can't).
    Authenticated callers resolve through _resolve_operator (factory-level,
    cookie first); this stays as the shared fallback."""
    user_id = x_operator_id or operator
    if not user_id or not user_id.strip():
        raise HTTPException(status_code=401, detail="operator session required")
    return _Operator(user_id.strip())


def _csrf_meta_for(request: Request) -> str:
    """CSRF <meta> for pages with mutating fetch() calls: the derived
    token when the viewer holds a session cookie, empty otherwise."""
    raw = request.cookies.get(SESSION_COOKIE)
    return csrf_meta(csrf_token_for(raw) if raw else "")

def _require_csrf_if_session(request: Request) -> None:
    """CSRF gate for mutating studio routes. Fires only when the request
    carries a session cookie (ambient credential = CSRF exposure): the
    cross-site attacker cannot set the header, the page's own JS can.
    Cookie-less callers (tests, header/query operator flows) are
    unaffected. CommerceError maps to its HTTP status with the
    closed-set code (R0.5)."""
    raw = request.cookies.get(SESSION_COOKIE)
    if raw is None:
        return
    try:
        check_csrf(raw, request.headers.get("X-OpenStore-CSRF"))
    except CommerceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"reason_code": e.reason_code, "message": e.message},
        ) from e

def _render_amendment_page(
    handoff: Handoff, token: str, store: ChallengeStore, request: Request
) -> HTMLResponse:
    """S11 Phase 4: render the amendment-approval page. Issues the
    WebAuthn challenge inline (bound to {"mode":"amendment",
    "amendment_id":...}) so no separate "begin assertion" route is
    needed (Q-017 reserves only the two approve/reject routes)."""
    draft_wrapper = handoff.amendment_draft or {}
    draft = draft_wrapper.get("draft", {})
    amendment_id = draft.get("amendment_id", "")
    buyer = buyer_handle(handoff)
    begin = begin_assertion(
        config, buyer, binding={"mode": "amendment", "amendment_id": amendment_id}, store=store
    )
    html = (TEMPLATES / "amendment_studio.html").read_text(encoding="utf-8")
    html = html.replace("__AMENDMENT_ID__", _safe_json(amendment_id))
    html = html.replace("__HANDOFF_TOKEN__", _safe_json(token))
    html = html.replace("__DRAFT_JSON__", _safe_json(draft))
    html = html.replace("__ASSERTION_BEGIN_JSON__", _safe_json(begin))
    html = inject_head(html, _csrf_meta_for(request))
    return HTMLResponse(html)


def inject_head(html_text: str, extra: str) -> str:
    """Insert `extra` markup before </head> without touching template bytes
    otherwise. Lets session-aware handlers add the CSRF meta (and the shell
    stylesheet link) to legacy templates that predate the shell."""
    if "</head>" not in html_text:
        return html_text
    return html_text.replace("</head>", extra + "\n</head>", 1)

def csrf_meta(csrf_token: str) -> str:
    """<meta> carrying the per-session CSRF token for fetch() callers.
    Empty content when the viewer holds no session (CSRF is only enforced
    when a session cookie is present, so anonymous callers need nothing)."""
    import html as _html
    return f'<meta name="csrf-token" content="{_html.escape(csrf_token)}">' ''

def _safe_json(value: Any) -> str:
    """JSON for direct embedding in <script>."""
    import json
    return json.dumps(value, separators=(",", ":"))


_HANDOFF_STATUS = {
    "authority.handoff_not_found": 404,
    "authority.handoff_expired": 410,
    "authority.handoff_consumed": 409,
}

logger = logging.getLogger("openstore.studio")


def _customer_label(handoff: Handoff) -> dict[str, str]:
    """PSP-side display name only (test-mode dashboard label, R0.5: loud
    about which surface originated the checkout)."""
    if handoff.chat_platform == "web":
        return {"name": f"Web buyer {handoff.chat_user_id}"}
    return {"name": f"Discord user {handoff.chat_user_id}"}

# S12 step 8: short timeout for the best-effort resume_url ping — this must
# never make the human wait meaningfully longer for their signing response.
_RESUME_NOTIFY_TIMEOUT_SECONDS = 3.0


def _resolve_handoff_token(session_factory: Callable[[], Session], token: str) -> str:
    """S11 Phase 2 (Q-014 / Q-016): resolve a chat-issued handoff `token` to the
    signing buyer's webauthn user_handle via the `handoffs` table. Fails loud
    (R0.5) on an unknown, expired, or already-consumed token — never a silent
    fallback to the operator-header identity."""
    return buyer_handle(_resolve_handoff(session_factory, token))


def _resolve_handoff(session_factory: Callable[[], Session], token: str) -> Handoff:
    """Like _resolve_handoff_token, but returns the full row (S11 Phase 4
    needs kind/amendment_draft, not just the buyer handle)."""
    session = session_factory()
    try:
        return resolve_handoff(session, token)
    except HandoffError as e:
        raise HTTPException(
            status_code=_HANDOFF_STATUS[e.reason_code],
            detail={"reason_code": e.reason_code, "message": e.message},
        )
    finally:
        session.close()


async def _notify_resume_url(config: Settings, resume_url: str, handoff_token: str) -> None:
    """S12 step 8: best-effort ping to a federated buyer process (its own
    /internal/signing-complete endpoint) that some buyer may have finished
    signing at this merchant.

    SECURITY: this POST carries NO AUTHORITY. The body is exactly
    {handoff_token, merchant_id} — no policy_id, no cap, nothing the
    receiver could mistake for proof a policy exists. The receiving side
    (surfaces/buyer_internal.py, in the buyer's own process) MUST
    independently re-verify over its own authenticated MCP channel
    (resolve_policy) before acting on this — a forged or replayed call here
    must gain an attacker nothing beyond making the buyer re-check and find
    nothing. This is what keeps DECISION-022's "no signing hub, no new trust
    root" line intact: this process is a doorbell, never a source of truth
    for the buyer.

    Must never fail or block the signing response the human is looking at —
    any timeout, connection error, or non-2xx is logged and swallowed."""
    try:
        async with httpx.AsyncClient(timeout=_RESUME_NOTIFY_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                resume_url,
                json={"handoff_token": handoff_token, "merchant_id": merchant_id(config)},
            )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("resume_url notify to %s failed: %s", resume_url, e)


async def _consume_and_resume(
    config: Settings,
    session_factory: Callable[[], Session],
    handoff_token: str,
    policy_id: str,
) -> dict[str, Any]:
    """S11 Phase 2 push + auto-resume: on successful policy signing, consume
    the handoff, DM the buyer that signing succeeded, and automatically resume
    the stored request_text (the whole point of the handoff bridge) so they
    never retype their errand. A handoff that fails to resolve (e.g. already
    consumed by a second tab) does not undo the policy that was just signed —
    it is surfaced in the response, not raised, since the signing itself
    already succeeded and committed.

    S12 step 8: resume_after_signing only works when the buyer and this
    merchant share a process — gated on config.discord.buyer_bot_enabled,
    the existing "this process hosts a live buyer conversation" signal. A
    federated buyer (buyer_cli.py) runs elsewhere and is never reachable
    this way; when the handoff carries a resume_url instead, that buyer
    process gets a best-effort, no-authority ping (_notify_resume_url) and
    re-verifies for itself."""
    session = session_factory()
    try:
        handoff = consume_handoff(session, handoff_token, result_policy_id=policy_id)
        session.commit()
        chat_platform = handoff.chat_platform
        chat_user_id = handoff.chat_user_id
        chat_channel_id = handoff.chat_channel_id
        request_text = handoff.request_text
        resume_url = handoff.resume_url
    except HandoffError as e:
        session.rollback()
        sync_alert("handoff_resume_failed", e.message, {"reason_code": e.reason_code})
        return {"resumed": False, "reason_code": e.reason_code}
    finally:
        session.close()

    await send_dm(config, chat_user_id, "Signed. Resuming your order…")

    outcome: dict[str, Any] = {"resumed": False}
    # Q-042: auto-resume only replays a Discord conversation (the buyer agent
    # lives there). A web-platform signing has no conversation to resume into —
    # resuming would mint a checkout + pay link the browser never sees. Web
    # signers return to /chat, which re-reads their policy on the next cart.
    if config.discord.buyer_bot_enabled and chat_platform == "discord":
        result = await resume_after_signing(
            config,
            InProcessMCPClient(config),
            chat_platform=chat_platform,
            chat_user_id=chat_user_id,
            chat_channel_id=chat_channel_id,
            request_text=request_text,
            policy_id=policy_id,
            trace_id=f"trace_handoff_{handoff_token[:8]}",
        )
        outcome = {"resumed": True, "shop_result": result}

    if resume_url:
        await _notify_resume_url(config, resume_url, handoff_token)

    return outcome


def _apply_amendment_delta(policy: IntentPolicy, delta: dict[str, Any]) -> IntentPolicy:
    """S11 Phase 4 (Q-020): apply exactly the fields draft_amendment()'s delta
    names — nothing invented beyond them (R0.3). `add_allowed_skus` exempts
    those specific SKUs from policy.blocked_skus for this one recompile;
    `bump_max_spend_per_tx_minor` raises the effective per-tx cap to at least
    that amount. Returns a new, unpersisted IntentPolicy snapshot (delta's own
    `one_time: True` — this never mutates the standing policy row)."""
    add_skus = set(delta.get("add_allowed_skus") or [])
    bump = delta.get("bump_max_spend_per_tx_minor", 0)
    return policy.model_copy(
        update={
            "blocked_skus": [s for s in policy.blocked_skus if s not in add_skus],
            "max_spend_per_tx_minor": max(policy.max_spend_per_tx_minor, bump),
        }
    )


async def _approve_amendment(
    config: Settings,
    session_factory: Callable[[], Session],
    handoff: Handoff,
    token: str,
) -> dict[str, Any]:
    """S11 Phase 4: apply the amendment as a one-time relief (Q-020), recompile,
    and — on ALLOW — create the checkout + payment link directly (the
    one-time relief exists only in this call's memory, so re-running the
    normal shop()/create_cart round-trip through the real, unrelieved policy
    row would just re-deny). DMs the buyer either way; consumes the handoff
    exactly once regardless of outcome (a decision was rendered)."""
    session = session_factory()
    try:
        draft_wrapper = handoff.amendment_draft or {}
        draft = draft_wrapper.get("draft", {})
        cart = draft_wrapper.get("cart", [])
        base_policy_hash = draft.get("base_policy_hash")

        policy = session.exec(
            select(IntentPolicy).where(IntentPolicy.policy_hash == base_policy_hash)
        ).first()
        if not policy:
            consume_handoff(session, token)
            session.commit()
            return {"applied": False, "reason_code": "authority.policy_unsigned"}

        amended_policy = _apply_amendment_delta(policy, draft.get("delta", {}))
        cart_hash = compute_cart_hash(cart)
        trace_id = f"trace_amend_{token[:8]}"
        client_id = f"discord:{handoff.chat_user_id}"

        result = create_checkout_from_policy(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            merchant_id=amended_policy.merchant_id,
            cart_items=cart,
            cart_hash=cart_hash,
            cart_version=1,
            policy=amended_policy,
            assertion_verified=True,  # the amendment approval assertion just verified
            agent_plan={"amendment_id": draft.get("amendment_id")},
        )

        if not result.allowed:
            consume_handoff(session, token, result_policy_id=policy.id)
            session.commit()
            await send_dm(
                config,
                handoff.chat_user_id,
                # Never show a raw closed-set reason_code to a buyer — the same
                # friendly map the bot uses everywhere else.
                "Amendment approved, but the cart still doesn't fit — "
                f"{_friendly_denial_reason(result.reason_code)}.",
            )
            return {
                "applied": False,
                "reason_code": result.reason_code,
                "transcript": result.transcript,
            }

        checkout = session.exec(select(Checkout).where(Checkout.id == result.checkout_id)).first()
        assert checkout is not None
        checkout.chat_platform = handoff.chat_platform
        checkout.chat_user_id = handoff.chat_user_id
        checkout.chat_channel_id = handoff.chat_channel_id
        checkout.request_text = handoff.request_text
        session.add(checkout)
        session.flush()

        checkout = create_payment_link(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout.id,
            amount_minor=checkout.amount_minor,
            currency=checkout.currency,
            customer=_customer_label(handoff),
        )

        consume_handoff(session, token, result_policy_id=policy.id)
        session.commit()

        shop_result = {
            "allowed": True,
            "checkout_id": checkout.id,
            "amount_minor": checkout.amount_minor,
            "currency": checkout.currency,
            "aal_level": result.aal_level,
            "short_url": checkout.short_url,
            "expires_at": checkout.expires_at.isoformat(),
        }
        await send_dm(
            config,
            handoff.chat_user_id,
            "Amendment approved. " + render_shop_result(shop_result),
            embed=build_shop_result_embed(shop_result),
        )
        return {"applied": True, "shop_result": shop_result}
    finally:
        session.close()


async def _approve_cart(
    config: Settings,
    session_factory: Callable[[], Session],
    handoff: Handoff,
    token: str,
) -> dict[str, Any]:
    """S16 (Q-033): the cart approval assertion just verified against the
    cart-bound challenge. Create the checkout with assertion_verified=True so
    the compile grades AAL2, attach chat identity + request_text for the PoAI
    bundle (e8/e9), create the payment link, DM the buyer, and consume the
    handoff exactly once regardless of outcome."""
    session = session_factory()
    try:
        payload = handoff.cart_payload or {}
        cart = payload.get("cart", [])
        cart_hash = payload.get("cart_hash", "")
        policy_id = payload.get("policy_id", "")
        # Recompute the hash server-side (R0.8) — never trust the stored copy
        # against a mutated cart.
        if compute_cart_hash(cart) != cart_hash:
            consume_handoff(session, token)
            session.commit()
            return {"applied": False, "reason_code": "assertion_required"}

        policy = session.exec(
            select(IntentPolicy).where(IntentPolicy.id == policy_id)
        ).first()
        if not policy:
            consume_handoff(session, token)
            session.commit()
            return {"applied": False, "reason_code": "authority.policy_unsigned"}

        trace_id = f"trace_cart_{token[:8]}"
        client_id = f"discord:{handoff.chat_user_id}"

        result = create_checkout_from_policy(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            merchant_id=policy.merchant_id,
            cart_items=cart,
            cart_hash=cart_hash,
            cart_version=1,
            policy=policy,
            assertion_verified=True,  # the cart approval assertion just verified
            agent_plan={"cart_id": payload.get("cart_id")},
        )

        if not result.allowed:
            consume_handoff(session, token, result_policy_id=policy.id)
            session.commit()
            await send_dm(
                config,
                handoff.chat_user_id,
                "Cart approved, but the cart still doesn't fit — "
                f"{_friendly_denial_reason(result.reason_code)}.",
            )
            return {
                "applied": False,
                "reason_code": result.reason_code,
                "transcript": result.transcript,
            }

        checkout = session.exec(select(Checkout).where(Checkout.id == result.checkout_id)).first()
        assert checkout is not None
        checkout.chat_platform = handoff.chat_platform
        checkout.chat_user_id = handoff.chat_user_id
        checkout.chat_channel_id = handoff.chat_channel_id
        checkout.request_text = handoff.request_text
        session.add(checkout)
        session.flush()

        checkout = create_payment_link(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout.id,
            amount_minor=checkout.amount_minor,
            currency=checkout.currency,
            customer=_customer_label(handoff),
        )

        consume_handoff(session, token, result_policy_id=policy.id)
        session.commit()

        shop_result = {
            "allowed": True,
            "checkout_id": checkout.id,
            "amount_minor": checkout.amount_minor,
            "currency": checkout.currency,
            "aal_level": result.aal_level,
            "short_url": checkout.short_url,
            "expires_at": checkout.expires_at.isoformat(),
        }
        await send_dm(
            config,
            handoff.chat_user_id,
            "Cart approved. " + render_shop_result(shop_result),
            embed=build_shop_result_embed(shop_result),
        )
        return {"applied": True, "shop_result": shop_result}
    finally:
        session.close()


def policy_studio_router(
    config: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
    challenge_store: ChallengeStore | None = None,
) -> APIRouter:
    """Build the Policy Studio APIRouter. `session_factory` yields a session per
    request; defaults to get_session(config). `challenge_store` is injectable for
    tests; defaults to a fresh process store."""
    make_session = session_factory or (lambda: get_session(config))
    store = challenge_store or ChallengeStore()

    def _resolve_operator(
        request: Request,
        x_operator_id: str | None = Header(default=None, alias=_NONCE_HEADER),
        operator: str | None = Query(default=None),
    ) -> _Operator:
        """Stage 24 (Q-044): cookie -> header -> ?operator= -> 401.

        A valid `openstore_session` cookie resolves to an AUTHENTICATED
        operator (passkey-verified at login). The header and query param are
        the legacy unauthenticated namespace selectors — kept so no existing
        test or JS flow breaks, and so plain browser navigation works
        (DECISION-030). An expired/invalid cookie falls through to them
        rather than hard-failing, exactly as if no cookie were sent."""
        from openstore.core.api import CommerceError as _CommerceError
        from openstore.core.session import SESSION_COOKIE, validate_session

        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            db = make_session()
            try:
                row = validate_session(
                    db, raw, request.headers.get("user-agent")
                )
                db.commit()  # persist the sliding-window refresh
                return _Operator(row.operator_id, authenticated=True)
            except _CommerceError:
                db.rollback()
            finally:
                db.close()
        return _operator(x_operator_id, operator)

    router = APIRouter()

    def _render_amendment_page(handoff: Handoff, token: str, store: ChallengeStore, request: Request) -> HTMLResponse:
        """S11 Phase 4: render the amendment-approval page. Issues the
        WebAuthn challenge inline (bound to {"mode":"amendment",
        "amendment_id":...}) so no separate "begin assertion" route is
        needed (Q-017 reserves only the two approve/reject routes)."""
        draft_wrapper = handoff.amendment_draft or {}
        draft = draft_wrapper.get("draft", {})
        amendment_id = draft.get("amendment_id", "")
        buyer = buyer_handle(handoff)
        begin = begin_assertion(
            config, buyer, binding={"mode": "amendment", "amendment_id": amendment_id}, store=store
        )
        html = (TEMPLATES / "amendment_studio.html").read_text(encoding="utf-8")
        html = html.replace("__AMENDMENT_ID__", _safe_json(amendment_id))
        html = html.replace("__HANDOFF_TOKEN__", _safe_json(token))
        html = html.replace("__DRAFT_JSON__", _safe_json(draft))
        html = html.replace("__ASSERTION_BEGIN_JSON__", _safe_json(begin))
        html = inject_head(html, _csrf_meta_for(request))
        return HTMLResponse(html)

    def _render_cart_page(
        handoff: Handoff, token: str, store: ChallengeStore, request: Request
    ) -> HTMLResponse:
        """S16 (Q-033): render the per-cart approval page. Issues the WebAuthn
        challenge inline bound to {"mode": "cart", "cart_hash": ...} so the
        resulting assertion cannot be replayed onto a different cart."""
        payload = handoff.cart_payload or {}
        cart_id = payload.get("cart_id", "")
        cart = payload.get("cart", [])
        cart_hash = payload.get("cart_hash", "")
        buyer = buyer_handle(handoff)
        begin = begin_assertion(
            config, buyer, binding={"mode": "cart", "cart_hash": cart_hash}, store=store
        )
        begin["binding"] = {"mode": "cart", "cart_hash": cart_hash}
        html = (TEMPLATES / "cart_studio.html").read_text(encoding="utf-8")
        html = html.replace("__CART_ID__", _safe_json(cart_id))
        html = html.replace("__HANDOFF_TOKEN__", _safe_json(token))
        html = html.replace("__CART_JSON__", _safe_json(cart))
        html = html.replace("__ASSERTION_BEGIN_JSON__", _safe_json(begin))
        html = inject_head(html, _csrf_meta_for(request))
        return HTMLResponse(html)

    # ------------------------------------------------------------------ page
    @router.get("/intent/studio", response_class=HTMLResponse)
    async def studio_page(
        request: Request,
        token: str | None = None,
        x_operator_id: str | None = Header(default=None, alias=_NONCE_HEADER),
        operator: str | None = Query(default=None),
    ) -> HTMLResponse:
        if token is not None:
            handoff = _resolve_handoff(make_session, token)
            if handoff.kind == HandoffKind.AMENDMENT:
                return _render_amendment_page(handoff, token, store, request)
            if handoff.kind == HandoffKind.CART:
                return _render_cart_page(handoff, token, store, request)
            user_id = buyer_handle(handoff)
        else:
            user_id = _resolve_operator(request, x_operator_id, operator).user_id
        html = (TEMPLATES / "policy_studio.html").read_text(encoding="utf-8")
        html = html.replace("__OPERATOR_ID__", _safe_json(user_id))
        html = html.replace("__HANDOFF_TOKEN__", _safe_json(token))
        html = html.replace("__HOLD_TABLE_ROWS__", _render_hold_rows())
        html = html.replace("__CAP_NOTE_HTML__", _render_cap_note())
        html = inject_head(html, _csrf_meta_for(request))
        return HTMLResponse(html)

    # ------------------------------------------------------------ enrolment
    @router.post("/internal/webauthn/register/begin")
    async def register_begin(
        body: RegistrationBegin, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        options = begin_registration(
            config,
            operator.user_id,
            body.user_name,
            body.display_name or body.user_name,
            store=store,
        )
        options["user_id"] = operator.user_id
        return options

    @router.post("/internal/webauthn/register/complete")
    async def register_complete(
        body: RegistrationComplete, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        session = make_session()
        try:
            cred = complete_registration(
                session,
                config,
                operator.user_id,
                body.credential_id,
                body.client_data_json,
                body.attestation_object,
                body.challenge,
                store=store,
            )
            session.commit()
            return {
                "ok": True,
                "credential_id": cred.credential_id,
                "sign_count": cred.sign_count,
            }
        except WebAuthnError as e:
            session.rollback()
            _record_webauthn_failure(session, operator.user_id, body.credential_id, e, "register")
            session.commit()
            raise HTTPException(
                status_code=422, detail={"reason_code": e.reason_code, "message": e.message}
            )
        finally:
            session.close()

    # ------------------------------------------------------------ assertion
    @router.post("/internal/webauthn/assertion/begin")
    async def assertion_begin(
        body: AssertionBegin, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        binding = (
            {"mode": "campaign", "campaign_id": body.campaign_id}
            if body.campaign_id
            else {"mode": "policy"}
        )
        options = begin_assertion(config, operator.user_id, binding=binding, store=store)
        options["binding"] = binding
        return options

    @router.post("/internal/webauthn/assertion/complete")
    async def assertion_complete(
        body: AssertionComplete, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        session = make_session()
        try:
            ok, new_sign_count = complete_assertion(
                session,
                config,
                operator.user_id,
                body.credential_id,
                body.client_data_json,
                body.authenticator_data,
                body.signature,
                body.challenge,
                binding={"mode": "policy"},
                store=store,
            )
            session.commit()
        except WebAuthnError as e:
            session.rollback()
            _record_webauthn_failure(session, operator.user_id, body.credential_id, e, "assertion")
            session.commit()
            raise HTTPException(
                status_code=401, detail={"reason_code": e.reason_code, "message": e.message}
            )
        finally:
            session.close()

        # If a policy payload accompanies the verified assertion, complete the
        # signing ceremony (S3.5 / PRD §3.2a). Gate rejections (legacy policy
        # version, aggregate cap) come back as non-ok SigningOutcome with the
        # exact closed-set reason_code (Q-006 resolution).
        if body.policy is not None:
            outcome = complete_policy_signing(
                fields=body.policy,
                merchant_id=merchant_id(config),
                user_id=operator.user_id,
                credential_id=body.credential_id,
                webauthn_sign_count=new_sign_count,
                aggregate_spent_minor=_current_aggregate(make_session, operator.user_id),
            )
            if not outcome.ok:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "reason_code": outcome.reason_code,
                        "message": f"policy-signing gate rejected: {outcome.reason_code}",
                    },
                )
            assert outcome.policy is not None
            psession = make_session()
            try:
                psession.add(outcome.policy)
                psession.commit()
                response = {
                    "ok": True,
                    "sign_count": new_sign_count,
                    "policy_id": outcome.policy.id,
                    "policy_hash": outcome.policy.policy_hash,
                }
            finally:
                psession.close()
            # S11 Phase 2 push + auto-resume: only when the page was opened via
            # a chat-issued handoff link. Runs after the policy is committed —
            # a resume failure never undoes a policy that was actually signed.
            if body.handoff_token:
                response["handoff"] = await _consume_and_resume(
                    config, make_session, body.handoff_token, outcome.policy.id
                )
            return response

        return {"ok": ok, "sign_count": new_sign_count}

    # --------------------------------------------------------------- campaigns
    # DECISION-024: these lived in server.py with NO authentication at all —
    # anyone who could route to the sidecar could reject a campaign, or approve
    # one by posting any truthy dict as the assertion. They now sit behind the
    # same operator gate as /internal/policy/blast-radius, and approval runs a
    # real RP verification inside activate_campaign.
    @router.get("/campaign/studio", response_class=HTMLResponse)
    async def campaign_studio_page(
        request: Request,
        operator: _Operator = Depends(_resolve_operator),
    ) -> HTMLResponse:
        session = make_session()
        try:
            rows = _campaign_rows(session)
        finally:
            session.close()
        html = (TEMPLATES / "campaign_studio.html").read_text(encoding="utf-8")
        html = html.replace("__OPERATOR_ID__", _safe_json(operator.user_id))
        html = html.replace("__CAMPAIGNS_JSON__", _safe_json(rows))
        html = inject_head(html, _csrf_meta_for(request))
        return HTMLResponse(html)

    @router.post("/campaign/{campaign_id}/approve")
    async def campaign_approve(
        campaign_id: str,
        body: CampaignDecision,
        operator: _Operator = Depends(_operator),
    ) -> dict[str, Any]:
        def _act(session: Session) -> Campaign:
            return activate_campaign(
                session,
                campaign_id,
                approver_credential_id=body.approver_credential_id,
                webauthn_assertion={
                    "credential_id": body.approver_credential_id,
                    "client_data_json": body.client_data_json,
                    "authenticator_data": body.authenticator_data,
                    "signature": body.signature,
                    "challenge": body.challenge,
                },
                config=config,
                user_handle=operator.user_id,
                challenge_store=store,
                trace_id=f"campaign:{campaign_id}",
            )

        campaign = _campaign_txn(make_session, _act)
        return {"status": "approved", "campaign_id": campaign.id, "state": campaign.state.value}

    @router.post("/campaign/{campaign_id}/reject")
    async def campaign_reject(
        campaign_id: str,
        body: CampaignReject,
        operator: _Operator = Depends(_operator),
    ) -> dict[str, Any]:
        campaign = _campaign_txn(
            make_session,
            lambda s: reject_campaign(s, campaign_id, body.reason, f"campaign:{campaign_id}"),
        )
        return {
            "status": "rejected",
            "campaign_id": campaign.id,
            "state": campaign.state.value,
            "reason": body.reason,
        }

    @router.post("/campaign/{campaign_id}/pause")
    async def campaign_pause(
        campaign_id: str,
        operator: _Operator = Depends(_operator),
    ) -> dict[str, Any]:
        campaign = _campaign_txn(
            make_session, lambda s: pause_campaign(s, campaign_id, f"campaign:{campaign_id}")
        )
        return {"status": "paused", "campaign_id": campaign.id, "state": campaign.state.value}

    # ------------------------------------------------------------------- admin
    @router.get("/admin/campaigns")
    async def admin_campaigns(request: Request, operator: _Operator = Depends(_resolve_operator)) -> dict[str, Any]:
        session = make_session()
        try:
            return {
                "campaigns": _campaign_rows(session),
                "analytics": get_analytics_view(session, merchant_id(config)),
            }
        finally:
            session.close()

    @router.get("/admin/orders")
    async def admin_orders(
        limit: int = 50, operator: _Operator = Depends(_operator)
    ) -> dict[str, Any]:
        session = make_session()
        try:
            return {"orders": _order_rows(session, limit)}
        finally:
            session.close()

    @router.get("/admin/orders/view", response_class=HTMLResponse)
    async def admin_orders_page(
        request: Request,
        limit: int = 50, operator: _Operator = Depends(_resolve_operator)
    ) -> HTMLResponse:
        session = make_session()
        try:
            rows = _order_rows(session, limit)
        finally:
            session.close()
        html = (TEMPLATES / "orders_admin.html").read_text(encoding="utf-8")
        html = html.replace("__OPERATOR_ID__", _safe_json(operator.user_id))
        html = html.replace("__ORDERS_JSON__", _safe_json(rows))
        html = html.replace("__LIMIT__", str(limit))
        html = inject_head(html, _csrf_meta_for(request))
        return HTMLResponse(html)

    # ------------------------------------------------------------ blast radius
    @router.get("/internal/policy/blast-radius")
    async def policy_blast_radius(operator: _Operator = Depends(_operator)) -> dict[str, Any]:
        session = make_session()
        try:
            # Scoped by the operator's own credentials, not merchant_id: with
            # merchant_id now the configured merchant (shared by everyone,
            # single-tenant), a merchant_id filter would show every operator's
            # policies pooled together instead of just this one's (see
            # _current_aggregate for the same fix and its rationale).
            credentials = get_user_credentials(session, operator.user_id)
            credential_ids = [c.credential_id for c in credentials]
            policies = (
                list(
                    session.exec(
                        select(IntentPolicy).where(
                            IntentPolicy.webauthn_credential_id.in_(credential_ids)  # type: ignore[attr-defined]
                        )
                    ).all()
                )
                if credential_ids
                else []
            )
            envelope_ids = [p.policy_hash for p in policies]
            radius = blast_radius(policies, envelope_ids)
            radius["credentials"] = len(credentials)
            return radius
        finally:
            session.close()

    # ------------------------------------------------------------ amendment ceremony (S11 Phase 4)
    @router.post("/intent/amendment/{amendment_id}/approve")
    async def amendment_approve(amendment_id: str, body: AmendmentDecision) -> dict[str, Any]:
        handoff = _resolve_handoff(make_session, body.token)
        if handoff.kind != HandoffKind.AMENDMENT:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "not an amendment handoff",
                },
            )
        draft = (handoff.amendment_draft or {}).get("draft", {})
        if draft.get("amendment_id") != amendment_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "amendment_id mismatch",
                },
            )
        if not (
            body.credential_id
            and body.client_data_json
            and body.authenticator_data
            and body.signature
            and body.challenge
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "reason_code": "assertion_required",
                    "message": "amendment approval requires a WebAuthn assertion",
                },
            )

        buyer = buyer_handle(handoff)
        vsession = make_session()
        try:
            complete_assertion(
                vsession,
                config,
                buyer,
                body.credential_id,
                body.client_data_json,
                body.authenticator_data,
                body.signature,
                body.challenge,
                binding={"mode": "amendment", "amendment_id": amendment_id},
                store=store,
            )
            vsession.commit()
        except WebAuthnError as e:
            vsession.rollback()
            _record_webauthn_failure(vsession, buyer, body.credential_id, e, "amendment")
            vsession.commit()
            raise HTTPException(
                status_code=401, detail={"reason_code": e.reason_code, "message": e.message}
            )
        finally:
            vsession.close()

        return await _approve_amendment(config, make_session, handoff, body.token)

    @router.post("/intent/amendment/{amendment_id}/reject")
    async def amendment_reject(amendment_id: str, body: AmendmentDecision) -> dict[str, Any]:
        handoff = _resolve_handoff(make_session, body.token)
        if handoff.kind != HandoffKind.AMENDMENT:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "not an amendment handoff",
                },
            )
        draft = (handoff.amendment_draft or {}).get("draft", {})
        if draft.get("amendment_id") != amendment_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "amendment_id mismatch",
                },
            )

        session = make_session()
        try:
            consume_handoff(session, body.token)
            session.commit()
        finally:
            session.close()

        await send_dm(config, handoff.chat_user_id, "Amendment rejected, order not placed.")
        return {"applied": False, "state": "REJECTED"}

    # ------------------------------------------------------------ cart ceremony (S16 / Q-033)
    @router.post("/intent/cart/{cart_id}/approve")
    async def cart_approve(cart_id: str, body: CartDecision) -> dict[str, Any]:
        handoff = _resolve_handoff(make_session, body.token)
        if handoff.kind != HandoffKind.CART:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "not a cart handoff",
                },
            )
        payload = handoff.cart_payload or {}
        if payload.get("cart_id") != cart_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "cart_id mismatch",
                },
            )
        if not (
            body.credential_id
            and body.client_data_json
            and body.authenticator_data
            and body.signature
            and body.challenge
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "reason_code": "assertion_required",
                    "message": "cart approval requires a WebAuthn assertion",
                },
            )

        buyer = buyer_handle(handoff)
        vsession = make_session()
        try:
            complete_assertion(
                vsession,
                config,
                buyer,
                body.credential_id,
                body.client_data_json,
                body.authenticator_data,
                body.signature,
                body.challenge,
                binding={"mode": "cart", "cart_hash": payload.get("cart_hash", "")},
                store=store,
            )
            vsession.commit()
        except WebAuthnError as e:
            vsession.rollback()
            _record_webauthn_failure(vsession, buyer, body.credential_id, e, "cart")
            vsession.commit()
            raise HTTPException(
                status_code=401, detail={"reason_code": e.reason_code, "message": e.message}
            )
        finally:
            vsession.close()

        return await _approve_cart(config, make_session, handoff, body.token)

    @router.post("/intent/cart/{cart_id}/reject")
    async def cart_reject(cart_id: str, body: CartDecision) -> dict[str, Any]:
        handoff = _resolve_handoff(make_session, body.token)
        if handoff.kind != HandoffKind.CART:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "not a cart handoff",
                },
            )
        payload = handoff.cart_payload or {}
        if payload.get("cart_id") != cart_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "authority.handoff_not_found",
                    "message": "cart_id mismatch",
                },
            )

        session = make_session()
        try:
            consume_handoff(session, body.token)
            session.commit()
        finally:
            session.close()

        await send_dm(config, handoff.chat_user_id, "Cart rejected, order not placed.")
        return {"applied": False, "state": "REJECTED"}

    return router


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _record_webauthn_failure(
    session: Session,
    operator_user_id: str,
    credential_id: str | None,
    err: WebAuthnError,
    ceremony: str,
) -> None:
    """Q-005 AMENDMENT: surface a WebAuthn failure in the evidence trail instead
    of flattening it. Writes an AuditLogEntry whose detail carries the precise
    failure_type (cloned-authenticator signals like sign_count_regression must
    never vanish), and emits a #alerts trace. failure_type stays local to the
    audit detail — it is NOT a REGISTRY reason_code.

    The audit row is written directly (not via core.audit.audit_log): that
    helper's `metadata` kwarg maps to no model field (`audit_metadata` is the
    real column), so every entry it writes today carries None detail. audit.py
    is outside this stage's SCOPE, so the detail-bearing entry is built here.
    """
    entry = AuditLog(
        trace_id=f"studio_{secrets.token_hex(8)}",
        client_id=operator_user_id,
        action=f"webauthn_{ceremony}_rejected",
        resource_type="webauthn",
        resource_id=credential_id,
        audit_metadata={
            "reason_code": err.reason_code,
            "failure_type": err.failure_type,
            "message": err.message,
        },
    )
    session.add(entry)
    session.flush()
    sync_alert(
        "webauthn_failure",
        err.message,
        {"failure_type": err.failure_type, "reason_code": err.reason_code},
    )


def _current_aggregate(session_factory: Callable[[], Session], user_id: str) -> int:
    """Recompute the enrolled operator's current active aggregate server-side
    (R0.8). Never trusted from the client.

    IntentPolicy has no user/buyer column. A user's policies are reached by
    joining through webauthn_credential_id to the credentials enrolled under
    this user_handle (S11 plan: "Buyer identity rides on the existing
    credential join"). This used to filter on
    IntentPolicy.merchant_id == user_id, which was only correct by accident
    because merchant_id and user_id were the same unverified header string;
    now that merchant_id is the configured merchant (see config.merchant_id), the
    aggregate must be scoped by credential instead.
    """
    session = session_factory()
    try:
        credential_ids = [c.credential_id for c in get_user_credentials(session, user_id)]
        if not credential_ids:
            return 0
        active = list(
            session.exec(
                select(IntentPolicy).where(
                    IntentPolicy.webauthn_credential_id.in_(credential_ids),  # type: ignore[attr-defined]
                    IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
                )
            ).all()
        )
        return sum(p.max_spend_total_minor for p in active)
    finally:
        session.close()


# Server-side rendering for the HOLD_SECONDS table and the aggregate-cap note.
# The values are normative (DECISIONS §11.1.5 / PRD §3.2a) and must be visible
# before the human signs; rendering them on the server keeps the page meaningful
# even before JavaScript runs.
_HOLD_MEANINGS = {
    3: "fresh, user-verified signature on this exact cart — immediate settlement",
    2: "standing policy with a fresh user-verified signature — 15-minute hold",
    1: "freshness / attestation / verification predicate failed — 1-hour hold",
}


def _render_hold_rows() -> str:
    from openstore.core.holdcancel import AALLevel

    rows: list[str] = []
    for level in (3, 2, 1):
        secs = AAL_HOLD_SECONDS.get(AALLevel(level))
        label = "no order created" if secs is None else f"{secs}s"
        rows.append(
            f"<tr><td><strong>AAL{level}</strong></td><td>{label}</td>"
            f"<td>{_HOLD_MEANINGS[level]}</td></tr>"
        )
    return "".join(rows)


def _render_cap_note() -> str:
    return (
        "per_user_aggregate_cap_minor = "
        f"{PER_USER_AGGREGATE_CAP_MINOR} paise. Signing a policy that pushes the "
        "enrolled operator's active aggregate above this is rejected with "
        "policy.aggregate_cap_exceeded."
    )
