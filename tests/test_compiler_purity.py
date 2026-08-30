"""§10 compiler purity + policy tests (IMPLEMENTATION_SPEC §3)."""

import ast
import importlib.util
from pathlib import Path

import pytest

from openstore import compiler
from openstore.compiler import CompilerContext, CompilerItem, CompilerPolicy, compile_decision
from openstore.canonical import canonical_json_bytes


def test_compiler_module_is_pure():
    spec = importlib.util.find_spec("openstore.compiler")
    tree = ast.parse(Path(spec.origin).read_text())
    forbidden = {"time", "datetime", "random", "secrets", "uuid", "os", "socket"}
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.Import):
            mod = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
        if mod in forbidden:
            raise AssertionError(f"compiler imports forbidden module: {mod}")


def test_legacy_policy_version_raises():
    with pytest.raises(compiler.LegacyPolicyError):
        compile_decision(
            (CompilerItem("A", 1, 100, ()),),
            CompilerPolicy(policy_version=1, merchant_id="m", currency="INR",
                           max_spend_per_tx_minor=1000, max_spend_total_minor=1000,
                           max_transactions=10, allowed_tags=(), tag_mode="all",
                           blocked_skus=(), not_before=0, expires_at=2_000_000_000),
            CompilerContext("m", "INR", 1_000, 0, 0),
        )


def _policy(**kw):
    base = dict(policy_version=2, merchant_id="m", currency="INR",
                max_spend_per_tx_minor=1000, max_spend_total_minor=5000,
                max_transactions=10, allowed_tags=(), tag_mode="all",
                blocked_skus=(), not_before=0, expires_at=2_000_000_000)
    base.update(kw)
    return CompilerPolicy(**base)


def test_never_allows_over_per_tx():
    policy = _policy(max_spend_per_tx_minor=1000)
    verdict = compile_decision((CompilerItem("A", 1, 1500, ()),), policy,
                               CompilerContext("m", "INR", 1_000, 0, 0))
    assert verdict.verdict == "DENY"
    assert verdict.reason_code == "spend_per_tx_exceeded"


def test_never_allows_over_cumulative():
    policy = _policy(max_spend_per_tx_minor=5000, max_spend_total_minor=2000)
    verdict = compile_decision((CompilerItem("A", 1, 1500, ()),), policy,
                               CompilerContext("m", "INR", 1_000, spent_minor=1000, transactions_count=1))
    assert verdict.verdict == "DENY"
    assert verdict.reason_code == "spend_cumulative_exceeded"


def test_monotonic_under_item_addition():
    # Adding an allowed, in-budget item can never flip ALLOW -> DENY.
    policy = _policy(max_spend_per_tx_minor=10_000, max_spend_total_minor=100_000)
    ctx = CompilerContext("m", "INR", 1_000, 0, 0)
    base = compile_decision((CompilerItem("A", 1, 500, ()),), policy, ctx)
    assert base.verdict == "ALLOW"
    for extra in (("B", 1, 300, ()), ("C", 2, 100, ()), ("D", 1, 700, ())):
        v = compile_decision((CompilerItem("A", 1, 500, ()), CompilerItem(*extra)), policy, ctx)
        assert v.verdict == "ALLOW", extra


def test_tag_mode_all_rejects_superset_tags():
    # tag_mode "all": an item's tags must be a SUBSET of the allowed tags, so an
    # item carrying an extra (superset) tag is rejected; an exact-match passes.
    policy = _policy(allowed_tags=("dairy-free",), tag_mode="all")
    ok = compile_decision((CompilerItem("A", 1, 100, ("dairy-free",)),), policy,
                          CompilerContext("m", "INR", 1_000, 0, 0))
    assert ok.verdict == "ALLOW"
    bad = compile_decision((CompilerItem("A", 1, 100, ("dairy-free", "vegan")),), policy,
                           CompilerContext("m", "INR", 1_000, 0, 0))
    assert bad.verdict == "DENY"
    assert bad.reason_code == "tag_violation"
