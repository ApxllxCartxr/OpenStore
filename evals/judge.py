"""Fidelity judge (AGENT_LAYER.md §3.3).

`judge_fidelity` is one call at one seed. `judge_case` runs three seeds and
takes a majority vote, ties resolving to `unanswerable`, and persists all three
raw votes (R3.3c).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .model import make_model, parse_label
from .types import FidelityVerdict

RUBRIC_PATH = Path(__file__).parent / "prompts" / "fidelity_rubric.md"


def rubric_hash() -> str:
    text = RUBRIC_PATH.read_text(encoding="utf-8")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_prompt(request_text: str, policy: dict, items: list) -> str:
    ifmt = "\n".join(
        f"- sku={i.get('sku')} qty={i.get('qty')} unit_minor={i.get('unit_minor')} "
        f"tags=[{', '.join(i.get('tags', []))}]"
        for i in items
    )
    pfmt = "\n".join(f"  {k}: {v}" for k, v in policy.items())
    return (
        f"RUBRIC:\n{RUBRIC_PATH.read_text(encoding='utf-8')}\n\n"
        f"REQUEST:\n{request_text}\n\n"
        f"ITEMS:\n{ifmt}\n\n"
        f"POLICY:\n{pfmt}\n\n"
        "Return JSON: {\"label\": <one of satisfies|partial|violates|unanswerable>, "
        "\"rationale\": <string>}"
    )


def judge_fidelity(
    request_text: str,
    policy: dict,
    items: list,
    model: str = "offline",
    seed_index: int = 0,
) -> FidelityVerdict:
    fn = make_model(model)
    prompt = _build_prompt(request_text, policy, items)
    raw = fn(prompt, seed=seed_index, model=model)
    label, rationale = parse_label(raw)
    return FidelityVerdict(label=label, rationale=rationale, raw=raw, model=model, seed_index=seed_index)


def judge_case(request_text: str, policy: dict, items: list, model: str = "offline"):
    votes = tuple(
        judge_fidelity(request_text, policy, items, model=model, seed_index=s)
        for s in (0, 1, 2)
    )
    labels = [v.label for v in votes]
    from .metrics import majority_vote

    return votes, majority_vote(labels)
