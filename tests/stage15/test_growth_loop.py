# tests/stage15/test_growth_loop.py
# DECISION-034: the autonomous growth-trigger loop and its feedback half.
#
# detect_stalled_skus/compute_campaign_outcome are pure and deterministic
# (R0.5 — the LLM never decides what counts as a decline or a lift). This
# suite pins the trigger's on/off conditions, the cooldown, and that the
# outcome math reads real Checkout data rather than inventing numbers.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from openstore.agents.campaign_agent import auto_draft_campaign_if_stalled
from openstore.core.campaigns import (
    compute_campaign_outcome,
    create_campaign,
    detect_stalled_skus,
    recent_campaign_outcomes,
    should_auto_trigger,
    submit_for_approval,
)


def _queue_provider(monkeypatch, name: str, responses: list) -> None:
    from openstore.agents.llm import DummyProvider, register_provider

    queue = list(responses)

    class _Provider(DummyProvider):
        def chat(self, messages, **kwargs):
            response = queue.pop(0)
            self.last_messages = messages  # type: ignore[attr-defined]
            return response if isinstance(response, str) else json.dumps(response)

    register_provider(name, _Provider)
    monkeypatch.setenv("LLM_PROVIDER", name)


class TestDetectStalledSkus:
    def test_flags_a_sku_with_real_30d_history_and_zero_last_7d(self):
        analytics = [
            {"sku": "gelato_vanilla", "units_sold_7d": 0, "units_sold_30d": 5},
            {"sku": "gelato_pistachio", "units_sold_7d": 2, "units_sold_30d": 5},
            {"sku": "gelato_chocolate", "units_sold_7d": 0, "units_sold_30d": 1},
        ]
        # min_units_30d=3 excludes chocolate's one-off sale from counting as a stall.
        assert detect_stalled_skus(analytics, min_units_30d=3) == ["gelato_vanilla"]

    def test_empty_analytics_is_not_a_stall(self):
        assert detect_stalled_skus([], min_units_30d=3) == []


class TestComputeCampaignOutcome:
    def test_reads_real_units_before_and_after_from_checkouts(
        self, session, config_with_catalog, seed_checkout
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        starts_at = now - timedelta(days=3)

        # Before window (equal length: 3 days pre-start).
        seed_checkout("gelato_vanilla", 2, 15000, starts_at - timedelta(days=1))
        # After window (since starts_at).
        seed_checkout("gelato_vanilla", 5, 15000, starts_at + timedelta(days=1))
        # A different SKU entirely — must not be counted either side.
        seed_checkout("gelato_pistachio", 9, 18000, starts_at + timedelta(days=1))

        campaign = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Vanilla push",
            rationale="Slow mover",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=starts_at,
            ends_at=now + timedelta(days=4),
        )
        session.commit()

        outcome = compute_campaign_outcome(session, campaign, now=now)
        assert outcome["units_before"] == 2
        assert outcome["units_after"] == 5
        assert outcome["delta_pct"] == 150.0

    def test_delta_pct_is_none_not_zero_when_nothing_sold_before(
        self, session, config_with_catalog, seed_checkout
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        starts_at = now - timedelta(days=2)
        seed_checkout("gelato_vanilla", 3, 15000, starts_at + timedelta(hours=1))

        campaign = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="New push",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=starts_at,
            ends_at=now + timedelta(days=5),
        )
        session.commit()

        outcome = compute_campaign_outcome(session, campaign, now=now)
        assert outcome["units_before"] == 0
        assert outcome["units_after"] == 3
        assert outcome["delta_pct"] is None


class TestShouldAutoTrigger:
    def test_no_stall_means_no_trigger(self, session, config_with_catalog):
        assert should_auto_trigger(session, config_with_catalog, "gelateria-milano") == []

    def test_stall_with_no_live_coverage_triggers(
        self, session, config_with_catalog, seed_checkout
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        # 4 units 10-20 days ago (in the 30d window, outside the 7d window), 0 recently.
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))

        stalled = should_auto_trigger(session, config_with_catalog, "gelateria-milano", now=now)
        assert stalled == ["gelato_vanilla"]

    def test_stall_already_covered_by_a_live_campaign_does_not_retrigger(
        self, session, config_with_catalog, seed_checkout
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))
        campaign = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Already running",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=6),
        )
        submit_for_approval(session, campaign.id)
        session.commit()

        assert should_auto_trigger(session, config_with_catalog, "gelateria-milano", now=now) == []

    def test_cooldown_blocks_a_second_auto_trigger(
        self, session, config_with_catalog, seed_checkout
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))

        # A prior auto-triggered draft for a DIFFERENT sku, created recently —
        # the cooldown is per-merchant, not per-sku (R0.5: bounds draft
        # frequency regardless of how many things stall at once).
        prior = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Earlier auto draft",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_pistachio"],
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=6),
            source_signals={"trigger": "auto_stall_detected", "stalled_skus": ["gelato_pistachio"]},
        )
        session.commit()
        assert prior.created_at is not None

        assert should_auto_trigger(session, config_with_catalog, "gelateria-milano", now=now) == []

    def test_cooldown_expires(self, session, config_with_catalog, seed_checkout):
        now = datetime.now(UTC).replace(tzinfo=None)
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))

        old_draft = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Old auto draft",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_pistachio"],
            starts_at=now - timedelta(days=2),
            ends_at=now + timedelta(days=6),
            source_signals={"trigger": "auto_stall_detected", "stalled_skus": ["gelato_pistachio"]},
        )
        old_draft.created_at = now - timedelta(hours=25)  # past the 24h default cooldown
        session.add(old_draft)
        session.commit()

        assert should_auto_trigger(session, config_with_catalog, "gelateria-milano", now=now) == [
            "gelato_vanilla"
        ]


