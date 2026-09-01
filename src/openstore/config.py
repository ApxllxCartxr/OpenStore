# OpenStore configuration loader
# Fails loud on unknown keys (R0.3)

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MerchantConfig(BaseModel):
    name: str
    currency: str = "INR"


class RazorpayConfig(BaseModel):
    key_id: str
    key_secret: str
    webhook_secret: str | None = None


class DiscordConfig(BaseModel):
    bot_token: str
    buyer_trace_channel_id: int
    merchant_trace_channel_id: int
    money_trace_channel_id: int
    alerts_channel_id: int


class WebAuthnConfig(BaseModel):
    rp_id: str
    rp_name: str
    origin: str


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///openstore.db"


class LLMSettings(BaseModel):
    model: str = "gpt-4o-mini"
    temperature: float = 0.2


class CampaignSettings(BaseModel):
    min_bps: int = 500
    max_bps: int = 3000
    max_active: int = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        env_nested_delimiter="__",
    )

    merchant: MerchantConfig
    razorpay: RazorpayConfig
    discord: DiscordConfig
    webauthn: WebAuthnConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    campaign: CampaignSettings = Field(default_factory=CampaignSettings)
    catalog_path: str | None = None
    evidence_retention_days: int = 540

    @classmethod
    def from_yaml(cls, path: str | Path) -> Settings:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        # Merge with env vars (pydantic-settings handles this)
        return cls.model_validate(data)


def load_config(config_path: str | Path) -> Settings:
    """Load configuration from YAML file and environment variables.

    Raises:
        ValidationError: If unknown keys are present (R0.3) or required fields missing.
    """
    settings = Settings.from_yaml(config_path)
    # Set catalog_path from the config file's directory
    settings.catalog_path = str(Path(config_path).parent / "catalog.yaml")
    return settings
