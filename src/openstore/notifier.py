# OpenStore — Discord notifier (S7.6 / §7.6)
# Four channels: #buyer-trace, #merchant-trace, #money-trace, #alerts.
# R0.10: This module holds NO payment keys, PSP credentials, or signing material.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from openstore.config import Settings

logger = logging.getLogger("openstore.notifier")

_discord_client: Any | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def set_discord_client(client: Any | None) -> None:
    """Register the live, logged-in discord.Client owned by server.py's
    lifespan (S11 plan: one client, started once, shared by the command
    handler and this notifier). Called with None on shutdown."""
    global _discord_client
    _discord_client = client


def get_discord_client() -> Any | None:
    return _discord_client


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Register the event loop server.py's lifespan (and the live
    discord.Client's own task) runs on. FastAPI's sync BackgroundTasks run in
    a threadpool worker thread, not on this loop — a worker thread that wants
    to push a DM must hand the coroutine to *this* loop rather than starting
    its own via asyncio.run(), because discord.py's aiohttp session is bound
    to whichever loop the client itself is running on and breaks
    ("Timeout context manager should be used inside a task") when driven from
    a different one. See run_from_worker_thread below."""
    global _main_loop
    _main_loop = loop


def run_from_worker_thread(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async notifier call from a sync BackgroundTasks worker thread.
    Schedules onto the registered main loop (where the live discord.Client
    lives) when one is registered and running; falls back to a fresh
    asyncio.run() otherwise (offline/tests, where no such loop exists to
    conflict with)."""
    loop = _main_loop
    if loop is not None and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return asyncio.run(coro)


def _to_discord_embed(fields: dict[str, Any]) -> Any:
    """Build a real discord.Embed from the plain dict the trace methods
    assemble. discord.py's Messageable.send(embed=...) calls .to_dict() on
    whatever it's given — a raw dict has no such method and raised
    AttributeError on every trace/alert push."""
    import discord

    embed = discord.Embed(
        title=fields.get("title"),
        description=fields.get("description"),
        timestamp=datetime.fromisoformat(fields["timestamp"]) if fields.get("timestamp") else None,
    )
    for f in fields.get("fields", []):
        embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
    return embed


def _init_discord(config: Settings) -> Any | None:
    """Return the shared client if server.py already registered one; otherwise
    construct an unstarted discord.Client purely so tests/offline callers get a
    consistent object shape. Kept offline whenever the token is absent/"token"
    (R0.5: no silent network attempt in tests)."""
    if _discord_client is not None:
        return _discord_client
    token = config.discord.bot_token
    if not token or token == "token":
        return None
    try:
        import discord

        client = discord.Client(intents=discord.Intents.default())
        return client
    except Exception as e:
        logger.warning(f"Discord notifier: could not init: {e}")
        return None


class DiscordNotifier:
    """
    S7.6: Four channels, one per concern.

    - #buyer-trace: buyer agent planning decisions
    - #merchant-trace: merchant agent responses, campaign lifecycle
    - #money-trace: append-only RESERVE/CAPTURE/RELEASE/REFUND with trace_id
    - #alerts: non-zero reconciliation drift, webhook dead-letter, etc.
    """

    def __init__(self, config: Settings):
        self.config = config
        self._channels = {
            "buyer": config.discord.buyer_trace_channel_id,
            "merchant": config.discord.merchant_trace_channel_id,
            "money": config.discord.money_trace_channel_id,
            "alerts": config.discord.alerts_channel_id,
        }

    async def _send(self, channel: str, message: str, embed: dict[str, Any] | None = None) -> None:
        # Look up the shared client live rather than caching it at construction
        # time: server.py's lifespan registers it (via set_discord_client) only
        # after login completes, which can be after a DiscordNotifier already
        # exists — a cached None would then silently drop every message forever.
        client = _init_discord(self.config)
        if client is None:
            logger.info(f"[{channel}] {message}")
            return
        channel_id = self._channels.get(channel)
        if not channel_id:
            return
        try:
            discord_embed = _to_discord_embed(embed) if embed else None
            for guild in client.guilds:
                for ch in guild.channels:
                    if ch.id == channel_id:
                        await ch.send(message, embed=discord_embed)
                        break
        except Exception as e:
            logger.warning(f"Discord send error: {e}")

    async def dm(self, user_id: str, message: str) -> None:
        """Direct-message a chat user by platform id (S11 plan: policy-signing
        and amendment-approval pushes address a user directly, no channel)."""
        await send_dm(self.config, user_id, message)

    async def buyer_trace(self, trace_id: str, action: str, details: dict[str, Any]) -> None:
        await self._send(
            "buyer",
            f"[{trace_id}] {action}",
            embed={
                "title": action,
                "fields": [
                    {"name": k, "value": str(v), "inline": True} for k, v in details.items()
                ],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def merchant_trace(self, trace_id: str, action: str, details: dict[str, Any]) -> None:
        await self._send(
            "merchant",
            f"[{trace_id}] {action}",
            embed={
                "title": action,
                "fields": [
                    {"name": k, "value": str(v), "inline": True} for k, v in details.items()
                ],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def money_trace(
        self, trace_id: str, action: str, amount_minor: int, currency: str = "INR"
    ) -> None:
        await self._send(
            "money",
            f"[{trace_id}] {action}: {amount_minor} {currency}",
            embed={
                "title": f"{action}: {amount_minor} {currency}",
                "fields": [
                    {"name": "trace_id", "value": trace_id, "inline": True},
                    {"name": "amount_minor", "value": str(amount_minor), "inline": True},
                    {"name": "currency", "value": currency, "inline": True},
                ],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def alert(self, alert_type: str, message: str, details: dict[str, Any]) -> None:
        await self._send(
            "alerts",
            f"⚠️ [{alert_type}] {message}",
            embed={
                "title": f"Alert: {alert_type}",
                "description": message,
                "fields": [
                    {"name": k, "value": str(v), "inline": True} for k, v in details.items()
                ],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )


async def send_dm(config: Settings, user_id: str, message: str) -> None:
    """Direct-message a chat user by platform id. Offline (token
    absent/"token") logs instead of sending, matching the four trace-channel
    methods' offline behaviour — tests never touch the network."""
    client = _init_discord(config)
    if client is None:
        logger.info(f"[dm:{user_id}] {message}")
        return
    try:
        user = await client.fetch_user(int(user_id))
        await user.send(message)
    except Exception as e:
        logger.warning(f"Discord DM error to {user_id}: {e}")


def sync_money_trace(trace_id: str, action: str, amount_minor: int, currency: str = "INR") -> None:
    """Synchronous money trace (for use in non-async contexts)."""
    logger.info(f"[money-trace][{trace_id}] {action}: {amount_minor} {currency}")


def sync_buyer_trace(trace_id: str, action: str, details: dict[str, Any]) -> None:
    logger.info(f"[buyer-trace][{trace_id}] {action}: {details}")


def sync_merchant_trace(trace_id: str, action: str, details: dict[str, Any]) -> None:
    logger.info(f"[merchant-trace][{trace_id}] {action}: {details}")


def sync_alert(alert_type: str, message: str, details: dict[str, Any]) -> None:
    logger.warning(f"[ALERT][{alert_type}] {message}: {details}")
