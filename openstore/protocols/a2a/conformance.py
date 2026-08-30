"""A2A adapter conformance (INTEROP_SPEC §6.4 / §7)."""

from __future__ import annotations


def conformance_pass() -> bool:
    import ast
    import os

    from openstore.protocols.a2a import A2A_EXTERNAL_FIELDS, agent_card

    assert agent_card("https://example.com")["url"].endswith("/.well-known/agent.json")
    assert set(A2A_EXTERNAL_FIELDS)

    # R6.4a — the merchant reasoning agent MUST NOT import core write functions.
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    agent_dir = os.path.join(repo, "merchant_agent")
    if os.path.isdir(agent_dir):
        banned = ("create_cart", "update_cart", "initiate_checkout", "confirm_checkout")
        for root, _dirs, files in os.walk(agent_dir):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(root, fn), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=fn)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.endswith("openstore.core.api"):
                            for n in node.names:
                                if n.name in banned:
                                    raise AssertionError(f"{fn} imports core write {n.name}")
                    if isinstance(node, ast.Import):
                        for n in node.names:
                            if n.name.endswith("openstore.core.api"):
                                raise AssertionError(f"{fn} imports openstore.core.api")
    return True
