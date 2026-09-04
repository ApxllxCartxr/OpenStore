# OpenStore configuration loader
# Fails loud on unknown keys (R0.3)

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")


def _interpolate_env(value: Any) -> Any:
    """Replace whole-string ${VAR_NAME} placeholders with their env var value.

    Fails loud (R0.5): an undefined placeholder is a hard error, never a
    silent blank or a literal "${...}" leaking into config.
    """
    if isinstance(value, str):
        match = _ENV_VAR_PATTERN.match(value)
        if match is None:
            return value
        var_name = match.group(1)
        if var_name not in os.environ:
            raise ValueError(f"Config references undefined environment variable: {var_name}")
        return os.environ[var_name]
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


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
    # SID-1: public origin (scheme+host) the sidecar is reachable at, used for
    # manifests and CORS/RP binding. None => same-origin reverse proxy (derive
    # from request). Subdomain deployments MUST set this explicitly.
    public_base_url: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> Settings:
        load_dotenv()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        data = _interpolate_env(data)
        return cls.model_validate(data)


def merchant_id(config: Settings) -> str:
    """Canonical merchant_id slug derived from the merchant name.

    Single source of truth: checkout looks policies up by (policy_id,
    merchant_id), so the slug the studio signs into an IntentPolicy and the
    slug the buyer agent sends to create_cart MUST be byte-identical. This
    was previously duplicated in four modules, one of which omitted the
    apostrophe strip — a merchant named "Joe's Gelato" signed "joes-gelato"
    but shopped as "joe's-gelato", yielding policy_not_found at checkout.
    """
    return config.merchant.name.lower().replace(" ", "-").replace("'", "")


def load_config(config_path: str | Path) -> Settings:
    """Load configuration from YAML file and environment variables.

    Raises:
        ValidationError: If unknown keys are present (R0.3) or required fields missing.
    """
    settings = Settings.from_yaml(config_path)
    # Set catalog_path from the config file's directory
    settings.catalog_path = str(Path(config_path).parent / "catalog.yaml")
    return settings
