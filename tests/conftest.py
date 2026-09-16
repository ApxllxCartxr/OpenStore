# tests/conftest.py
# Shared fixtures for money-path unit tests.

from __future__ import annotations

import openstore.agents.llm as _llm_module  # noqa: F401  (imported for its side effect)
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
    _database_module._engine_url = None


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    """The test suite must never depend on (or accidentally hit) a
    developer's real LLM keys/chain from .env.llm. agents/llm.py loads
    .env.llm at import time, so LLM_PROVIDER_CHAIN can be sitting in
    os.environ for the whole pytest session regardless of what an individual
    test's LLM_PROVIDER monkeypatch says — create_llm() checks the chain
    first. Clearing it here (autouse, every test) restores the "dummy by
    default" isolation; a test that wants to exercise the chain sets
    LLM_PROVIDER_CHAIN itself via monkeypatch.

    This only holds because conftest imports openstore.agents.llm above: the
    module's import-time load_dotenv runs once, at collection, BEFORE any
    fixture. Without that import the first test to pull the module in re-set
    LLM_PROVIDER_CHAIN after this fixture had cleared it, and the suite made
    real network calls to whatever chain sat in .env.llm."""
    monkeypatch.delenv("LLM_PROVIDER_CHAIN", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "dummy")


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


# ---------------------------------------------------------------------------
# Campaign approval ceremony (DECISION-024)
#
# activate_campaign runs the real WebAuthn RP with binding
# {"mode": "campaign", "campaign_id": ...}, so a stub assertion dict no longer
# publishes anything. These are fixtures rather than module-level helpers so
# every test directory can reach them without a third copy.
# ---------------------------------------------------------------------------
CAMPAIGN_APPROVER_HANDLE = "gelateria-milano"


@pytest.fixture()
def enrol_approver():
    """Factory: (session, config, user_handle=...) -> enrolled VirtualAuthenticator."""

    def _enrol(session, config, user_handle: str = CAMPAIGN_APPROVER_HANDLE):
        from cryptography.hazmat.primitives.asymmetric import ec
        from openstore.core.webauthn_rp import begin_registration, complete_registration
        from openstore.devtools.virtual_authenticator import VirtualAuthenticator, b64u_raw

        va = VirtualAuthenticator(
            credential_id=b"K" * 32,
            key=ec.generate_private_key(ec.SECP256R1()),
            rp_id=config.webauthn.rp_id,
            origin=config.webauthn.origin,
            sign_count=1,
        )
        options = begin_registration(config, user_handle, "approver", "Approver")
        reg = va.register(b64u_raw(options["challenge"]))
        complete_registration(
            session=session,
            config=config,
            user_handle=user_handle,
            credential_id=reg.credential_id,
            client_data_json=reg.client_data_json,
            attestation_object=reg.attestation_object,
            challenge_b64url=options["challenge"],
        )
        session.commit()
        return va

    return _enrol


@pytest.fixture()
def approve_campaign():
    """Factory: run a genuine approval ceremony.

    `bind_to` issues the challenge for a different campaign than the one being
    approved, which is how the replay test proves the binding actually binds.
    """

    def _approve(
        session,
        config,
        va,
        campaign_id: str,
        *,
        sign_count: int = 2,
        bind_to: str | None = None,
        user_handle: str = CAMPAIGN_APPROVER_HANDLE,
    ):
        from openstore.core.campaigns import activate_campaign
        from openstore.core.webauthn_rp import begin_assertion
        from openstore.devtools.virtual_authenticator import b64u_raw

        begin = begin_assertion(
            config,
            user_handle,
            binding={"mode": "campaign", "campaign_id": bind_to or campaign_id},
        )
        asr = va.assert_credential(b64u_raw(begin["challenge"]), sign_count=sign_count)
        return activate_campaign(
            session,
            campaign_id,
            approver_credential_id=asr.credential_id,
            webauthn_assertion={
                "credential_id": asr.credential_id,
                "client_data_json": asr.client_data_json,
                "authenticator_data": asr.authenticator_data,
                "signature": asr.signature,
                "challenge": begin["challenge"],
            },
            config=config,
            user_handle=user_handle,
        )

    return _approve


@pytest.fixture
def demo_client(tmp_path):
    """A demo-mode app on its own sqlite file (Q-050 / demo tests).

    The database engine is a process-global singleton, so it is swapped out
    and restored around the test; demo_surface.reset() drops the virtual
    authenticator and the in-memory payment links between sittings.
    """
    from demo_harness import write_config
    from fastapi.testclient import TestClient
    from openstore.core.database import apply_migrations, get_engine
    from openstore.server import create_app
    from openstore.surfaces import demo as demo_surface

    demo_surface.reset()
    prev = _database_module._engine
    _database_module._engine = None
    config = write_config(tmp_path)
    apply_migrations(config)
    try:
        with TestClient(create_app(config)) as client:
            yield client
    finally:
        get_engine(config).dispose()
        _database_module._engine = prev
        demo_surface.reset()
