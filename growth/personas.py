"""Persona loading for the Synthetic Buyer Swarm (GROWTH_AGENTS.md §2.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .catalog import policy_from_dict

DEFAULT_PERSONAS_PATH = Path(__file__).parent / "data" / "personas.yaml"


@dataclass(frozen=True, slots=True)
class Persona:
    persona_id: str
    policy: dict
    goals: Tuple[str, ...]
    weight: float
    always_ignore_advisory: bool = False

    def compiler_policy(self):
        return policy_from_dict(self.policy)


def load_personas(path: str | Path = DEFAULT_PERSONAS_PATH) -> List[Persona]:
    data = yaml.safe_load(Path(path).read_text())
    out: List[Persona] = []
    for item in data:
        item = dict(item)
        pid = item["persona_id"]
        always_ignore = pid in ("skeptical_ignore_all",)
        out.append(Persona(
            persona_id=pid,
            policy=item["policy"],
            goals=tuple(item.get("goals", [])),
            weight=float(item.get("weight", 0.0)),
            always_ignore_advisory=always_ignore,
        ))
    return out


def validate_weights(personas: List[Persona], *, tol: float = 1e-6) -> None:
    """R2.1a — persona weights MUST sum to 1.0."""
    total = sum(p.weight for p in personas)
    if abs(total - 1.0) > tol:
        raise ValueError(f"persona weights sum to {total}, expected 1.0")


def weighted_sample(personas: List[Persona], n: int, seed: int) -> List[Persona]:
    """Sample `n` personas by weight (R2.1b: seeded for reproducibility)."""
    import random

    rng = random.Random(seed)
    return list(rng.choices(personas, weights=[p.weight for p in personas], k=n))


def default_personas() -> List[Persona]:
    personas = load_personas()
    validate_weights(personas)
    return personas
