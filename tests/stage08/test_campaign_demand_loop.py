# tests/stage08/test_campaign_demand_loop.py
# DECISION-025 demand side: before this, the Campaign Orchestrator published
# into a void. No cart line in the live flow ever carried a campaign_id, so
# compiler check 12, the discount math in core/compiler.py, and PoAI section 9
# were all unreachable in production — the PRD's own demo beat (Part 12 step 5,
# "the buyer agent's planner discovers it via list_campaigns and re-plans the
# next cart around it") could not run.
#
# These tests pin the whole path: buyer discovers -> Python attaches -> compiler
# discounts -> evidence records.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from openstore.agents.buyer_graph import BuyerGraph, _campaign_offers
from openstore.core.campaigns import create_campaign, submit_for_approval
from openstore.core.compiler import CompilerContext, compile_decision
from openstore.models import IntentPolicy


class _FakeMCP:
    """One merchant, one SKU, one live campaign covering it."""

    def __init__(self, campaigns: list[dict[str, Any]] | None = None, success: bool = True):
        self._campaigns = campaigns if campaigns is not None else [
            {
                "campaign_id": "camp_live",
                "merchant_id": "gelateria-milano",
                "title": "Pistachio push",
                "discount_bps": 1500,
                "applies_to_skus": ["gelato_pistachio"],
            }
        ]
        self._success = success
        self.list_calls = 0

    async def search_products(self, query, tags=None, limit=10):
        return {
            "success": True,
            "data": {
                "items": [
                    {
                        "sku": "gelato_pistachio",
                        "merchant_id": "gelateria-milano",
                        "name": "Pistachio Gelato",
                        "unit_minor": 18000,
                        "tags": ["pistachio"],
                    },
                    {
                        "sku": "gelato_vanilla",
                        "merchant_id": "gelateria-milano",
                        "name": "Vanilla Gelato",
                        "unit_minor": 15000,
                        "tags": ["vegan"],
                    },
                ]
            },
        }

    async def list_campaigns(self):
        self.list_calls += 1
        if not self._success:
            return {"success": False, "error": {"reason_code": "internal_error"}}
        return {"success": True, "data": {"campaigns": self._campaigns}}


def _queue_provider(monkeypatch, name: str, responses: list) -> None:
    from openstore.agents.llm import DummyProvider, register_provider

    queue = list(responses)

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            response = queue.pop(0)
            return response if isinstance(response, str) else json.dumps(response)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


async def test_offer_index_keys_by_merchant_and_sku():
    offers = await _campaign_offers(_FakeMCP())
    assert offers == {
        ("gelateria-milano", "gelato_pistachio"): {
            "campaign_id": "camp_live",
            "title": "Pistachio push",
            "discount_bps": 1500,
        }
    }


async def test_offer_index_is_empty_when_the_client_cannot_list():
    """A client without list_campaigns, or one whose call errors, must degrade
    to 'no offers' — never fail the buyer's turn."""
    assert await _campaign_offers(object()) == {}
    assert await _campaign_offers(_FakeMCP(success=False)) == {}


async def test_cart_line_carries_the_campaign_python_attached(config, monkeypatch):
    """The LLM names a SKU; Python decides whether an offer applies. The model
    is never asked for, and cannot supply, a campaign_id (R0.8/R0.9)."""
    _queue_provider(
        monkeypatch,
        "demand_loop_attach",
        [
            {"action": "search", "query": "gelato"},
            {
                "action": "answer",
                "selections": [
                    {"sku": "gelato_pistachio", "merchant_id": "gelateria-milano", "qty": 2},
                    {"sku": "gelato_vanilla", "merchant_id": "gelateria-milano", "qty": 1},
                ],
            },
        ],
    )
    mcp = _FakeMCP()
    result = await BuyerGraph(config, mcp).converse(
        [{"role": "user", "content": "some gelato"}], "p_1", "trace_demand"
    )

    cart = {line["sku"]: line for line in result["cart"]}
    assert cart["gelato_pistachio"]["campaign_id"] == "camp_live"
    assert cart["gelato_pistachio"]["discount_bps"] == 1500
    # A SKU the campaign does not cover carries no campaign at all.
    assert "campaign_id" not in cart["gelato_vanilla"]


async def test_llm_cannot_invent_a_campaign(config, monkeypatch):
    """A model that puts campaign_id in its own selection payload gets ignored:
    the value comes from the search result Python stamped."""
    _queue_provider(
        monkeypatch,
        "demand_loop_forged",
        [
            {"action": "search", "query": "gelato"},
            {
                "action": "answer",
                "selections": [
                    {
                        "sku": "gelato_vanilla",
                        "merchant_id": "gelateria-milano",
                        "qty": 1,
                        "campaign_id": "camp_i_made_up",
                        "discount_bps": 9000,
                    }
                ],
            },
        ],
    )
    result = await BuyerGraph(config, _FakeMCP()).converse(
        [{"role": "user", "content": "vanilla"}], "p_1", "trace_forged"
    )
    assert "campaign_id" not in result["cart"][0]


