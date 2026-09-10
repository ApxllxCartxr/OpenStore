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
    # Optional: a single guild channel where the buyer bot treats any message
    # as a shop request, no "!shop " prefix needed (S14). DMs already accept
    # free text; this widens it to exactly one designated channel, not every
    # channel, to keep LLM cost/false-trigger risk bounded.
    shopping_channel_id: int | None = None
    # S11->S12: the buyer agent is moving into its own process. Merchant
    # configs sharing one bot_token (e.g. two merchants on the same Discord
    # app) would otherwise start two BuyerBot logins on that token, each
    # receiving and replying to the same DM. Default True preserves today's
    # single-process demo behavior for every existing config/test.
    buyer_bot_enabled: bool = True


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
    # DECISION-034: autonomous growth-trigger loop. A SKU counts as "stalled"
    # once it has at least stall_min_units_30d sales in the last 30 days but
    # zero in the last 7 — real, deterministic decline, not an LLM guess.
    # auto_trigger_cooldown_hours bounds how often the loop may draft a new
    # auto-triggered campaign for the same merchant, regardless of how many
    # stalls it finds (R0.5: bounded, no runaway draft spam).
    stall_min_units_30d: int = 3
    auto_trigger_cooldown_hours: int = 24
    growth_check_interval_seconds: int = 3600


class Settings(BaseSettings):
    # No env_file here on purpose. from_yaml() already calls load_dotenv() and
    # resolves ${VAR} itself, so a dotenv settings source is redundant — and
    # with extra="forbid" it is actively harmful: pydantic-settings feeds EVERY
    # .env key into validation, so any key that isn't a field path (the buyer
    # process's own credentials, say) fails every Settings construction in the
    # repo. extra="forbid" stays: it is what catches typo'd YAML keys (R0.3).
    model_config = SettingsConfigDict(
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
        settings = cls.model_validate(data)
        # Production override: model_validate does not consult the environment
        # (only the Settings() constructor does), so the nested-delimiter env
        # var would otherwise be silently ignored and the container would
        # migrate/boot against the YAML's SQLite default. Fail loud on empty.
        db_url = os.environ.get("DATABASE__URL")
        if db_url is not None:
            if not db_url.strip():
                raise ValueError("DATABASE__URL is set but empty")
            settings.database.url = db_url
        return settings


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
    # An explicit catalog_path wins, resolved relative to the config file so a
    # second merchant can live beside the first without its own directory.
    # Without this, every config in a directory shares one catalog.yaml — two
    # merchants in the same repo silently served identical catalogs.
    if settings.catalog_path:
        settings.catalog_path = str(Path(config_path).parent / settings.catalog_path)
    else:
        settings.catalog_path = str(Path(config_path).parent / "catalog.yaml")
    return settings
