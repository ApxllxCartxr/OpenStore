"""Eval harness for OpenStore agent behaviour (AGENT_LAYER.md §3).

Offline, deterministic, and isolated from the money path: this package MUST NOT
be imported by anything under `openstore/` (the judge is allowed to call the
pure compiler, but the compiler is never allowed to see this package).
"""

from .types import (
    FIDELITY_LABELS,
    SAFETY_LABELS,
    SOURCES,
    Case,
    CaseJudgement,
    FidelityVerdict,
)
from .dataset import load_cases, load_case_file, load_calibration
from .judge import judge_fidelity, judge_case
from .metrics import (
    cohen_kappa,
    confusion_matrix,
    majority_vote,
)
from .harvest import harvest_bundles
from .run import compute_metrics, run_suite, main

__all__ = [
    "FIDELITY_LABELS",
    "SAFETY_LABELS",
    "SOURCES",
    "Case",
    "CaseJudgement",
    "FidelityVerdict",
    "load_cases",
    "load_case_file",
    "judge_fidelity",
    "judge_case",
    "compute_metrics",
    "cohen_kappa",
    "confusion_matrix",
    "majority_vote",
    "harvest_bundles",
    "run_suite",
    "main",
]
