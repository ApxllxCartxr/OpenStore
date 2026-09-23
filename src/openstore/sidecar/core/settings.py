"""Configuration, read once at boot and validated loudly.

Every variable here is named in `.env.example` and nowhere else (§10
Environment). There are no defaults for anything that carries a secret or
decides an origin: a config that silently falls back is a config that boots
wrong in production and looks fine.

Two boot refusals live here because they cannot live anywhere later:

- Live provider keys in demo mode.
- Live provider keys alongside a non-empty dev profile allowlist (ADR-0012).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootRefused(RuntimeError):
    """Raised instead of booting. Never caught inside the sidecar — the process
    is supposed to die, visibly, with the reason on stderr."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `ignore`, deliberately. The compose demo shares one `.env` across the
        # sidecar, the store and the chat (§10), so a variable this process does
        # not declare is usually another service's, not a typo. Typo detection
        # is not dropped — it moves to CI, where `test_env_example_and_settings_agree`
        # diffs `.env.example` against these fields in both directions and fails
        # the build on either half. A boot-time refusal here would mean the
        # sidecar dies because the chat added a variable.
        extra="ignore",
        case_sensitive=False,
    )

    # Identity and origin. The RP ID decides which passkeys exist (ADR-0008),
    # so it is required rather than derived from a request header.
    openstore_merchant_domain: str = ""
    openstore_public_origin: str = ""
    webauthn_rp_id: str = ""
    webauthn_rp_name: str = ""

    # What this shop sells, for the discovery card (ADR-0027). Declared by the
    # Merchant, never derived: the card is the one document that must answer
    # even when the catalogue service is down, and a summary computed from the
    # trait would make discovery fail whenever stock did.
    openstore_merchant_description: str = ""
    openstore_merchant_categories: str = ""

    # Service wiring (§16.1).
    sidecar_port: int = 8000
    store_port: int = 3000
    store_internal_url: str = ""

    # Databases — two, never shared (SPEC §5).
    sidecar_database_url: str = ""
    merchant_database_url: str = ""

    # The 9-door trait. `trait_base_url` is the Merchant's ORIGIN — the client
    # appends `/trait/<door>` itself.
    trait_base_url: str = ""
    trait_hmac_secret: str = ""

    # Keys (ADR-0014, ADR-0011).
    sidecar_signing_key_path: str = ""
    sidecar_signing_key_passphrase: str = ""
    sidecar_key_export_path: str = ""
    deploy_pseudonym_key: str = ""

    # Provider (ADR-0013).
    payment_provider: str = "fake"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    provider_webhook_secret: str = ""
    """The webhook secret for every adapter that is not Razorpay. Two names
    rather than one shared value, because Razorpay's is issued by Razorpay's own
    dashboard and a deploy that pasted it into a generic variable would verify
    callbacks with a secret the Provider never agreed to. Unset means **every**
    webhook is refused — there is no unauthenticated callback path."""

    # Admission (ADR-0012).
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    openstore_dev_profile_hosts: str = Field(
        default="",
        description=(
            "THE SSRF EXCEPTION. Comma-separated host[:port], never a CIDR. "
            "Permits plain http for exactly these entries (§10.1) because the "
            "compose demo's own chat is reachable only as http://buyer-chat:3001 "
            "and HTTPS-only hardening would refuse the demo's own registration."
        ),
    )

    # Demo.
    openstore_demo_mode: bool = True
    admin_seed_password: str = ""

    @property
    def dev_profile_hosts(self) -> tuple[str, ...]:
        return tuple(h.strip() for h in self.openstore_dev_profile_hosts.split(",") if h.strip())

    @property
    def merchant_categories(self) -> tuple[str, ...]:
        """Lowercased and de-duplicated, order kept. An agent comparing
        `Kitchenware` against `kitchenware` and deciding they are different
        shops' worth of difference is a bug nobody would find."""
        out: list[str] = []
        for raw in self.openstore_merchant_categories.split(","):
            value = raw.strip().lower()
            if value and value not in out:
                out.append(value)
        return tuple(out)

    @property
    def webhook_secret(self) -> str:
        """This deploy's callback secret, chosen by adapter and never guessed."""
        if self.payment_provider == "razorpay":
            return self.razorpay_webhook_secret
        return self.provider_webhook_secret

    @property
    def has_live_provider_keys(self) -> bool:
        """A Razorpay key is live unless it is explicitly test-marked. Unknown
        shapes count as live: guessing 'probably a test key' is how a real charge
        happens in a demo."""
        if not self.razorpay_key_id:
            return False
        return not self.razorpay_key_id.startswith("rzp_test_")

    @model_validator(mode="after")
    def _refuse_dangerous_combinations(self) -> Settings:
        if self.has_live_provider_keys and self.openstore_demo_mode:
            raise BootRefused(
                "live provider keys with OPENSTORE_DEMO_MODE=true: the demo marks every "
                "receipt as demo and must never be able to move real money. Use an "
                "rzp_test_ key, or turn demo mode off deliberately."
            )
        if self.has_live_provider_keys and self.dev_profile_hosts:
            raise BootRefused(
                "live provider keys with a non-empty OPENSTORE_DEV_PROFILE_HOSTS "
                f"({', '.join(self.dev_profile_hosts)}): the dev allowlist is an SSRF "
                "exception for the compose demo and must never be reachable from a "
                "deployment that can take money (ADR-0012)."
            )
        if self.trait_base_url.rstrip("/").endswith("/trait"):
            raise BootRefused(
                f"TRAIT_BASE_URL is {self.trait_base_url!r}, which already ends in /trait. "
                f"It is the Merchant's origin; the client appends the door path itself, "
                f"and this would produce /trait/trait/<door> — every call refused, with "
                f"nothing in the logs saying why."
            )
        if self.trait_base_url and not self.trait_hmac_secret:
            raise BootRefused(
                "TRAIT_BASE_URL is set and TRAIT_HMAC_SECRET is not. Both sides of the "
                "trait need the same secret; without it every door refuses and the shop "
                "looks empty rather than broken."
            )

        for host in self.dev_profile_hosts:
            if "/" in host:
                raise BootRefused(
                    f"OPENSTORE_DEV_PROFILE_HOSTS entry {host!r} looks like a CIDR. "
                    "Named host[:port] entries only — a range is not an exception, it is "
                    "a hole."
                )
            if host.split(":")[0] == "169.254.169.254":
                raise BootRefused(
                    "the cloud metadata address is refused unconditionally, dev allowlist included."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read once. The cache is what makes 'validated at boot' true rather than
    'validated on whichever request happens to construct it first'."""
    return Settings()
