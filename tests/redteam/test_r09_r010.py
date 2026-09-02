# tests/redteam/test_r09_r010.py
# R0.9 — LLM output is a proposal, never a command. All agent artifacts pass through
#   compile_decision() and the deterministic validators. No agent bypass path exists.
# R0.10 — Reasoning agents hold NO payment keys, PSP credentials, or signing material.

from __future__ import annotations

from conftest import build_settings
from openstore.agents.buyer_agent import BuyerAgent
from openstore.agents.campaign_agent import CampaignAgent
from openstore.agents.merchant_agent import MerchantAgent


def _dummy_mcp() -> object:
    # BuyerAgent now requires an mcp_client; these tests only inspect agent
    # attributes and never invoke a tool call, so a bare object is sufficient.
    return object()


def test_buyer_agent_has_no_override_knobs():
    """R0.9: no skip/force/override knobs on the money-path agent."""
    agent = BuyerAgent(build_settings(), _dummy_mcp())
    for attr in ("skip_spend_cap", "override_total", "force_allow", "bypass_compiler"):
        assert not hasattr(agent, attr)


def test_merchant_agent_cannot_emit_verdict(session):
    """R0.9: negotiate() returns only negotiation states, never an allow/deny command."""
    agent = MerchantAgent(build_settings())
    cart = [{"sku": "GEL-VAN", "qty": 1, "unit_minor": 21000, "tags": ["vegan"]}]
    result = agent.negotiate(cart, "policy.tag_violation", "trace_001", policy={})
    assert result["state"] in (
        "PROPOSED",
        "COUNTERED",
        "ACCEPTED",
        "NO_COMPLIANT_PATH",
        "AMENDMENT_REQUESTED",
    )


def test_buyer_agent_holds_no_payment_keys():
    """R0.10: the buyer agent has no PSP credentials in scope."""
    agent = BuyerAgent(build_settings(), _dummy_mcp())
    for attr in ("razorpay_key_secret", "key_secret", "psp_credentials", "signing_key"):
        assert not hasattr(agent, attr)


def test_merchant_agent_holds_no_signing_material():
    """R0.10: the merchant agent holds no private-key material."""
    agent = MerchantAgent(build_settings())
    seen = {name for name in dir(agent)}
    secrets = {
        n
        for n in seen
        if "secret" in n.lower() or "private_key" in n.lower() or "signing" in n.lower()
    }
    assert secrets == set()


def test_campaign_agent_has_no_session_no_keys():
    """R0.10 (INV-14): the campaign agent holds only config, never a session or keys."""
    agent = CampaignAgent(build_settings())
    assert agent.config is not None
    for attr in ("session", "razorpay_key_secret", "key_secret", "private_key", "signing_key"):
        assert not hasattr(agent, attr)


def test_campaign_draft_is_not_activated(session):
    """R0.9: a drafted campaign is inert until a human approves it."""
    from openstore.agents.campaign_agent import CampaignAgent
    from openstore.models import Campaign
    from sqlmodel import select

    agent = CampaignAgent(build_settings())
    draft = agent.draft_campaign(session, "gelateria-milano")
    session.commit()
    assert isinstance(draft, dict)
    if "error" not in draft:
        assert "title" in draft
    # The draft call alone must not have created an ACTIVE campaign row.
    rows = session.exec(select(Campaign)).all()
    for row in rows:
        assert row.state.value != "ACTIVE"
