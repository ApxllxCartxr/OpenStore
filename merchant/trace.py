import httpx
from datetime import datetime
from merchant.config import settings

CHANNEL_WEBHOOKS = {
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

def emit(channel: str, title: str, fields: dict, trace_id: str, level: str = "info"):
    webhook_url = CHANNEL_WEBHOOKS[channel]
    embed = {
        "title": title,
        "color": LEVEL_COLORS.get(level, LEVEL_COLORS["info"]),
        "fields": [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ],
        "footer": {"text": f"trace_id={trace_id}"},
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        httpx.post(webhook_url, json={"embeds": [embed]}, timeout=5.0)
    except httpx.HTTPError:
        # Tracing must never crash the request it's tracing.
        pass