"""Eval suite runner + report (AGENT_LAYER.md §3.2–§3.5)."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openstore.compiler import (
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    compile_decision,
)
from openstore.compiler import COMPILER_DIGEST

from .dataset import load_cases, load_calibration
from .judge import judge_case, rubric_hash
from .metrics import cohen_kappa, confusion_matrix, majority_vote
from .model import make_model, TOKEN_PRICE_PER_1K
from .types import FIDELITY_LABELS, Case

REPORT_DIR = Path(__file__).parent / "reports"


def _compiler_verdict(case: Case) -> str:
    try:
        policy = CompilerPolicy(**{k: v for k, v in case.policy.items()
                                   if k in CompilerPolicy.__dataclass_fields__})
        ctx = case.context or {}
        context = CompilerContext(
            merchant_id=ctx.get("merchant_id", policy.merchant_id),
            currency=ctx.get("currency", policy.currency),
            evaluated_at_unix=ctx.get("evaluated_at_unix", policy.expires_at),
            spent_minor=ctx.get("spent_minor", 0),
            transactions_count=ctx.get("transactions_count", 0),
        )
        items = tuple(
            CompilerItem(
                sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"],
                tags=tuple(i.get("tags", [])),
            )
            for i in case.cart_items
        )
        return compile_decision(items, policy, context).verdict
    except Exception:
        return "DENY"


def compute_metrics(cases: list[Case], model: str) -> dict:
    safety_violations = 0
    blocked = 0
    satisfied = 0
    completed = 0
    total_chars = 0
    per_case = []

    for case in cases:
        verdict = _compiler_verdict(case)
        if verdict == "ALLOW" and case.expected_safety == "DENY":
            safety_violations += 1
        if verdict == "DENY":
            blocked += 1
        if verdict == "ALLOW":
            completed += 1

        votes, label = judge_case(case.request_text, case.policy, case.cart_items, model=model)
        for v in votes:
            total_chars += len(v.raw)
        if label == "satisfies":
            satisfied += 1

        per_case.append({
            "case_id": case.case_id,
            "source": case.source,
            "compiler_verdict": verdict,
            "expected_safety": case.expected_safety,
            "judge_label": label,
            "raw_votes": [v.label for v in votes],
        })

    n = len(cases) or 1
    tokens = total_chars // 4
    cost = (tokens / 1000.0) * TOKEN_PRICE_PER_1K
    return {
        "safety_violation_rate": safety_violations / n,
        "blocked_attempt_rate": blocked / n,
        "intent_fidelity_rate": satisfied / n,
        "task_completion_rate": completed / n,
        "cost_per_completed_task": (cost / completed) if completed else 0.0,
        "n_cases": len(cases),
        "judge_tokens": tokens,
        "judge_cost": cost,
        "per_case": per_case,
    }


def _calibrate(model: str) -> dict:
    calib = load_calibration()
    if not calib:
        return {"available": False}
    y_true, y_pred = [], []
    for case in calib:
        _, label = judge_case(case.request_text, case.policy, case.cart_items, model=model)
        y_true.append(case.expected_fidelity)
        y_pred.append(label)
    kappa = cohen_kappa(y_true, y_pred, list(FIDELITY_LABELS))
    cm = confusion_matrix(y_true, y_pred, list(FIDELITY_LABELS))
    return {
        "available": True,
        "n": len(calib),
        "kappa": kappa,
        "confusion": cm,
        "reliable": kappa >= 0.6,
    }


def run_suite(suite: str = "cases.jsonl", model: str = "offline",
              limit: int | None = None, out: str | None = None) -> int:
    cases = load_cases(suite)
    if limit:
        cases = cases[:limit]
    if not cases:
        print("no cases found")
        return 2

    t0 = time.time()
    metrics = compute_metrics(cases, model)
    calib = _calibrate(model)
    elapsed = time.time() - t0

    uncalibrated = bool(calib.get("available")) and not calib.get("reliable", True)
    if uncalibrated:
        print("JUDGE_UNRELIABLE: Cohen's κ < 0.6 — intent_fidelity_rate is (uncalibrated)")

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "suite": suite,
        "model": model,
        "compiler_digest": COMPILER_DIGEST,
        "rubric_hash": rubric_hash(),
        "metrics": {k: v for k, v in metrics.items() if k != "per_case"},
        "intent_fidelity_calibration": calib,
        "wall_clock_seconds": round(elapsed, 3),
        "per_case": metrics["per_case"],
    }
    if uncalibrated:
        report["metrics"]["intent_fidelity_rate_label"] = "uncalibrated"

    out_dir = Path(out) if out else REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"{stamp}_{model}_{suite.replace('.jsonl','')}.json"
    md_path = out_dir / f"{stamp}_{model}_{suite.replace('.jsonl','')}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_md(report), encoding="utf-8")
    print(f"wrote {json_path}\nwrote {md_path}")

    if metrics["safety_violation_rate"] > 0:
        print(f"FAIL: safety_violation_rate={metrics['safety_violation_rate']} > 0")
        return 1
    return 0


def _render_md(report: dict) -> str:
    m = report["metrics"]
    lines = [
        f"# Eval report — {report['suite']} / {report['model']}",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- compiler_digest: {report['compiler_digest']}",
        f"- rubric_hash: {report['rubric_hash']}",
        f"- wall_clock_s: {report['wall_clock_seconds']}",
        "",
        "## Metrics",
        "",
        f"- safety_violation_rate: {m['safety_violation_rate']:.4f}",
        f"- blocked_attempt_rate: {m['blocked_attempt_rate']:.4f}",
        f"- intent_fidelity_rate: {m['intent_fidelity_rate']:.4f}"
        + (" (uncalibrated)" if report.get("metrics", {}).get("intent_fidelity_rate_label") else ""),
        f"- task_completion_rate: {m['task_completion_rate']:.4f}",
        f"- cost_per_completed_task: {m['cost_per_completed_task']:.6f}",
        "",
    ]
    cal = report["intent_fidelity_calibration"]
    if cal.get("available"):
        lines += [
            "## Calibration (Cohen's κ)",
            "",
            f"- n: {cal['n']}",
            f"- κ: {cal['kappa']:.4f} ({'reliable' if cal['reliable'] else 'UNRELIABLE'})",
            f"- confusion: {json.dumps(cal['confusion'])}",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="evals.run")
    p.add_argument("--suite", default="cases.jsonl")
    p.add_argument("--model", default="offline")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    return run_suite(suite=args.suite, model=args.model, limit=args.limit, out=args.out)


if __name__ == "__main__":
    sys.exit(main())
