"""The harness goes red on planted violations, and green otherwise.

A1's DONE WHEN. A guardrail nobody has watched fail is a guardrail nobody knows
works: every lint here is run against a file written to fail it, and against the
real tree, which must stay clean.

The three the plan names are an unregistered code, float money, and a cross-root
import. The others are planted because each one is a way money or time goes
wrong quietly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


# ── The real tree stays clean ────────────────────────────────────────────────


def test_money_lint_is_green_on_the_tree() -> None:
    result = _run("lint_money.py")
    assert result.returncode == 0, result.stdout


def test_time_lint_is_green_on_the_tree() -> None:
    result = _run("lint_time.py")
    assert result.returncode == 0, result.stdout


def test_firewall_is_green_on_the_tree() -> None:
    result = _run("lint_firewall.py")
    assert result.returncode == 0, result.stdout


# ── Planted violation 1: an unregistered reason code ─────────────────────────


def test_planted_unregistered_code_is_caught(tmp_path: Path) -> None:
    """A code that exists in prose and in no enum. The scanner reads the
    normative files, so the plant goes into a copy of one."""
    plant = REPO / "PLAN-planted-violation.md"
    plant.write_text(
        "Refuses with `insufficient-vibes`, which is in no closed set.\n", encoding="utf-8"
    )
    try:
        result = _run("registry_diff.py", "--prose")
        assert result.returncode == 1
        assert "insufficient-vibes" in result.stdout
    finally:
        plant.unlink()

    assert _run("registry_diff.py", "--prose").returncode == 0, "tree not restored"


def test_planted_stale_codes_doc_is_caught(tmp_path: Path) -> None:
    """Editing the generated doc instead of the enum."""
    doc = REPO / "docs/CODES.md"
    original = doc.read_text(encoding="utf-8")
    doc.write_text(original + "\n- `hand-edited-code`\n", encoding="utf-8")
    try:
        result = _run("registry_diff.py", "--check")
        assert result.returncode == 1
        assert "stale" in result.stdout
    finally:
        doc.write_text(original, encoding="utf-8")

    assert _run("registry_diff.py", "--check").returncode == 0, "tree not restored"


# ── Planted violation 2: float money ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("total = 12.5\n", "float literal"),
        ("total = float(amount_minor)\n", "float()"),
        ("total = round(amount_minor * rate)\n", "round()"),
        ("share = amount_minor / 3\n", "true division"),
    ],
    ids=["literal", "cast", "round", "division"],
)
def test_planted_float_money_is_caught(tmp_path: Path, source: str, expected: str) -> None:
    planted = tmp_path / "money_violation.py"
    planted.write_text(source, encoding="utf-8")
    result = _run("lint_money.py", str(planted))
    assert result.returncode == 1, result.stdout
    assert expected in result.stdout


def test_decimal_division_passes_when_marked(tmp_path: Path) -> None:
    """§16.11's tax extraction needs exact decimal division. The marker makes
    every place money is divided greppable rather than invisible."""
    planted = tmp_path / "ok.py"
    planted.write_text(
        "from decimal import Decimal\n"
        "tax = Decimal(inc) * Decimal(bp) / Decimal(10000 + bp)  # money-lint: decimal\n",
        encoding="utf-8",
    )
    assert _run("lint_money.py", str(planted)).returncode == 0


# ── Planted violation 3: a cross-root import ─────────────────────────────────


def test_planted_cross_root_import_is_caught() -> None:
    """The sidecar reaching into the demo Merchant. Planted in the real tree,
    because the firewall's whole job is knowing where a file lives."""
    plant = REPO / "src/openstore/sidecar/core/planted_violation.py"
    plant.write_text("from demo.merchant_site import orders\n", encoding="utf-8")
    try:
        result = _run("lint_firewall.py")
        assert result.returncode == 1
        assert "FIREWALL" in result.stdout
        assert "planted_violation.py" in result.stdout
    finally:
        plant.unlink()

    assert _run("lint_firewall.py").returncode == 0, "tree not restored"


def test_planted_design_module_is_caught() -> None:
    """A `.ts` file in `design/` is shared code between two SvelteKit roots
    wearing an asset's hat."""
    plant = REPO / "design/shared.ts"
    plant.write_text("export const oops = 1;\n", encoding="utf-8")
    try:
        result = _run("lint_firewall.py")
        assert result.returncode == 1
        assert "assets only" in result.stdout
    finally:
        plant.unlink()

    assert _run("lint_firewall.py").returncode == 0, "tree not restored"


# ── Planted violation 4: naive time ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from datetime import datetime\nx = datetime.now()\n", "no tz"),
        ("from datetime import datetime\nx = datetime.utcnow()\n", "deprecated"),
        ("from datetime import datetime\nx = datetime.fromtimestamp(0)\n", "naive"),
        ("from datetime import date\nx = date.today()\n", "no timezone"),
        ("from datetime import datetime\nx = datetime(2026, 4, 1)\n", "naive"),
    ],
    ids=["now", "utcnow", "fromtimestamp", "today", "constructor"],
)
def test_planted_naive_time_is_caught(tmp_path: Path, source: str, expected: str) -> None:
    planted = tmp_path / "time_violation.py"
    planted.write_text(source, encoding="utf-8")
    result = _run("lint_time.py", str(planted))
    assert result.returncode == 1, result.stdout
    assert expected in result.stdout


def test_aware_time_passes(tmp_path: Path) -> None:
    planted = tmp_path / "ok.py"
    planted.write_text(
        "from datetime import UTC, datetime\n"
        "from zoneinfo import ZoneInfo\n"
        "now = datetime.now(UTC)\n"
        "fy = now.astimezone(ZoneInfo('Asia/Kolkata')).year\n",
        encoding="utf-8",
    )
    assert _run("lint_time.py", str(planted)).returncode == 0
