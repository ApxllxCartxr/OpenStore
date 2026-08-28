"""Read-only configuration for the merchant reasoning agent.

This process loads NO Razorpay credentials and NO signing key.
The .env file for this process must not contain:
  - RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET
  - MERCHANT_SIGNING_KEY_PATH
If those values are absent from the environment, they structurally
cannot be used by this process — the strongest form of the guarantee.
"""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./openstore.db"
    merchant_config_path: str = "config/gelateria.yaml"
    discord_webhook_merchant_agent: str = ""
    a2a_host: str = "0.0.0.0"
    a2a_port: int = 8001

    class Config:
        env_file = str(Path(__file__).resolve().parent / ".env")


settings = Settings()
