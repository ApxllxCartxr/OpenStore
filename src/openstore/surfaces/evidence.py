# OpenStore — Evidence surface (S11 Phase 4 / plan items #20-22).
#
# Routes (REGISTRY.json):
#   GET /orders/{checkout_id}/evidence       -> the persisted PoAI bundle (JSON)
#   GET /orders/{checkout_id}/evidence/view  -> renders templates/evidence_viewer.html
#
# The bundle itself is produced in psp/router.py when a chat-originated
# checkout reaches RELEASED (webhook path) and persisted on
# Checkout.poai_bundle (Q-020). This module only serves it back.
#
# Access model (Stage 24 / Q-045, DEF-8): the bundle contains buyer detail,
# so we require ONE of: a valid merchant session cookie, the
# buyer's existing capability token (?buyer_key=, same ownership check as
# /web/order), or a merchant-minted share token (?t=, "Share receipt" from
# /merchant/orders). Brute-force protection on the bearer token is an
# in-memory per-IP failure counter that alerts (no new response code — the
# response stays an indistinguishable 404, giving token-guessers no oracle).

from __future__ import annotations

import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.api import CommerceError
from openstore.core.database import get_session
from openstore.core.session import SESSION_COOKIE, hash_token, validate_session
from openstore.models import Checkout
from openstore.notifier import sync_alert
from openstore.surfaces.webcart import _BUYER_KEY_RE, _WEB_PLATFORM

TEMPLATES = Path(__file__).resolve().parent / "templates"


def evidence_router(
    config: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> APIRouter:
    """Build the Evidence APIRouter. `session_factory` yields a session per
    request; defaults to get_session(config) (injectable for tests, matching
    policy_studio_router's convention)."""
    make_session = session_factory or (lambda: get_session(config))

    router = APIRouter()
    _failures: dict[tuple[str, str], list[float]] = {}
    _alerted: dict[tuple[str, str], float] = {}

    _FAILURE_WINDOW_SECONDS = 600.0
    _FAILURE_ALERT_THRESHOLD = 20

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _is_merchant(request: Request) -> bool:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return False
        db = make_session()
        try:
            try:
                validate_session(db, raw, request.headers.get("user-agent"))
            except CommerceError:
                db.rollback()
                return False
            db.commit()  # persist the sliding-window refresh
            return True
        finally:
            db.close()

    def _is_buyer(checkout: Checkout, buyer_key: str | None) -> bool:
        if not buyer_key or not _BUYER_KEY_RE.match(buyer_key):
            return False
        return (
            checkout.chat_platform == _WEB_PLATFORM and checkout.chat_user_id == buyer_key
        )

    def _is_share(checkout: Checkout, token: str | None) -> bool:
        if not token or not checkout.evidence_token_hash:
            return False
        if checkout.evidence_token_expires_at is None:
            return False
        now = datetime.now(UTC).replace(tzinfo=None)
        if now >= checkout.evidence_token_expires_at:
            return False
        return hmac.compare_digest(checkout.evidence_token_hash, hash_token(token))

    def _note_failure(ip: str, checkout_id: str) -> None:
        now = time.monotonic()
        key = (ip, checkout_id)
        recent = [t for t in _failures.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
        recent.append(now)
        _failures[key] = recent
        if len(recent) >= _FAILURE_ALERT_THRESHOLD and now - _alerted.get(key, 0) > _FAILURE_WINDOW_SECONDS:
            _alerted[key] = now
            sync_alert(
                "evidence_share_bruteforce",
                f"{len(recent)} failed share-token attempts for {checkout_id} from {ip}",
                {"checkout_id": checkout_id, "ip": ip, "attempts": len(recent)},
            )

    def _get_checkout(db: Session, checkout_id: str) -> Checkout:
        checkout = db.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        if checkout is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "checkout.not_found",
                    "message": f"Checkout {checkout_id} not found",
                },
            )
        return checkout

    def _authorize(
        request: Request,
        checkout: Checkout,
        t: str | None,
        buyer_key: str | None,
    ) -> None:
        """Q-045 gate. Either credential grants; failures are explicit about
        which credential failed but never reveal whether the other would pass."""
        if _is_merchant(request):
            return
        if buyer_key is not None and _is_buyer(checkout, buyer_key):
            return
        if t is not None and _is_share(checkout, t):
            return
        if t is not None:
            # No oracle: a wrong/expired/revoked token is indistinguishable
            # from a never-issued one — but the MESSAGE is explicit (R0.5).
            _note_failure(_client_ip(request), checkout.id)
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "checkout.evidence_not_found",
                    "message": "share link invalid, expired, or revoked",
                },
            )
        if buyer_key is not None:
            raise HTTPException(
                status_code=403,
                detail={
                    "reason_code": "checkout.not_owned",
                    "message": "This checkout does not belong to the calling identity",
                },
            )
        raise HTTPException(
            status_code=401,
            detail={
                "reason_code": "auth.session_required",
                "message": "merchant sign-in, buyer key, or share token required",
            },
        )

    @router.get("/orders/{checkout_id}/evidence")
    async def get_evidence(
        checkout_id: str,
        request: Request,
        t: str | None = Query(default=None),
        buyer_key: str | None = Query(default=None),
    ) -> dict[str, Any]:
        session = make_session()
        try:
            checkout = _get_checkout(session, checkout_id)
            _authorize(request, checkout, t, buyer_key)
            if checkout.poai_bundle is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "reason_code": "checkout.evidence_not_found",
                        "message": f"No evidence bundle for {checkout_id}",
                    },
                )
            return checkout.poai_bundle
        finally:
            session.close()

    # REGISTRY names this param `id` (/orders/<id>/evidence/view), distinct
    # from the JSON route's `checkout_id` — both identify the same Checkout.
    @router.get("/orders/{id}/evidence/view", response_class=HTMLResponse)
    async def view_evidence(
        id: str,
        request: Request,
        t: str | None = Query(default=None),
        buyer_key: str | None = Query(default=None),
    ) -> HTMLResponse:
        checkout_id = id
        session = make_session()
        try:
            checkout = _get_checkout(session, checkout_id)
            _authorize(request, checkout, t, buyer_key)
        finally:
            session.close()

        html = (TEMPLATES / "evidence_viewer.html").read_text(encoding="utf-8")
        # Auto-load: fetch this checkout's bundle and render it, so the /view
        # link works standalone without the buyer manually picking a file.
        # The ?t=/buyer_key credentials ride along (same-origin, no CORS).
        # The failure handler renders an EXPLICIT state — once this route is
        # gated, a silent blank page would be an R0.5 violation (Q-045).
        # The template's own manual file-upload path (loadFile()) still works
        # unchanged for an offline bundle.
        autoload = (
            "\n<script>\n"
            "(async function() {\n"
            "  const params = new URLSearchParams(location.search);\n"
            '  const q = params.toString() ? "?" + params.toString() : "";\n'
            "  try {\n"
            '    const res = await fetch("/orders/' + checkout_id + '/evidence" + q);\n'
            "    if (!res.ok) {\n"
            '      const box = document.createElement("div");\n'
            '      box.className = "panel";\n'
            '      let msg = "This receipt link is not valid.";\n'
            "      if (res.status === 401) msg = \"Sign in as the merchant, "
            "or open this receipt with its buyer key or share link.\";\n"
            "      else if (res.status === 403) msg = \"This receipt does not "
            'belong to the supplied buyer key.";\n'
            "      else if (res.status === 404) msg = \"This receipt link has "
            'expired, was revoked, or never existed.";\n'
            '      box.innerHTML = "<h2>Receipt unavailable</h2><p>" + msg + "</p>";\n'
            "      document.body.prepend(box);\n"
            "      return;\n"
            "    }\n"
            "    const bundle = await res.json();\n"
            "    await renderBundle(bundle);\n"
            '  } catch (e) { const box = document.createElement("div");\n'
            '    box.className = "panel";\n'
            '    box.innerHTML = "<h2>Receipt unavailable</h2><p>Could not load '
            'this receipt (network error). You can still verify an offline copy '
            'with the file picker below.</p>";\n'
            "    document.body.prepend(box); }\n"
            "})();\n"
            "</script>\n"
        )
        html = html.replace("</body>", autoload + "</body>")
        return HTMLResponse(html)

    return router

