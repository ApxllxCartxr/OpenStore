# tests/conftest.py
# Shared fixtures for money-path unit tests.

from __future__ import annotations

import openstore.core.database as _database_module
import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import get_session, init_database


def pytest_configure(config: pytest.Config) -> None:
    # openstore.core.database caches a global engine singleton. It's shared
    # across tests within one pytest session by design (see test_spend_cap.py),
    # but a fresh session must start with a fresh in-memory DB — otherwise a
    # second pytest.main() call in the same process (e.g. mutmut's stats vs.
    # clean-run passes) reuses stale state and hits stale-id collisions.
    _database_module._engine = None


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant"),
        razorpay=RazorpayConfig(key_id="rzp_test", key_secret="secret"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OpenStore", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session(settings: Settings):
    init_database(settings)
    s = get_session(settings)
    yield s
    s.close()
