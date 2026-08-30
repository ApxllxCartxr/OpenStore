from typing import Dict

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENCOMMERCE_")

    merchant_key_path: str = "openstore_merchant_key.pem"
    anchor_secret: str = "change-me-anchor"
    catalog: Dict[str, dict] = {
        "SKU1": {"title": "Latte", "blocked": False},
        "SKU2": {"title": "Croissant", "blocked": False},
    }
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Ledger DB URL. Defaults to in-memory; set to a file/sqlite URL for a
    # persistent DB (required to seed PspIntents that a live webhook can match).
    ledger_url: str = Field(
        default="sqlite:///:memory:",
        validation_alias=AliasChoices("DATABASE_URL", "OPENCOMMERCE_LEDGER_URL"),
    )

    # Discord tracing webhooks (buyer agent + merchant reasoning agent).
    # Accept both the OPENCOMMERCE_-prefixed name and the bare DISCORD_WEBHOOK_*
    # name from the legacy .env layout so existing deployments keep working.
    discord_webhook_buyer_agent: str = Field(
        default="",
        validation_alias=AliasChoices("DISCORD_WEBHOOK_BUYER_AGENT", "OPENCOMMERCE_DISCORD_WEBHOOK_BUYER_AGENT"),
    )
    discord_webhook_merchant_agent: str = Field(
        default="",
        validation_alias=AliasChoices("DISCORD_WEBHOOK_MERCHANT_AGENT", "OPENCOMMERCE_DISCORD_WEBHOOK_MERCHANT_AGENT"),
    )
    discord_webhook_merchant_server: str = Field(
        default="",
        validation_alias=AliasChoices("DISCORD_WEBHOOK_MERCHANT_SERVER", "OPENCOMMERCE_DISCORD_WEBHOOK_MERCHANT_SERVER"),
    )
    discord_webhook_audit_trail: str = Field(
        default="",
        validation_alias=AliasChoices("DISCORD_WEBHOOK_AUDIT_TRAIL", "OPENCOMMERCE_DISCORD_WEBHOOK_AUDIT_TRAIL"),
    )


settings = Settings()
