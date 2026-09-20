#!/usr/bin/env python3
"""Money lint — paise integers, and no float anywhere in a money path.

A float in a money path does not fail; it rounds. That is why this is a build
break and not a review comment: the bug ships looking correct and shows up as a
one-paise reconciliation drift six weeks later.

What is refused, over `src/openstore/sidecar/` by default:

- a float literal
- `float(...)`
- the `round()` builtin — it is banker's rounding, and §16.11 requires
  ROUND_HALF_UP; the two disagree on exactly the .5 cases money hits
- true division `/` — `int / int` silently produces a float

`/` is genuinely needed for the tax extraction (§16.11 step 5), which is exact
decimal arithmetic. That line carries `# money-lint: decimal` — loud, greppable,
and it marks every place in the system where money is divided, which is a list
worth being able to read.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ALLOW_MARKER = "# money-lint: decimal"

DEFAULT_ROOTS = [Path("src/openstore/sidecar")]


class MoneyVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.problems: list[tuple[int, str]] = []

    def _allowed(self, lineno: int) -> bool:
        return ALLOW_MARKER in self.lines[lineno - 1]

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float):
            self.problems.append(
                (node.lineno, f"float literal {node.value!r} — money is paise, an int")
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name == "float":
            self.problems.append((node.lineno, "float() — money is paise, an int"))
        elif name == "round":
            self.problems.append(
                (
                    node.lineno,
                    "round() is banker's rounding; §16.11 requires "
                    "Decimal.quantize(..., ROUND_HALF_UP)",
                )
            )
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div) and not self._allowed(node.lineno):
            self.problems.append(
                (
                    node.lineno,
                    f"true division '/' produces a float; use '//' or exact Decimal "
                    f"arithmetic marked '{ALLOW_MARKER}'",
                )
            )
        self.generic_visit(node)


def check(paths: list[Path]) -> int:
    failures = 0
    for path in sorted(p for root in paths for p in _python_files(root)):
        source = path.read_text(encoding="utf-8")
        visitor = MoneyVisitor(path, source)
        visitor.visit(ast.parse(source, filename=str(path)))
        for lineno, message in visitor.problems:
            print(f"{path}:{lineno}: MONEY {message}")
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
        print(f"\n{failures} money-lint violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
