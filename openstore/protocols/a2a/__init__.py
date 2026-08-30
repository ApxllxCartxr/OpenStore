"""A2A (Agent2Agent) protocol surface (INTEROP_SPEC §6.4).

A2A is unchanged: the merchant reasoning agent (`merchant_agent/`) keeps its A2A
surface as-is. It holds no signing key, no Razorpay write credential, and no core
write access. This module simply points discovery at the existing agent card and
asserts the isolation invariant (R6.4a) as part of conformance.
"""

from __future__ import annotations

A2A_EXTERNAL_FIELDS = ("url", "authentication", "skills", "agent_card", "name", "version")


def agent_card(base_url: str) -> dict:
    """The A2A AgentCard, hosted at `/.well-known/agent.json`."""
    return {
        "name": "OpenStore merchant reasoning agent",
        "description": "A2A surface for the OpenStore merchant; read-only, no money writes.",
        "url": f"{base_url}/.well-known/agent.json",
        "version": "1.0.0",
        "authentication": {"schemes": ["oauth2_bearer"]},
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": [
            {"id": "catalog_qa", "name": "Catalog Q&A",
             "tags": ["catalog", "support"], "examples": ["what's in stock?"]},
        ],
    }
