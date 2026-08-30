"""Closed identifier sets and dataclasses for the eval harness (AGENT_LAYER.md §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Closed sets — do not extend without updating AGENT_LAYER.md.
FIDELITY_LABELS = ("satisfies", "partial", "violates", "unanswerable")
SAFETY_LABELS = ("ALLOW", "DENY")
SOURCES = ("synthetic", "harvested", "adversarial")


@dataclass
class Case:
    case_id: str
    request_text: str
    policy: dict
    catalog_digest: str
    expected_safety: str
    expected_fidelity: str
    source: str
    notes: str = ""
    # The cart the agent actually submitted (used for the safety metrics).
    cart_items: list = field(default_factory=list)
    # The CompilerContext the agent operated under.
    context: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = {
            "case_id": self.case_id,
            "request_text": self.request_text,
            "policy": self.policy,
            "catalog_digest": self.catalog_digest,
            "expected_safety": self.expected_safety,
            "expected_fidelity": self.expected_fidelity,
            "source": self.source,
            "notes": self.notes,
            "cart_items": self.cart_items,
            "context": self.context,
        }
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Case":
        return cls(
            case_id=d["case_id"],
            request_text=d.get("request_text", ""),
            policy=d["policy"],
            catalog_digest=d.get("catalog_digest", ""),
            expected_safety=d["expected_safety"],
            expected_fidelity=d["expected_fidelity"],
            source=d["source"],
            notes=d.get("notes", ""),
            cart_items=d.get("cart_items", []),
            context=d.get("context", {}),
        )


@dataclass
class FidelityVerdict:
    """One judge call (one seed)."""

    label: str
    rationale: str
    raw: str
    model: str
    seed_index: int


@dataclass
class CaseJudgement:
    case_id: str
    raw_votes: tuple  # tuple[FidelityVerdict, ...]
    label: str  # majority (or "unanswerable" on tie)
    deviated: Optional[bool] = None
    money_moved: Optional[bool] = None
