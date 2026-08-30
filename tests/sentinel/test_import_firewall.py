# tests/sentinel/test_import_firewall.py
# Sentinel test: asserts import firewall - core/ and verify/ must not import LLM SDKs or agents

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

FORBIDDEN_IMPORTS = {
    # LLM SDKs
    "openai",
    "anthropic",
    "google.generativeai",
    "langchain",
    "langgraph",
    "mcp",
    # Internal agents (R0.9, R0.10)
    "openstore.agents",
    "openstore.agents.buyer_agent",
    "openstore.agents.merchant_agent",
    "openstore.agents.campaign_agent",
    "openstore.agents.llm",
}

FORBIDDEN_FROM_IMPORTS = {
    "openstore.agents",
    "openstore.agents.buyer_agent",
    "openstore.agents.merchant_agent",
    "openstore.agents.campaign_agent",
    "openstore.agents.llm",
}

ALLOWED_DIRS = {
    "src/openstore/core",
    "src/openstore/verify",
}


def check_file(filepath: Path) -> list[str]:
    """Check a single Python file for forbidden imports."""
    errors = []
    try:
        content = filepath.read_text()
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError as e:
        errors.append(f"{filepath}: Syntax error - {e}")
        return errors

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"{filepath}:{node.lineno}: Forbidden import '{alias.name}'"
                    )
                # Check prefix matches
                for forbidden in FORBIDDEN_IMPORTS:
                    if alias.name.startswith(forbidden + "."):
                        errors.append(
                            f"{filepath}:{node.lineno}: Forbidden import '{alias.name}' (matches {forbidden})"
                        )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if node.module in FORBIDDEN_FROM_IMPORTS:
                    errors.append(
                        f"{filepath}:{node.lineno}: Forbidden from-import '{node.module}'"
                    )
                for forbidden in FORBIDDEN_FROM_IMPORTS:
                    if node.module.startswith(forbidden + "."):
                        errors.append(
                            f"{filepath}:{node.lineno}: Forbidden from-import '{node.module}' (matches {forbidden})"
                        )
    return errors


def test_import_firewall():
    """Assert core/ and verify/ never import LLM SDKs or agents."""
    all_errors = []

    for allowed_dir in ALLOWED_DIRS:
        dir_path = Path(allowed_dir)
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob("*.py"):
            errors = check_file(py_file)
            all_errors.extend(errors)

    if all_errors:
        print("IMPORT FIREWALL VIOLATIONS:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        pytest.fail(f"Import firewall violated: {len(all_errors)} forbidden imports found")


if __name__ == "__main__":
    test_import_firewall()
    print("Import firewall OK")
