# OpenStore agents — Campaign Agent (S8.2)
# Reads ONLY the aggregated analytics view (INV-14). No raw PII.

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from openstore.agents.llm import llm_chat
from openstore.config import Settings
from openstore.core.campaigns import get_analytics_view
from openstore.models import IntentPolicy
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

        headroom_minor = _policy_headroom_minor(session, merchant_id)

        prompt = json.dumps(
            {
                "top_skus": top_skus,
                "slow_skus": slow_skus,
                "calendar_event": calendar_event or "no upcoming events",
                "headroom_minor": headroom_minor,
                "merchant_id": merchant_id,
                "discount_bps_range": [self.config.campaign.min_bps, self.config.campaign.max_bps],
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
                        "SKUs given to you."
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


def _policy_headroom_minor(session: Session, merchant_id: str) -> int:
    """PRD §9.2 stage 1: 'current policy headroom' is one of the four ingest
    signals. It is the unspent total across this merchant's active signed
    policies — previously a hardcoded `200000 - gross_minor`, an invented
    constant with no basis in the spec (R0.7)."""
    from openstore.core.database import compute_policy_exposure

    policies = list(
        session.exec(
            select(IntentPolicy).where(
                IntentPolicy.merchant_id == merchant_id,
                IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
            )
        ).all()
    )
    headroom = 0
    for policy in policies:
        spent = compute_policy_exposure(session, policy.id)
        headroom += max(0, policy.max_spend_total_minor - spent)
    return headroom
