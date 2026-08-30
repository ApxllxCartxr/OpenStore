"""Enforce buyer_agent isolation: no imports from merchant/ or merchant_agent/."""
import ast
import sys
from pathlib import Path


def test_buyer_agent_isolation():
    """Walk buyer_agent/ ASTs and fail on any import of merchant*."""
    buyer_agent_root = Path("reference/buyer_agent")
    forbidden_prefixes = ("merchant", "merchant_agent")
    violations = []

    for py_file in buyer_agent_root.rglob("*.py"):
        if py_file.name == "__pycache__":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in forbidden_prefixes):
                        violations.append(
                            f"{py_file}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and any(node.module.startswith(p) for p in forbidden_prefixes):
                    violations.append(
                        f"{py_file}: from {node.module} import ..."
                    )

    if violations:
        print("ISOLATION VIOLATIONS:")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)


if __name__ == "__main__":
    test_buyer_agent_isolation()
    print("Isolation check passed: buyer_agent imports nothing from merchant/ or merchant_agent/")


def test_money_path_does_not_import_evals():
    """R3.3a (AGENT_LAYER.md): the judge/ eval harness MUST NOT be importable
    from the money path. `merchant/` is deleted; `openstore/` is the live
    package that decides whether money moves, so assert it never imports `evals`."""
    root = Path("openstore")
    forbidden = ("evals",)
    violations = []
    for py_file in root.rglob("*.py"):
        if py_file.name == "__pycache__":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden or alias.name.startswith("evals."):
                        violations.append(f"{py_file}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module in forbidden or node.module.startswith("evals")):
                    violations.append(f"{py_file}: from {node.module} import ...")
    if violations:
        print("MONEY-PATH ISOLATION VIOLATIONS:")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)


if __name__ == "__main__":
    test_money_path_does_not_import_evals()
    print("Isolation check passed: openstore/ imports nothing from evals/")


def test_growth_cannot_move_money():
    """R7.1a/R7.1b (GROWTH_AGENTS.md §7.1): nothing under `growth/` may import a
    money-moving, signing-key, or PSP module. The only permitted contact with the
    authorization layer is the read-only `openstore.compiler` (compile_decision)
    and its frozen data types, plus `openstore.blast_radius` and `openstore.config`.
    """
    root = Path("growth")
    if not root.exists():
        return
    forbidden = {
        "razorpay",
        "openstore.gateway",
        "openstore.runtime",
        "openstore.hold",
        "openstore.ledger",
        "openstore.evidence",
        "openstore.attest",
        "openstore.mcp_server",
        "openstore.surfaces",
        "openstore.server",
        "openstore.auth",
        "openstore.client",
        "openstore.core",
        "openstore.verify",
        "openstore.agent_plan",
    }
    allowed_openstore = {
        "openstore.compiler",
        "openstore.blast_radius",
        "openstore.config",
        "openstore.canonical",
        "openstore.models",
    }
    violations = []
    for py_file in root.rglob("*.py"):
        if py_file.name == "__pycache__":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name
                    if mod in forbidden or mod.startswith("razorpay"):
                        violations.append(f"{py_file}: import {mod}")
                    if mod.startswith("openstore.") and mod not in allowed_openstore \
                            and not any(mod == a or mod.startswith(a + ".") for a in allowed_openstore):
                        violations.append(
                            f"{py_file}: forbidden openstore import {mod}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in forbidden or mod.startswith("razorpay"):
                    violations.append(f"{py_file}: from {mod} import ...")
                if mod.startswith("openstore.") and mod not in allowed_openstore \
                        and not any(mod == a or mod.startswith(a + ".") for a in allowed_openstore):
                    violations.append(f"{py_file}: forbidden from {mod} import ...")

    if violations:
        print("GROWTH MONEY-MOVEMENT VIOLATIONS:")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)


if __name__ == "__main__":
    test_growth_cannot_move_money()
    print("Isolation check passed: growth/ cannot move money")