# OpenStore — buyer-side configuration (S12/S12 step 7).
# Holds the buyer agent's view of the merchants it can talk to over HTTP MCP.
# Kept separate from config.py (merchant-side Settings) because a buyer install
# has no merchant config of its own — just a list of origins to call out to.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from openstore.config import DatabaseConfig, DiscordConfig, LLMSettings, Settings, _interpolate_env
from openstore.config import merchant_id as _slug_merchant_id


class MerchantOrigin(BaseModel):
    """One merchant reachable over HTTP MCP: where it lives and how to auth."""

    name: str
    base_url: str
    client_id: str
    client_secret: str

    @property
    def merchant_id(self) -> str:
        """Canonical merchant_id slug — MUST match config.merchant_id's slugging
        exactly (policy lookups are keyed by this slug), so this reuses that
        function via a duck-typed Settings stand-in rather than re-deriving
        the slug rule here (a past bug came from exactly that duplication)."""
        stand_in = SimpleNamespace(merchant=SimpleNamespace(name=self.name))
        return _slug_merchant_id(cast(Settings, stand_in))


class BuyerSettings(BaseSettings):
    """S12 step 7: the buyer process's own config — one Discord bot token
    (the buyer owns the one real bot login; merchant configs set
    discord.buyer_bot_enabled=false), its own DB, and the list of merchant
    origins it's allowed to shop at. Mirrors config.py's Settings: same
    ${VAR} env interpolation, same extra="forbid" (a typo'd key must fail
    loud, not silently be ignored, R0.3)."""

    # No env_file here (unlike config.py's Settings): pydantic-settings'
    # DotEnvSettingsSource dumps every key it finds in .env unconditionally
    # into the validated payload, extra="forbid" and all — a shared repo-root
    # .env carrying merchant-only keys (RAZORPAY_KEY_ID, ...) would then fail
    # BuyerSettings validation for keys it never declared. Value substitution
    # for ${VAR} placeholders is done explicitly by _interpolate_env below
    # (via load_dotenv() populating os.environ), so no automatic ingestion is
    # needed — only intentional, YAML-declared references get through.
    model_config = SettingsConfigDict(
        extra="forbid",
        env_nested_delimiter="__",
    )

    discord: DiscordConfig
    database: DatabaseConfig = Field(
        default_factory=lambda: DatabaseConfig(url="sqlite:///buyer.db")
    )
    llm: LLMSettings = Field(default_factory=LLMSettings)
    merchants: list[MerchantOrigin]

    # S23 (Q-043): consolidated budget guardrail — the operator-declared
    # total, in paise, bounding one federated checkout across ALL merchants
    # (per-policy exposures + this cart's pending totals, checked after Phase
    # 1 validates and before any Phase 2 commit). None (default) disables the
    # guardrail entirely: each merchant's own signed caps still hold, there is
    # simply no cross-merchant total. Must be a positive integer when set —
    # validated in __post_init__ style below (fail loud, R0.5).
    federation_total_cap_minor: int | None = None

    @model_validator(mode="after")
    def _validate_budget_cap(self) -> BuyerSettings:
        cap = self.federation_total_cap_minor
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0):
            raise ValueError("federation_total_cap_minor must be a positive integer or unset")
        return self

    # S12 step 8: this buyer process's own internal HTTP surface
    # (surfaces/buyer_internal.py), which receives a merchant's best-effort
    # "some buyer may have finished signing" ping and re-verifies before
    # acting. Bound to loopback only — a merchant reaches it only if it runs
    # on the same host, which is the deployment this defaults for.
    resume_port: int = 8765
    # None (default) means "compute from resume_port" (see
    # signing_complete_url below). An explicit "" disables the feature
    # entirely: no resume_url is ever sent to a merchant, preserving the
    # pre-step-8 behavior of no auto-resume for federated buyers.
    callback_base_url: str | None = None

    def signing_complete_url(self) -> str | None:
        """The URL this process listens on for a merchant's signing-complete
        ping, or None if the operator explicitly disabled it
        (callback_base_url=""). Defaults to http://127.0.0.1:<resume_port> —
        matches where the internal FastAPI surface actually binds
        (buyer_cli.py's serve())."""
        base = (
            self.callback_base_url
            if self.callback_base_url is not None
            else f"http://127.0.0.1:{self.resume_port}"
        )
        if not base:
            return None
        return f"{base.rstrip('/')}/internal/signing-complete"

    @classmethod
    def from_yaml(cls, path: str | Path) -> BuyerSettings:
        import os

        load_dotenv()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        data = _interpolate_env(data)
        settings = cls.model_validate(data)
        db_url = os.environ.get("DATABASE__URL")
        if db_url is not None:
            if not db_url.strip():
                raise ValueError("DATABASE__URL is set but empty")
            settings.database.url = db_url
        return settings


def load_buyer_config(path: str | Path, *, verify_origins: bool = True) -> BuyerSettings:
    """Load the buyer's own config and, unless verify_origins is False (tests,
    offline startup), fail loud if any configured merchant's live manifest
    doesn't match the whitelist entry (R0.5) — a typo'd base_url or a
    merchant_id drift must never result in silently shopping the wrong
    store."""
    config = BuyerSettings.from_yaml(path)
    if not verify_origins:
        return config

    for origin in config.merchants:
        manifest_url = f"{origin.base_url}/.well-known/agent-commerce.json"
        try:
            resp = httpx.get(manifest_url, timeout=8.0)
            resp.raise_for_status()
            manifest = resp.json()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"buyer config: could not fetch manifest for merchant {origin.name!r} "
                f"at {manifest_url}: {e}"
            ) from e

        manifest_id = manifest.get("merchant", {}).get("id")
        if manifest_id != origin.merchant_id:
            raise RuntimeError(
                f"buyer config: merchant {origin.name!r} manifest id "
                f"{manifest_id!r} does not match expected {origin.merchant_id!r} "
                f"(base_url={origin.base_url!r}) — refusing to shop there"
            )

        expected_mcp_endpoint = f"{origin.base_url}/agent/mcp"
        manifest_mcp_endpoint = manifest.get("mcp_endpoint")
        if manifest_mcp_endpoint != expected_mcp_endpoint:
            raise RuntimeError(
                f"buyer config: merchant {origin.name!r} manifest mcp_endpoint "
                f"{manifest_mcp_endpoint!r} does not match expected "
                f"{expected_mcp_endpoint!r} — refusing to shop there"
            )

    return config
