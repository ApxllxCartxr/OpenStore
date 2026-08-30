"""Voting + rank-agreement statistics for the judge (AGENT_LAYER.md §3.3/§3.4)."""

from __future__ import annotations

from collections import Counter


def majority_vote(labels: list[str]) -> str:
    """Majority label across the three seeds; exact tie -> `unanswerable` (R3.3b)."""
    counts = Counter(labels)
    top = counts.most_common()
    if len(top) >= 2 and top[0][1] == top[1][1]:
        return "unanswerable"
    return top[0][0]


def confusion_matrix(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict:
    """Per-class confusion. `labels` defines row/col order."""
    mat = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(y_true, y_pred):
        mat.setdefault(t, {p: 0 for p in labels})
        mat[t][p] = mat[t].get(p, 0) + 1
    return mat


def cohen_kappa(y_true: list[str], y_pred: list[str], labels: list[str]) -> float:
    """Cohen's κ over the labelled classes (R3.4a)."""
    n = len(y_true)
    if n == 0:
        return 0.0
    cm = confusion_matrix(y_true, y_pred, labels)
    po = sum(cm[t][t] for t in labels) / n
    pe = 0.0
    for t in labels:
        row = sum(cm[t].values())
        col = sum(cm[r][t] for r in labels)
        pe += (row / n) * (col / n)
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)
