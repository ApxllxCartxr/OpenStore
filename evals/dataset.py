"""Case loading (AGENT_LAYER.md §3.1)."""

from __future__ import annotations

import json
from pathlib import Path

from .types import Case

DATASET_DIR = Path(__file__).parent / "dataset"


def load_case_file(path: str | Path) -> list[Case]:
    path = Path(path)
    if not path.exists():
        return []
    cases = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            cases.append(Case.from_json(json.loads(line)))
    return cases


def load_cases(name: str = "cases.jsonl") -> list[Case]:
    return load_case_file(DATASET_DIR / name)


def load_calibration() -> list[Case]:
    return load_case_file(DATASET_DIR / "calibration.jsonl")
