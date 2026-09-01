# OpenStore agents — Campaign Agent (S8.2)
# Reads ONLY the aggregated analytics view (INV-14). No raw PII.

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from openstore.agents.llm import llm_chat
from openstore.config import Settings
from openstore.core.campaigns import get_analytics_view
from openstore.notifier import sync_merchant_trace


class CampaignAgent:
    """
    S8.2: Campaign orchestrator.
    - Reads ONLY the derived analytics view (INV-14).
    - Drafts a Campaign per the §9.3 closed schema.
    - LLM output is a DRAFT only (R0.9).
    """

    def __init__(self, config: Settings):
        self.config = config

    def draft_campaign(
        self,
        session: Any,
        merchant_id: str,
        calendar_event: str | None = None,
    ) -> dict[str, Any]:
        """
        S8.2: Draft a campaign from analytics + calendar.
        R0.9: LLM output is a DRAFT. Deterministic validation follows.
        """
        analytics = get_analytics_view(session, merchant_id)
        top_skus = [a["sku"] for a in sorted(analytics, key=lambda x: x["units_sold_30d"], reverse=True)[:5]]
        slow_skus = [a["sku"] for a in sorted(analytics, key=lambda x: x["units_sold_30d"])[:5]]

        gross_minor = sum(a["gross_minor_30d"] for a in analytics)
        headroom_minor = max(0, 200000 - gross_minor)

        prompt = json.dumps({
            "top_skus": top_skus,
            "slow_skus": slow_skus,
            "calendar_event": calendar_event or "no upcoming events",
            "headroom_minor": headroom_minor,
            "merchant_id": merchant_id,
        })

        sync_merchant_trace("campaign_draft", "draft_start", {
            "merchant_id": merchant_id,
            "top_skus": len(top_skus),
            "slow_skus": len(slow_skus),
        })

        try:
            llm_response = llm_chat(
                self.config,
                messages=[
                    {"role": "system", "content": "You are a campaign strategist. Respond with valid JSON only."},
                    {"role": "user", "content": f"Draft a campaign JSON: {prompt}"},
                ],
            )
            return self._parse_llm_draft(llm_response, top_skus, slow_skus, calendar_event, headroom_minor)
        except Exception as e:
            sync_merchant_trace("campaign_draft", "draft_error", {"error": str(e)})
            return {"error": str(e)}

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
        except json.JSONDecodeError:
            return {"error": "invalid_json"}

        if not isinstance(draft.get("title"), str):
            return {"error": "missing_title"}
        if not isinstance(draft.get("discount_bps"), int):
            return {"error": "missing_discount_bps"}

        return {
            "title": draft.get("title", ""),
            "rationale": draft.get("rationale", ""),
            "discount_bps": draft.get("discount_bps", 0),
            "applies_to_skus": draft.get("applies_to_skus", top_skus),
            "source_signals": {
                "top_skus": top_skus,
                "slow_skus": slow_skus,
                "calendar_event": calendar_event,
                "headroom_minor": headroom_minor,
            },
        }