def test_compiler_discounts_a_cart_that_carries_a_live_campaign(session, config_with_catalog):
    """End of the loop: the attached campaign_id reaches check 12 and the
    discount math, and effective_amount_minor is below the raw subtotal."""
    now = datetime.now(UTC)
    campaign = create_campaign(
        session,
        config_with_catalog,
        merchant_id="gelateria-milano",
        title="Pistachio push",
        rationale="Slow mover",
        discount_bps=1500,
        applies_to_skus=["gelato_pistachio"],
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(days=7),
    )
    submit_for_approval(session, campaign.id)
    session.commit()

    policy = IntentPolicy(
        id="pol_demand",
        merchant_id="gelateria-milano",
        policy_version=2,
        policy_hash="ph_demand",
        currency="INR",
        max_spend_per_tx_minor=50000,
        max_spend_total_minor=200000,
        max_transactions=10,
        allowed_tags=[],
        tag_mode="all",
        blocked_skus=[],
        not_before=int(now.timestamp()) - 60,
        expires_at=int(now.timestamp()) + 86400,
        assertion_max_age_seconds=86400,
        no_human_authority=True,
        fulfilment_mode="all_or_nothing",
        required_skus=[],
        webauthn_credential_id="cred_demand",
        webauthn_sign_count=0,
        signed_at=now,
        is_active=True,
    )
    session.add(policy)
    session.commit()

    ctx = CompilerContext(
        cart_items=[
            {
                "sku": "gelato_pistachio",
                "qty": 2,
                "unit_minor": 18000,
                "tags": [],
                "campaign_id": campaign.id,
            }
        ],
        policy=policy,
        merchant_id="gelateria-milano",
        currency="INR",
        checkout_count=0,
        cumulative_spend_minor=0,
        has_webauthn_assertion=False,
        assertion_age_seconds=0,
        now_unix=int(now.timestamp()),
        campaign_lookup={
            campaign.id: {
                "id": campaign.id,
                "state": "ACTIVE",
                "offer_terms": {
                    "discount_bps": 1500,
                    "starts_at": campaign.starts_at.isoformat(),
                    "ends_at": campaign.ends_at.isoformat(),
                },
            }
        },
    )
    result = compile_decision(ctx)
    assert result.allowed is True, result.reason_code
    # 2 x 18000 = 36000, less 15% = 30600
    assert result.effective_amount_minor == 30600
    assert any(c["check"] == "campaign_validity" and c["result"] == "pass" for c in result.transcript)


@pytest.mark.parametrize("state", ["DRAFT", "PENDING_APPROVAL", "PAUSED", "EXPIRED"])
def test_only_active_campaigns_are_listed_to_buyers(session, config_with_catalog, state):
    """INV-13 at the discovery surface: list_campaigns must not show an offer
    the compiler would then reject."""
    from openstore.models import CampaignState
    from openstore.surfaces.mcp_server import list_campaigns

    now = datetime.now(UTC)
    campaign = create_campaign(
        session,
        config_with_catalog,
        merchant_id="gelateria-milano",
        title="T",
        rationale="R",
        discount_bps=1500,
        applies_to_skus=["gelato_vanilla"],
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(days=7),
    )
    campaign.state = CampaignState[state]
    session.add(campaign)
    session.commit()

    result = list_campaigns(config_with_catalog, session, "gelateria-milano")
    assert result.data["campaigns"] == []


def test_active_but_out_of_window_is_not_listed(session, config_with_catalog):
    """list_campaigns used to return every ACTIVE row regardless of window, so a
    buyer could discover an offer that check 12 then rejected with
    policy.campaign_outside_window."""
    from openstore.models import CampaignState
    from openstore.surfaces.mcp_server import list_campaigns

    now = datetime.now(UTC)
    campaign = create_campaign(
        session,
        config_with_catalog,
        merchant_id="gelateria-milano",
        title="Old",
        rationale="R",
        discount_bps=1500,
        applies_to_skus=["gelato_vanilla"],
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(days=1),
    )
    campaign.state = CampaignState.ACTIVE
    session.add(campaign)
    session.commit()

    assert list_campaigns(config_with_catalog, session, "gelateria-milano").data["campaigns"] == []


def test_catalog_feed_publishes_approved_offers(session, config_with_catalog, enrol_approver, approve_campaign):
    """PRD §9.2 stage 5: publish 'to catalog item offers[]'. The field was
    normalized on load since S6.5 and never written by anything."""
    from openstore.surfaces.catalog import serve_catalog_feed

    now = datetime.now(UTC)
    va = enrol_approver(session, config_with_catalog)
    campaign = create_campaign(
        session,
        config_with_catalog,
        merchant_id="gelateria-milano",
        title="Vanilla week",
        rationale="R",
        discount_bps=1000,
        applies_to_skus=["gelato_vanilla"],
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(days=7),
    )
    submit_for_approval(session, campaign.id)
    approve_campaign(session, config_with_catalog, va, campaign.id, sign_count=2)
    session.commit()

    feed = serve_catalog_feed(config_with_catalog, "gelateria-milano")
    by_sku = {item["sku"]: item for item in feed["items"]}
    assert by_sku["gelato_vanilla"]["offers"] == [
        {
            "campaign_id": campaign.id,
            "title": "Vanilla week",
            "discount_bps": 1000,
            "starts_at": campaign.starts_at.isoformat() + "Z",
            "ends_at": campaign.ends_at.isoformat() + "Z",
        }
    ]
    assert by_sku["gelato_chocolate"]["offers"] == []
