# OpenStore agents — Campaign Agent (S8.2)
# Reads ONLY the aggregated analytics view (INV-14). No raw PII.

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from openstore.agents.llm import llm_chat
from openstore.config import Settings
from openstore.core.campaigns import (
    AUTO_TRIGGER_SOURCE,
    compute_merchant_headroom,
    create_campaign,
    get_analytics_view,
    recent_campaign_outcomes,
    should_auto_trigger,
    submit_for_approval,
)
from openstore.models import Campaign
from openstore.notifier import sync_merchant_trace


class CampaignDraftError(Exception):
    """The LLM broke the §9.3 draft contract. Distinct from llm.LLMError, which
    means the call itself failed."""


class CampaignAgent:
    """
    S8.2: Campaign orchestrator.
    - Reads ONLY the derived analytics view (INV-14).
    - Drafts a Campaign per the §9.3 closed schema.
    - LLM output is a DRAFT only (R0.9); core/campaigns.validate_campaign disposes.
    """

    def __init__(self, config: Settings):
        self.config = config

    def draft_campaign(
        self,
        session: Session,
        merchant_id: str,
        calendar_event: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """
        S8.2: Draft a campaign from analytics + calendar.
        R0.9: LLM output is a DRAFT. Deterministic validation follows.

        Fails loud (R0.5). This used to wrap the whole body in
        `except Exception: return {"error": str(e)}`, which made an LLM outage
        and a contract violation indistinguishable from a valid draft with an
        error key, and made it the only agent in the tree that swallowed.
        """
        analytics = get_analytics_view(session, merchant_id)
        top_skus = [
            a["sku"] for a in sorted(analytics, key=lambda x: x["units_sold_30d"], reverse=True)[:5]
        ]
        slow_skus = [a["sku"] for a in sorted(analytics, key=lambda x: x["units_sold_30d"])[:5]]

        headroom_minor = compute_merchant_headroom(session, merchant_id)
        # DECISION-034: feedback loop. Past campaigns' actual before/after
        # sales are given alongside the raw analytics, so a repeat suggestion
        # is informed by what already did or didn't work, not blind to it.
        past_outcomes = recent_campaign_outcomes(session, merchant_id)

        prompt = json.dumps(
            {
                "top_skus": top_skus,
                "slow_skus": slow_skus,
                "calendar_event": calendar_event or "no upcoming events",
                "headroom_minor": headroom_minor,
                "merchant_id": merchant_id,
                "discount_bps_range": [self.config.campaign.min_bps, self.config.campaign.max_bps],
                "past_campaign_outcomes": past_outcomes,
            }
        )

        sync_merchant_trace(
            trace_id or "campaign",
            "draft_start",
            {
                "merchant_id": merchant_id,
                "top_skus": len(top_skus),
                "slow_skus": len(slow_skus),
                "headroom_minor": headroom_minor,
            },
        )

        llm_response = llm_chat(
            self.config,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a campaign strategist. Respond with valid JSON only, "
                        'shaped {"title": str, "rationale": str, "discount_bps": int, '
                        '"applies_to_skus": [str]}. Choose applies_to_skus only from the '
                        "SKUs given to you. If past_campaign_outcomes is non-empty, weigh "
                        "it: a past campaign with a strongly negative or ~0% delta_pct on "
                        "the same SKU(s) is a real signal that a similar offer likely won't "
                        "help either — say so in rationale and prefer a different angle "
                        "(different SKU, different discount) rather than repeating it "
                        "unchanged. delta_pct is null when the SKU had no prior sales at "
                        "all to compare against, not a bad outcome."
                    ),
                },
                {"role": "user", "content": f"Draft a campaign JSON: {prompt}"},
            ],
        )
        return self._parse_llm_draft(
            llm_response, top_skus, slow_skus, calendar_event, headroom_minor
        )

    def _parse_llm_draft(
        self,
        llm_response: str,
        top_skus: list[str],
        slow_skus: list[str],
        calendar_event: str | None,
        headroom_minor: int,
    ) -> dict[str, Any]:
        try:
            draft = json.loads(llm_response)
        except json.JSONDecodeError as e:
            raise CampaignDraftError(f"campaign draft is not valid JSON: {e}") from e

        if not isinstance(draft, dict):
            raise CampaignDraftError("campaign draft must be a JSON object")
        if not isinstance(draft.get("title"), str):
            raise CampaignDraftError("campaign draft is missing a string 'title'")
        if not isinstance(draft.get("discount_bps"), int) or isinstance(
            draft.get("discount_bps"), bool
        ):
            raise CampaignDraftError("campaign draft is missing an integer 'discount_bps'")

        applies_to_skus = draft.get("applies_to_skus", top_skus)
        if not isinstance(applies_to_skus, list) or not all(
            isinstance(s, str) for s in applies_to_skus
        ):
            raise CampaignDraftError("campaign draft 'applies_to_skus' must be a list of strings")

        # Shape is checked here; VALUES are passed through untouched. Clamping a
        # discount into range or silently swapping an unknown SKU for a popular
        # one would be exactly the coercion R0.5 forbids — and it would hide a
        # misbehaving model. validate_campaign() disposes (R0.9), naming
        # campaign.discount_out_of_bounds or campaign.sku_not_found.
        return {
            "title": draft["title"],
            "rationale": draft.get("rationale", ""),
            "discount_bps": draft["discount_bps"],
            "applies_to_skus": applies_to_skus,
            "source_signals": {
                "top_skus": top_skus,
                "slow_skus": slow_skus,
                "calendar_event": calendar_event,
                "headroom_minor": headroom_minor,
            },
        }


def auto_draft_campaign_if_stalled(
    session: Session, config: Settings, merchant_id: str, trace_id: str | None = None
) -> Campaign | None:
    """DECISION-034: the growth loop's single entry point, called on a timer
    from server.py._campaign_growth_loop. Lives here rather than in
    core/campaigns.py because it calls CampaignAgent — core/ must never
    import agents/ (R0.9/R0.10, enforced by tests/sentinel/test_import_
    firewall.py). Same draft -> validate -> DRAFT -> PENDING_APPROVAL
    pipeline `openstore campaign draft` and MerchantBot's suggest_campaign
    already run — this only decides WHEN to call it, never skips
    validate_campaign, and never activates anything (R0.10: still requires a
    real WebAuthn approval at /campaign/studio). Returns None when
    should_auto_trigger found nothing to act on."""
    from datetime import UTC, datetime, timedelta

    stalled = should_auto_trigger(session, config, merchant_id)
    if not stalled:
        return None

    draft = CampaignAgent(config).draft_campaign(
        session,
        merchant_id,
        calendar_event=f"auto-detected stall on: {', '.join(stalled)}",
        trace_id=trace_id,
    )
    draft["source_signals"]["trigger"] = AUTO_TRIGGER_SOURCE
    draft["source_signals"]["stalled_skus"] = stalled

    now = datetime.now(UTC).replace(tzinfo=None)
    campaign = create_campaign(
        session,
        config,
        merchant_id=merchant_id,
        title=draft["title"],
        rationale=draft["rationale"],
        discount_bps=draft["discount_bps"],
        applies_to_skus=draft["applies_to_skus"],
        starts_at=now,
        ends_at=now + timedelta(days=7),
        source_signals=draft["source_signals"],
        trace_id=trace_id,
    )
    submit_for_approval(session, campaign.id, trace_id)
    sync_merchant_trace(
        trace_id or "campaign",
        "campaign_auto_triggered",
        {"campaign_id": campaign.id, "merchant_id": merchant_id, "stalled_skus": stalled},
    )
    return campaign
