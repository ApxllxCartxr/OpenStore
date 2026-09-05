# OpenStore — buyer-side enrollment aggregator page (S12 UX polish).
#
# A federated cart can surface signing links for several unenrolled merchants
# in the same turn (buyer_agent.py's _submit_federated_cart). Posting N raw
# studio URLs into chat works but is tedious to click through one at a time.
# This page — hosted by the buyer process itself, same as
# surfaces/buyer_internal.py — just lists them together with live status.
#
# NO SIGNING AUTHORITY LIVES HERE (DECISION-022: no signing hub). Every "Sign
# with passkey" button still sends the browser to that merchant's own
# /intent/studio origin, where the WebAuthn ceremony happens against that
# merchant's own RP and DB exactly as before. This page never sees a
# credential, a challenge, or a policy — it only ever (a) reads back the
# static entries BuyerAgent recorded when it minted the handoffs, and (b)
# independently calls resolve_policy at each merchant to report signed/not,
# same trust rule as buyer_internal.py's signing-complete handler.

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from openstore.agents.buyer_agent import BuyerAgent
from openstore.buyer_config import BuyerSettings

TEMPLATES = Path(__file__).resolve().parent / "templates"


def create_buyer_studio_router(config: BuyerSettings, agent: BuyerAgent) -> APIRouter:
    router = APIRouter()

    @router.get("/enroll/{group_id}", response_class=HTMLResponse)
    async def enroll_page(group_id: str) -> HTMLResponse:
        group = agent.get_enrollment_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="unknown or expired enrollment link")
        html = (TEMPLATES / "enrollment_studio.html").read_text(encoding="utf-8")
        html = html.replace("__GROUP_ID__", _safe_json(group_id))
        html = html.replace("__ENTRIES_JSON__", _safe_json(group["entries"]))
        return HTMLResponse(html)

    @router.get("/enroll/{group_id}/status")
    async def enroll_status(group_id: str) -> dict[str, Any]:
        group = agent.get_enrollment_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="unknown or expired enrollment link")
        user_id = group["user_id"]

        async def _check(entry: dict[str, str]) -> tuple[str, bool]:
            client = agent.mcp.client_for(entry["merchant_id"])
            result = await client.call("resolve_policy", {"user_id": user_id}, require_auth=True)
            return entry["merchant_id"], bool(result.get("success"))

        checks = await asyncio.gather(*(_check(e) for e in group["entries"]))
        return {"signed": dict(checks)}

    return router


def _safe_json(value: Any) -> str:
    return json.dumps(value)
