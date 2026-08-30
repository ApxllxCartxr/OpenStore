"""AgentExecutor: dispatches incoming A2A tasks to the matching skill.

This is a lightweight A2A task executor. It parses the skill_id from the
incoming message, runs the corresponding skill function, and returns the
result. All skills complete synchronously — no streaming needed.
"""

import json
import logging
import uuid

from reference.merchant_agent.skills.cross_sell import cross_sell_skill
from reference.merchant_agent.skills.campaign_draft import campaign_draft_skill
from reference.merchant_agent.skills.finance_qa import finance_qa_skill

logger = logging.getLogger(__name__)

_SKILLS = {
    "cross_sell": cross_sell_skill,
    "campaign_draft": campaign_draft_skill,
    "finance_qa": finance_qa_skill,
}


def execute_task(skill_id: str, params: dict) -> dict:
    """Dispatch to the matching skill and return the result.

    Args:
        skill_id: one of "cross_sell", "campaign_draft", "finance_qa"
        params: skill-specific parameters

    Returns:
        {"status": "completed", "result": {...}} or
        {"status": "failed", "error": "..."}
    """
    skill_fn = _SKILLS.get(skill_id)
    if skill_fn is None:
        return {
            "status": "failed",
            "error": f"Unknown skill: {skill_id}. Available: {list(_SKILLS.keys())}",
        }

    try:
        logger.info("Executing skill %s with params %s", skill_id, params)
        result = skill_fn(**params)
        return {"status": "completed", "result": result}
    except Exception as e:
        logger.exception("Skill %s failed", skill_id)
        return {"status": "failed", "error": str(e)}
