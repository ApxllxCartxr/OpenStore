"""Adversarial red-team campaign runner (AGENT_LAYER.md §4)."""

from .target import SimulatedBuyerAgent, run_attack
from .run import run_campaign, main

__all__ = ["SimulatedBuyerAgent", "run_attack", "run_campaign", "main"]
