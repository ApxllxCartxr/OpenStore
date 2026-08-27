"""Enforce buyer_agent isolation: no imports from merchant/ or merchant_agent/."""
import ast
import sys
from pathlib import Path


def test_buyer_agent_isolation():
    """Walk buyer_agent/ ASTs and fail on any import of merchant*."""
    buyer_agent_root = Path("buyer_agent")
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