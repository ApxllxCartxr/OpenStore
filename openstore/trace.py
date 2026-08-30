"""Discord tracing webhooks (mirrors reference/merchant/trace.py).

Four channels:
- ``buyer-agent``    — outbound notifications to the buyer agent
- ``merchant-agent`` — outbound notifications to the merchant reasoning agent
- ``merchant-server``— server-side operational events (the merchant bot)
- ``audit-trail``    — append-only audit log channel

Tracing is best-effort: network failures are swallowed so a trace never
crashes the request it instruments. ``emit`` is async; use ``emit_later``
(fire-and-forget) from a request path to avoid blocking the response.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Dict

import httpx

from openstore.config import settings

CHANNEL_WEBHOOKS: Dict[str, str] = {
    "buyer-agent": settings.discord_webhook_buyer_agent,
    "merchant-agent": settings.discord_webhook_merchant_agent,
    "merchant-server": settings.discord_webhook_merchant_server,
    "audit-trail": settings.discord_webhook_audit_trail,
}

LEVEL_COLORS = {
    "info": 0x3498DB,      # blue
    "gate": 0xF1C40F,      # amber
    "blocked": 0xE74C3C,   # red
    "executed": 0x2ECC71,  # green
}


async def emit(
    channel: str,
    title: str,
    fields: Dict[str, str],
    trace_id: str,
    level: str = "info",
) -> None:
    """POST an embed to a Discord channel webhook. No-ops if the URL is unset."""
    webhook_url = CHANNEL_WEBHOOKS.get(channel)
    if not webhook_url:
        return

    embed = {
        "title": title,
        "color": LEVEL_COLORS.get(level, LEVEL_COLORS["info"]),
        "fields": [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ],
        "footer": {"text": f"trace_id={trace_id}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json={"embeds": [embed]}, timeout=5.0)
    except httpx.HTTPError:
        # Tracing must never crash the request it is tracing.
        pass


def emit_later(
    channel: str,
    title: str,
    fields: Dict[str, str],
    trace_id: str,
    level: str = "info",
) -> None:
    """Fire-and-forget variant for use from an async request path."""
    asyncio.create_task(emit(channel, title, fields, trace_id, level))


def emit_background(
    channel: str,
    title: str,
    fields: Dict[str, str],
    trace_id: str,
    level: str = "info",
) -> None:
    """Fire-and-forget variant for use from a synchronous context.

    Runs the async ``emit`` in a daemon thread with its own event loop so it
    never blocks the calling thread.
    """
    def _run() -> None:
        asyncio.run(emit(channel, title, fields, trace_id, level))

    threading.Thread(target=_run, daemon=True).start()
