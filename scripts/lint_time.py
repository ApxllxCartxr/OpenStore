#!/usr/bin/env python3
"""Time lint — UTC everywhere, and no naive datetime.

A naive datetime is a wrong answer that formats correctly. In this system it is
worse than usual: `expiry_utc` is inside the `cart_hash` preimage, so a naive
timestamp is a binding the Consumer did not agree to, and the payment window is
15 minutes, which is shorter than most timezone offsets.

What is refused, over `src/openstore/sidecar/` by default:

- `datetime.now()` with no tzinfo argument
- `datetime.utcnow()` and `datetime.utcfromtimestamp()` — naive, and deprecated
  in 3.12 for exactly this reason
- `datetime.fromtimestamp(...)` with no `tz=`
- `date.today()` — there is no such thing as today without a timezone
- `datetime(...)` constructed with no `tzinfo=`

The one deliberate exception is the financial year (ADR-0020), which is
evaluated in `Asia/Kolkata` and not in UTC. That is a *local calendar bucket*
derived from a UTC instant, not a naive timestamp, so it does not trip this
lint — `dispatched_at.astimezone(ZoneInfo("Asia/Kolkata"))` is an aware value
throughout.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFAULT_ROOTS = [Path("src/openstore/sidecar")]

_NAIVE_ALWAYS = {"utcnow", "utcfromtimestamp"}


def _attr_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _has_kwarg(node: ast.Call, *names: str) -> bool:
    return any(kw.arg in names for kw in node.keywords)


class TimeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _attr_name(node.func)

        if name in _NAIVE_ALWAYS:
            self.problems.append(
                (node.lineno, f"datetime.{name}() is naive and deprecated; use datetime.now(UTC)")
            )
        elif name == "now" and not (node.args or _has_kwarg(node, "tz")):
            self.problems.append(
                (node.lineno, "datetime.now() with no tz is local time; use datetime.now(UTC)")
            )
        elif name == "fromtimestamp" and not _has_kwarg(node, "tz"):
            self.problems.append((node.lineno, "fromtimestamp() with no tz= is naive; pass tz=UTC"))
        elif name == "today":
            self.problems.append(
                (node.lineno, "today() has no timezone; use datetime.now(UTC).date()")
            )
        elif name == "datetime" and not _has_kwarg(node, "tzinfo"):
            # datetime(2026, 4, 1) — a literal with no tzinfo is naive.
            if len(node.args) >= 3:
                self.problems.append(
                    (node.lineno, "datetime(...) built with no tzinfo= is naive; pass tzinfo=UTC")
                )

        self.generic_visit(node)


def check(paths: list[Path]) -> int:
    failures = 0
    for path in sorted(p for root in paths for p in _python_files(root)):
        source = path.read_text(encoding="utf-8")
        visitor = TimeVisitor()
        visitor.visit(ast.parse(source, filename=str(path)))
        for lineno, message in visitor.problems:
            print(f"{path}:{lineno}: TIME {message}")
            failures += 1
    return failures


def _python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", type=Path, default=None)
    args = ap.parse_args()
    roots = args.paths or DEFAULT_ROOTS
    failures = check(roots)
    if failures:
        print(f"\n{failures} time-lint violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
