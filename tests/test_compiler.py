import ast
import importlib.util
from pathlib import Path

import pytest

from openstore import compiler
from openstore.compiler import (
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    CompilerVerdict,
    compile_decision,
)

PINNED_DIGEST = "sha256:96543354c15751ccfdd7700c1cf9d1e0735559cdc166313186d593adcee9b03b"


def _policy(**over):
    base = dict(
        policy_version=2,
        merchant_id="gelateria-roma",
        currency="INR",
        max_spend_per_tx_minor=50000,
        max_spend_total_minor=200000,
        max_transactions=10,
        allowed_tags=("dairy-free", "vegan"),
        tag_mode="all",
        blocked_skus=(),
        not_before=1787000000,
        expires_at=1788000000,
    )
    base.update(over)
    return CompilerPolicy(**base)


def _ctx(**over):
    base = dict(
        merchant_id="gelateria-roma",
        currency="INR",
        evaluated_at_unix=1787000900,
        spent_minor=0,
        transactions_count=0,
    )
    base.update(over)
    return CompilerContext(**base)


def _items(*specs):
    return tuple(CompilerItem(sku=s, qty=q, unit_minor=u, tags=t) for s, q, u, t in specs)


def test_compiler_digest_is_pinned():
    assert compiler.COMPILER_DIGEST == PINNED_DIGEST


def test_allow_happy_path():
    v = compile_decision(
        _items(("GEL-VAN-500", 2, 21000, ("dairy-free", "vegan"))),
        _policy(),
        _ctx(),
    )
    assert v.verdict == "ALLOW"
    assert v.reason_code is None
    assert len(v.transcript) == 10


def test_tag_violation():
    v = compile_decision(
        _items(("GEL-RUM-500", 1, 26000, ("alcohol", "dessert"))),
        _policy(),
        _ctx(),
    )
    assert v.verdict == "DENY"
    assert v.reason_code == "tag_violation"


def test_empty_cart_raises():
    with pytest.raises(ValueError):
        compile_decision((), _policy(), _ctx())


def test_legacy_policy_rejected():
    with pytest.raises(compiler.LegacyPolicyError):
        compile_decision(_items(("X", 1, 100, ())), _policy(policy_version=1), _ctx())


def test_spend_cumulative_exceeded():
    v = compile_decision(
        _items(("GEL-VAN-500", 2, 21000, ("vegan",))),
        _policy(max_spend_total_minor=30000),
        _ctx(spent_minor=20000),
    )
    assert v.reason_code == "spend_cumulative_exceeded"


def test_any_tag_mode():
    # "any": ALLOW iff item shares at least one allowed tag (legacy behaviour)
    v = compile_decision(
        _items(("GEL-RUM-500", 1, 26000, ("vegan", "alcohol"))),
        _policy(tag_mode="any"),
        _ctx(),
    )
    assert v.verdict == "ALLOW"
    bad = compile_decision(
        _items(("GEL-RUM-500", 1, 26000, ("alcohol", "dessert"))),
        _policy(tag_mode="any"),
        _ctx(),
    )
    assert bad.verdict == "DENY"


def test_compiler_module_is_pure():
    """AST walk: the compiler imports no clock/db/network/trace (R3.2a)."""
    path = Path(importlib.util.find_spec("openstore.compiler").origin)
    tree = ast.parse(path.read_text())
    forbidden = {
        "time", "datetime", "random", "secrets", "uuid", "os",
        "merchant", "sqlmodel", "razorpay", "fastapi",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert n.name.split(".")[0] not in forbidden
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden
