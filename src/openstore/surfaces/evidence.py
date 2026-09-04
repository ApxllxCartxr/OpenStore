# OpenStore — Evidence surface (S11 Phase 4 / plan items #20-22).
#
# Routes (REGISTRY.json, pre-registered by an earlier stage but never
# mounted until now):
#   GET /orders/{checkout_id}/evidence       -> the persisted PoAI bundle (JSON)
#   GET /orders/{checkout_id}/evidence/view  -> renders templates/evidence_viewer.html
#
# The bundle itself is produced in psp/router.py when a chat-originated
# checkout reaches RELEASED (webhook path) and persisted on
# Checkout.poai_bundle (Q-020). This module only serves it back.
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.database import get_session
from openstore.models import Checkout

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

    @router.get("/orders/{checkout_id}/evidence")
    async def get_evidence(checkout_id: str) -> dict[str, Any]:
        session = make_session()
        try:
            checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
            if checkout is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "reason_code": "checkout.not_found",
                        "message": f"Checkout {checkout_id} not found",
                    },
                )
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
    async def view_evidence(id: str) -> HTMLResponse:
        checkout_id = id
        session = make_session()
        try:
            checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
            if checkout is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "reason_code": "checkout.not_found",
                        "message": f"Checkout {checkout_id} not found",
                    },
                )
        finally:
            session.close()

        html = (TEMPLATES / "evidence_viewer.html").read_text(encoding="utf-8")
        # Auto-load: fetch this checkout's bundle and render it, so the /view
        # link works standalone without the buyer manually picking a file.
        # The template's own manual file-upload path (loadFile()) still works
        # unchanged for an offline bundle.
        autoload = f"""
<script>
(async function() {{
  try {{
    const res = await fetch("/orders/{checkout_id}/evidence");
    if (!res.ok) return;
    const bundle = await res.json();
    await renderBundle(bundle);
  }} catch (e) {{ /* leave the manual upload path available */ }}
}})();
</script>
""".strip()
        html = html.replace("</body>", autoload + "\n</body>")
        return HTMLResponse(html)

    return router
