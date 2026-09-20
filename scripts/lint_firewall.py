#!/usr/bin/env python3
"""Import firewall — no cross-root import, in either direction, ever.

Four roots, and they may talk over HTTP and signed webhooks only:

    src/openstore/sidecar/   the product
    demo/merchant-site/      Surface 2, a Merchant like any other
    demo/buyer-chat/         Surface 3, an air-gapped stranger
    adapters/woocommerce/    a second trait implementation (D3, later)

The demo Merchant and the demo agent prove the integration is HTTP-only. A
single shared import — even a type — quietly turns the proof into a
demonstration that two halves of one program can call each other, which nobody
needed proving. Runtime, types and tests all count (SPEC §1).

`demo/buyer-chat/src/lib/identity/` is authored by Track P (§5) and is still the
chat's own code in the chat's own root: same root, no crossing.

`design/` is assets only. A `.ts` file in there is a shared module wearing a
hat, and it would be importable from both SvelteKit roots at once.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOTS: dict[str, Path] = {
    "sidecar": Path("src/openstore/sidecar"),
    "merchant-site": Path("demo/merchant-site"),
    "buyer-chat": Path("demo/buyer-chat"),
    "woocommerce": Path("adapters/woocommerce"),
}

#: What each root must never mention. Checked as import *targets*, not as free
#: text, so prose about the sidecar in a comment is fine.
FORBIDDEN_TOKENS: dict[str, tuple[str, ...]] = {
    "sidecar": ("merchant-site", "merchant_site", "buyer-chat", "buyer_chat"),
    "merchant-site": ("openstore.sidecar", "openstore/sidecar", "buyer-chat", "buyer_chat"),
    "buyer-chat": ("openstore.sidecar", "openstore/sidecar", "merchant-site", "merchant_site"),
    "woocommerce": ("openstore.sidecar", "openstore/sidecar", "buyer-chat", "buyer_chat"),
}

DESIGN_DIR = Path("design")
#: Assets. Anything executable or importable is not an asset.
ASSET_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico",
        ".woff", ".woff2", ".ttf", ".otf",
        ".css",
        ".json", ".md", ".txt",
        ".mp4", ".webm",
        "",  # extensionless: .gitkeep and friends
    }
)  # fmt: skip

#: JS/TS/Svelte import and re-export forms, plus dynamic import().
_JS_IMPORT = re.compile(
    r"""(?:^|\s)(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]"""
    r"""|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)

_PY_SUFFIX = ".py"
_JS_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".svelte", ".jsx", ".tsx"})


def _python_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def _js_imports(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for match in _JS_IMPORT.finditer(text):
        target = next(g for g in match.groups() if g)
        lineno = text.count("\n", 0, match.start()) + 1
        found.append((lineno, target))
    return found


def _files(root: Path) -> list[Path]:
    skip = {"node_modules", "__pycache__", ".svelte-kit", "build", "dist", ".venv"}
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and not skip & set(p.parts) and p.suffix in _JS_SUFFIXES | {_PY_SUFFIX}
    ]


def check_roots() -> int:
    failures = 0
    for name, root in ROOTS.items():
        if not root.exists():
            continue
        forbidden = FORBIDDEN_TOKENS[name]
        for path in sorted(_files(root)):
            imports = _python_imports(path) if path.suffix == _PY_SUFFIX else _js_imports(path)
            for lineno, target in imports:
                hit = next((tok for tok in forbidden if tok in target), None)
                if hit:
                    print(
                        f"{path}:{lineno}: FIREWALL {name!r} imports {target!r} "
                        f"— the roots talk over HTTP, never by import"
                    )
                    failures += 1
    return failures


def check_design() -> int:
    if not DESIGN_DIR.exists():
        return 0
    failures = 0
    for path in sorted(DESIGN_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() not in ASSET_SUFFIXES:
            print(
                f"{path}: FIREWALL design/ holds assets only — {path.suffix!r} is "
                f"importable, and a module in here is shared code between two roots"
            )
            failures += 1
    return failures


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    failures = check_roots() + check_design()
    if failures:
        print(f"\n{failures} firewall violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
