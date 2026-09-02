# tests/stage07/test_agents.py
# Stage 7 — Agents: buyer, merchant, negotiation, amendment, narrator

from __future__ import annotations

import pytest
from openstore.agents.buyer_agent import BuyerAgent, compute_cart_hash
from openstore.agents.llm import DummyProvider, LLMProvider
from openstore.agents.merchant_agent import MerchantAgent, NegotiationMessage
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


@pytest.fixture()
def config() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test Merchant", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
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


class FakeMCPClient:
    """Minimal stand-in for InProcessMCPClient in unit tests (no DB, no OAuth)."""

    async def call(self, tool_name: str, arguments: dict) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}

    async def search_products(self, query: str, limit: int = 10) -> dict:
        return {"success": True, "data": {"items": []}, "error": None}

    async def get_order(self, checkout_id: str) -> dict:
        return {"success": False, "data": {}, "error": {"reason_code": "fake.noop"}}


@pytest.fixture()
def mcp_client() -> FakeMCPClient:
    return FakeMCPClient()


class TestBuyerAgent:
    def test_plan_returns_cart(self, config, mcp_client):
        import asyncio

        agent = BuyerAgent(config, mcp_client)
        result = asyncio.run(agent.plan("vanilla gelato", policy_id="p_001", trace_id="trace_001"))
        assert "trace_id" in result
        assert result["trace_id"] == "trace_001"

    def test_agent_holds_no_payment_keys(self, config, mcp_client):
        """R0.10: agent has no Razorpay credentials."""
        agent = BuyerAgent(config, mcp_client)
        # No way to access razorpay.key_secret
        assert not hasattr(agent, "razorpay_key_secret")
        assert not hasattr(agent, "key_secret")
        assert not hasattr(agent, "psp_credentials")

    def test_cart_hash_is_deterministic(self, config):
        cart = [
            {"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000},
            {"sku": "GEL-CHO-500", "qty": 2, "unit_minor": 22000},
        ]
        h1 = compute_cart_hash(cart)
        h2 = compute_cart_hash(cart)
        assert h1 == h2
        assert h1.startswith("sha256:")


class TestMerchantAgent:
    def test_negotiate_returns_counter(self, config):
        agent = MerchantAgent(config)
        cart = [{"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000, "tags": ["vegan"]}]
        result = agent.negotiate(cart, "policy.tag_violation", "trace_001", policy={})
        assert result["from"] == "merchant_agent"
        assert result["state"] == "COUNTERED"
        assert result["reason_code"] == "policy.tag_violation"

    def test_negotiate_unrecoverable_returns_no_compliant_path(self, config):
        agent = MerchantAgent(config)
        result = agent.negotiate([], "policy.no_human_authority", "trace_002", policy={})
        assert result["state"] == "NO_COMPLIANT_PATH"

    def test_draft_amendment_has_required_fields(self, config):
        agent = MerchantAgent(config)
        cart = [{"sku": "GEL-PIST-500", "qty": 1, "unit_minor": 30000, "tags": ["pistachio"]}]
        amendment = agent.draft_amendment(
            base_policy_hash="hash_001",
            reason_code="policy.sku_blocked",
            cart=cart,
            trace_id="trace_001",
        )
        assert amendment["drafted_by"] == "merchant_agent"
        assert amendment["state"] == "PENDING_APPROVAL"
        assert amendment["reason_code_triggered"] == "policy.sku_blocked"
        assert amendment["approval"]["webauthn_assertion"] is None  # R0.5: no self-approval
        assert amendment["draft_digest"].startswith("sha256:")

    def test_draft_amendment_requires_human_signature(self, config):
        """R0.5: agent cannot self-approve. The amendment is a PROPOSAL."""
        agent = MerchantAgent(config)
        amendment = agent.draft_amendment("h", "policy.sku_blocked", [], "trace_001")
        # webauthn_assertion must be None — human must sign
        assert amendment["approval"]["webauthn_assertion"] is None
        assert amendment["approval"]["approver_credential_id"] is None

    def test_narrator_writes_prose(self, config):
        agent = MerchantAgent(config)
        bundle = {
            "transaction": {
                "merchant_id": "gelateria",
                "checkout_id": "chk_001",
                "amount_minor": 21000,
            },
            "adjudication": {"verdict": "ALLOW"},
            "aal": {"level": 2},
        }
        note = agent.narrate(bundle)
        assert "chk_001" in note
        assert "AAL" in note or "level" in note.lower()
        assert "gelateria" in note
        # R0.9: narrator prose is text, never modifying the bundle
        assert isinstance(note, str)


class TestNegotiationStates:
    def test_valid_states(self):
        from openstore.agents.merchant_agent import NEGOTIATION_STATES

        for state in (
            "PROPOSED",
            "COUNTERED",
            "ACCEPTED",
            "NO_COMPLIANT_PATH",
            "AMENDMENT_REQUESTED",
        ):
            assert state in NEGOTIATION_STATES

    def test_invalid_state_raises(self):
        with pytest.raises(ValueError):
            NegotiationMessage(
                negotiation_id="neg_1",
                round=1,
                from_="buyer_agent",
                state="INVALID_STATE",
                cart_delta={},
                reason_code="x",
                trace_id="t",
            )

    def test_invalid_from_raises(self):
        with pytest.raises(ValueError):
            NegotiationMessage(
                negotiation_id="neg_1",
                round=1,
                from_="random",
                state="PROPOSED",
                cart_delta={},
                reason_code="x",
                trace_id="t",
            )


class TestR0NoAgentBypass:
    """R0.9: agents can never bypass compile_decision()."""

    def test_buyer_agent_no_spend_cap(self, config, mcp_client):
        """Buyer agent cannot have a 'skip_spend_cap' knob."""
        agent = BuyerAgent(config, mcp_client)
        assert not hasattr(agent, "skip_spend_cap")
        assert not hasattr(agent, "override_total")
        assert not hasattr(agent, "force_allow")

    def test_merchant_agent_no_signing_keys(self, config):
        """R0.10: merchant agent holds no signing material."""
        agent = MerchantAgent(config)
        for attr in dir(agent):
            if "key" in attr.lower() and attr not in ("_mock_keys",):
                if not attr.startswith("__"):
                    val = getattr(agent, attr, None)
                    if callable(val) and not isinstance(val, type):
                        continue
                    assert "secret" not in attr.lower() or "secret" in ("draft_secret",)


class TestLLMProvider:
    def test_dummy_provider(self):
        provider = DummyProvider(model="dummy-model")
        result = provider.chat([{"role": "user", "content": "hi"}])
        assert isinstance(result, str)

    def test_register_provider(self):
        from openstore.agents.llm import _PROVIDERS, register_provider

        class TestProvider(LLMProvider):
            def chat(self, messages, **kwargs):
                return "test"

        register_provider("test", TestProvider)
        assert "test" in _PROVIDERS
