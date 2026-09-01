# OpenStore — Discord notifier (S7.6 / §7.6)
# Four channels: #buyer-trace, #merchant-trace, #money-trace, #alerts.
# R0.10: This module holds NO payment keys, PSP credentials, or signing material.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from openstore.config import Settings

logger = logging.getLogger("openstore.notifier")

_discord_client: Any | None = None


def _init_discord(config: Settings) -> Any | None:
    global _discord_client
    if _discord_client is not None:
        return _discord_client
    token = config.discord.bot_token
    if not token or token == "token":
        return None
    try:
        import discord
        client = discord.Client(intents=discord.Intents.default())
        _discord_client = client
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
        self._client = _init_discord(config)
        self._channels = {
            "buyer": config.discord.buyer_trace_channel_id,
            "merchant": config.discord.merchant_trace_channel_id,
            "money": config.discord.money_trace_channel_id,
            "alerts": config.discord.alerts_channel_id,
        }

    async def _send(self, channel: str, message: str, embed: dict[str, Any] | None = None) -> None:
        if self._client is None:
            logger.info(f"[{channel}] {message}")
            return
        channel_id = self._channels.get(channel)
        if not channel_id:
            return
        try:
            for guild in self._client.guilds:
                for ch in guild.channels:
                    if ch.id == channel_id:
                        await ch.send(message, embed=embed)
                        break
        except Exception as e:
            logger.warning(f"Discord send error: {e}")

    async def buyer_trace(self, trace_id: str, action: str, details: dict[str, Any]) -> None:
        await self._send("buyer", f"[{trace_id}] {action}", embed={
            "title": action,
            "fields": [{"name": k, "value": str(v), "inline": True} for k, v in details.items()],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def merchant_trace(self, trace_id: str, action: str, details: dict[str, Any]) -> None:
        await self._send("merchant", f"[{trace_id}] {action}", embed={
            "title": action,
            "fields": [{"name": k, "value": str(v), "inline": True} for k, v in details.items()],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def money_trace(
        self, trace_id: str, action: str, amount_minor: int, currency: str = "INR"
    ) -> None:
        await self._send("money", f"[{trace_id}] {action}: {amount_minor} {currency}", embed={
            "title": f"{action}: {amount_minor} {currency}",
            "fields": [
                {"name": "trace_id", "value": trace_id, "inline": True},
                {"name": "amount_minor", "value": str(amount_minor), "inline": True},
                {"name": "currency", "value": currency, "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def alert(self, alert_type: str, message: str, details: dict[str, Any]) -> None:
        await self._send("alerts", f"⚠️ [{alert_type}] {message}", embed={
            "title": f"Alert: {alert_type}",
            "description": message,
            "fields": [{"name": k, "value": str(v), "inline": True} for k, v in details.items()],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def sync_money_trace(trace_id: str, action: str, amount_minor: int, currency: str = "INR") -> None:
    """Synchronous money trace (for use in non-async contexts)."""
    logger.info(f"[money-trace][{trace_id}] {action}: {amount_minor} {currency}")


def sync_buyer_trace(trace_id: str, action: str, details: dict[str, Any]) -> None:
    logger.info(f"[buyer-trace][{trace_id}] {action}: {details}")


def sync_merchant_trace(trace_id: str, action: str, details: dict[str, Any]) -> None:
    logger.info(f"[merchant-trace][{trace_id}] {action}: {details}")


def sync_alert(alert_type: str, message: str, details: dict[str, Any]) -> None:
    logger.warning(f"[ALERT][{alert_type}] {message}: {details}")
