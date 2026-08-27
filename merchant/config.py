import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    discord_webhook_buyer_agent: str
    discord_webhook_merchant_agent: str
    discord_webhook_merchant_server: str
    discord_webhook_audit_trail: str
    database_url: str = "sqlite:///./openstore.db"
    merchant_config_path: str = "config/gelateria.yaml"
    approver_discord_user_id: str = "0"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    discord_bot_token: str = ""
    discord_buyer_channel_id: str = ""
    intent_signing_user_id: str = "default-user"
    gemini_api_key: str = ""

    @property
    def merchant_id(self) -> str:
        with open(self.merchant_config_path) as f:
            raw = yaml.safe_load(f)
        return raw["merchant"]["id"]

    class Config:
        env_file = ".env"


settings = Settings()