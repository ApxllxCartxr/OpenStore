"""Model abstraction for the judge (AGENT_LAYER.md §3.3).

The judge is an *offline* LLM opinion. It is deliberately pluggable: the
harness ships with a deterministic offline heuristic so it runs with zero
configuration, and a Gemini adapter that activates only when an API key is
present. The money path never imports this module.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Protocol

TOKEN_PRICE_PER_1K = float(os.environ.get("EVAL_TOKEN_PRICE", "0.00001"))


class ModelFn(Protocol):
    """A callable model. Returns free text; records tokens via `.tokens`."""

    tokens: int

    def __call__(self, prompt: str, *, seed: int, model: str) -> str: ...


class OfflineHeuristicModel:
    """Deterministic stand-in so `evals.run` produces all five metrics with no LLM.

    Rules (transparent, not a real judge):
      - parse a budget ceiling "₹<n>" -> n*100 minor; parse a dietary tag.
      - satisfies if total <= budget AND every item carries the requested tag.
      - violates if budget exceeded.
      - partial otherwise.
    """

    tokens = 0

    def __call__(self, prompt: str, *, seed: int, model: str) -> str:
        # Three seeds must be reproducible; jitter only the rationale wording.
        self.tokens += 32
        m = re.search(r"REQUEST:\n(.*?)\n", prompt, re.S)
        request = m.group(1).strip() if m else ""
        items_m = re.search(r"ITEMS:\n(.*?)\nPOLICY:", prompt, re.S)
        items_blob = items_m.group(1) if items_m else ""

        budget = None
        bm = re.search(r"₹\s*(\d+)", request)
        if bm:
            budget = int(bm.group(1)) * 100
        tag = None
        for t in ("vegan", "alcohol", "dairy-free", "gluten-free", "nut-free"):
            if t in request.lower():
                tag = t
                break

        total = 0
        tags_seen = set()
        for line in items_blob.splitlines():
            line = line.strip()
            if not line:
                continue
            qm = re.search(r"qty=(\d+)\s+unit_minor=(\d+)\s+tags=\[(.*)\]", line)
            if not qm:
                continue
            qty = int(qm.group(1))
            unit = int(qm.group(2))
            its = [t.strip() for t in qm.group(3).split(",") if t.strip()]
            total += qty * unit
            tags_seen.update(its)

        if budget is not None and total > budget:
            label = "violates"
        elif tag is not None and tag not in tags_seen:
            label = "partial"
        elif budget is None and not tag:
            label = "partial"
        else:
            label = "satisfies"
        return f'{{"label":"{label}","rationale":"offline heuristic (seed {seed})"}}'


class GeminiModel:
    """Adapter for google-genai. Imported lazily; degrades to offline if absent."""

    tokens = 0

    def __init__(self, model_id: str = "gemini-2.5-flash"):
        self.model_id = model_id
        self._client = None

    def _ensure(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        return self._client

    def __call__(self, prompt: str, *, seed: int, model: str) -> str:
        client = self._ensure()
        resp = client.models.generate_content(
            model=model or self.model_id,
            contents=prompt,
            config={"temperature": 0, "seed": seed},
        )
        text = resp.text or ""
        self.tokens += max(1, len(text) // 4)
        return text


def make_model(model_id: str) -> ModelFn:
    if model_id.startswith("gemini") and os.environ.get("GEMINI_API_KEY"):
        return GeminiModel(model_id)
    return OfflineHeuristicModel()


def parse_label(raw: str) -> tuple[str, str]:
    """Extract a FIDELITY_LABELS member from a model response, best-effort."""
    m = re.search(r'"label"\s*:\s*"(satisfies|partial|violates|unanswerable)"', raw)
    if m:
        rm = re.search(r'"rationale"\s*:\s*"(.*?)"', raw)
        return m.group(1), (rm.group(1) if rm else raw[:200])
    for lab in ("satisfies", "partial", "violates", "unanswerable"):
        if lab in raw.lower():
            return lab, raw[:200]
    return "unanswerable", raw[:200]