class TestAutoDraftCampaignIfStalled:
    def test_drafts_and_parks_for_approval_when_stalled(
        self, session, config_with_catalog, seed_checkout, monkeypatch
    ):
        now = datetime.now(UTC).replace(tzinfo=None)
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))
        _queue_provider(
            monkeypatch,
            "growth_loop_draft",
            [
                {
                    "title": "Win back vanilla",
                    "rationale": "Stalled for a week",
                    "discount_bps": 1000,
                    "applies_to_skus": ["gelato_vanilla"],
                }
            ],
        )

        campaign = auto_draft_campaign_if_stalled(session, config_with_catalog, "gelateria-milano")

        assert campaign is not None
        assert campaign.state.value == "PENDING_APPROVAL"
        assert campaign.source_signals["trigger"] == "auto_stall_detected"
        assert campaign.source_signals["stalled_skus"] == ["gelato_vanilla"]

    def test_returns_none_when_nothing_stalled(self, session, config_with_catalog, monkeypatch):
        # No LLM call should even happen — should_auto_trigger short-circuits first.
        result = auto_draft_campaign_if_stalled(session, config_with_catalog, "gelateria-milano")
        assert result is None

    def test_never_activates_only_reaches_pending_approval(
        self, session, config_with_catalog, seed_checkout, monkeypatch
    ):
        """R0.10: nothing about the autonomous path can ever move a campaign
        past PENDING_APPROVAL — still requires the real WebAuthn ceremony."""
        now = datetime.now(UTC).replace(tzinfo=None)
        seed_checkout("gelato_vanilla", 4, 15000, now - timedelta(days=15))
        _queue_provider(
            monkeypatch,
            "growth_loop_no_activate",
            [
                {
                    "title": "Win back vanilla",
                    "rationale": "Stalled",
                    "discount_bps": 1000,
                    "applies_to_skus": ["gelato_vanilla"],
                }
            ],
        )
        campaign = auto_draft_campaign_if_stalled(session, config_with_catalog, "gelateria-milano")
        assert campaign is not None
        assert campaign.state.value != "ACTIVE"
        assert campaign.approver_credential_id is None
        assert campaign.webauthn_assertion is None


class TestCampaignAgentUsesOutcomeFeedback:
    """DECISION-034: the feedback half only matters if the draft prompt
    actually carries it — pins that CampaignAgent.draft_campaign puts a past
    campaign's real before/after numbers in front of the model, not just
    computes them and drops them."""

    def test_prompt_carries_past_campaign_outcomes(
        self, session, config_with_catalog, seed_checkout, monkeypatch
    ):
        from openstore.agents.campaign_agent import CampaignAgent
        from openstore.agents.llm import DummyProvider, register_provider

        now = datetime.now(UTC).replace(tzinfo=None)
        starts_at = now - timedelta(days=5)
        seed_checkout("gelato_vanilla", 1, 15000, starts_at - timedelta(days=1))
        seed_checkout("gelato_vanilla", 4, 15000, starts_at + timedelta(days=1))
        campaign = create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Earlier vanilla push",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=starts_at,
            ends_at=now + timedelta(days=2),
        )
        submit_for_approval(session, campaign.id)
        from openstore.models import CampaignState

        campaign.state = CampaignState.ACTIVE
        session.add(campaign)
        session.commit()

        captured: dict[str, Any] = {}

        class _CapturingProvider(DummyProvider):
            def chat(self, messages, **kwargs):
                captured["messages"] = messages
                return json.dumps(
                    {
                        "title": "New push",
                        "rationale": "R",
                        "discount_bps": 1000,
                        "applies_to_skus": ["gelato_vanilla"],
                    }
                )

        register_provider("test_outcome_feedback", _CapturingProvider)
        monkeypatch.setenv("LLM_PROVIDER", "test_outcome_feedback")

        CampaignAgent(config_with_catalog).draft_campaign(session, "gelateria-milano")

        user_payload = json.loads(captured["messages"][-1]["content"].split(": ", 1)[1])
        outcomes = user_payload["past_campaign_outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["title"] == "Earlier vanilla push"
        assert outcomes[0]["units_before"] == 1
        assert outcomes[0]["units_after"] == 4


class TestRecentCampaignOutcomes:
    def test_only_includes_campaigns_that_actually_ran(self, session, config_with_catalog):
        now = datetime.now(UTC).replace(tzinfo=None)
        create_campaign(
            session,
            config_with_catalog,
            merchant_id="gelateria-milano",
            title="Still a draft",
            rationale="R",
            discount_bps=1000,
            applies_to_skus=["gelato_vanilla"],
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=6),
        )
        session.commit()

        # DRAFT never ran — excluded.
        assert recent_campaign_outcomes(session, "gelateria-milano") == []
