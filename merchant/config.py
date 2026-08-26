from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    discord_webhook_buyer_agent: str
    discord_webhook_merchant_agent: str
    discord_webhook_merchant_server: str
    discord_webhook_audit_trail: str
    database_url: str = "sqlite:///./openstore.db"
    merchant_config_path: str = "config/gelateria.yaml"

    class Config:
        env_file = ".env"

settings = Settings()