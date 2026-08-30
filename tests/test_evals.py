"""Tests for the eval harness (AGENT_LAYER.md §3)."""

import json
from pathlib import Path

from evals import (
    compute_metrics,
    judge_case,
    load_calibration,
    load_cases,
    majority_vote,
    run_suite,
)
from evals.model import OfflineHeuristicModel
from openstore.compiler import COMPILER_DIGEST

CASES_FILE = Path(__file__).parent.parent / "evals" / "dataset" / "cases.jsonl"
CAL_FILE = Path(__file__).parent.parent / "evals" / "dataset" / "calibration.jsonl"


def test_dataset_loads():
    cases = load_cases()
    assert len(cases) >= 8
    assert all(c.expected_safety in ("ALLOW", "DENY") for c in cases)


def test_calibration_has_50_plus():
    cal = load_calibration()
    assert len(cal) >= 50


def test_offline_judge_three_seeds_deterministic():
    case = load_cases()[0]
    votes, label = judge_case(case.request_text, case.policy, case.cart_items, model="offline")
    assert len(votes) == 3
    assert label in ("satisfies", "partial", "violates", "unanswerable")
    # All three offline seeds agree (deterministic heuristic).
    assert len({v.label for v in votes}) == 1


def test_majority_vote_tie_resolves_unanswerable():
    assert majority_vote(["satisfies", "partial", "violates"]) == "unanswerable"
    assert majority_vote(["satisfies", "satisfies", "violates"]) == "satisfies"


def test_safety_violation_rate_is_zero_on_clean_corpus():
    cases = load_cases()
    metrics = compute_metrics(cases, model="offline")
    assert metrics["safety_violation_rate"] == 0.0
    # All five metrics present.
    for k in ("safety_violation_rate", "blocked_attempt_rate", "intent_fidelity_rate",
              "task_completion_rate", "cost_per_completed_task"):
        assert k in metrics


def test_run_suite_writes_report_and_exits_zero(tmp_path):
    out = tmp_path / "reports"
    code = run_suite(suite="cases.jsonl", model="offline", out=str(out))
    assert code == 0
    files = list(out.glob("*.json"))
    assert files
    report = json.loads(files[0].read_text())
    assert report["compiler_digest"] == COMPILER_DIGEST
    # Calibration present; kappa is reported.
    assert report["intent_fidelity_calibration"]["available"] is True
    assert "kappa" in report["intent_fidelity_calibration"]
