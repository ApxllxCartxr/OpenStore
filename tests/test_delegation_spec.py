"""Tests for the remaining DELEGATION_AND_ORCHESTRATION features:

- §5.1 compiler v1.1.0 (spend_envelope check, merchant_lock set-membership, digest dispatch)
- §5.3 bundle authority.delegation verification (R5.3)
- §5.4 persistence tables
- §6 AAL depth / multi-merchant caps
- §4 fork detection + CLI --detect-forks
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from openstore import compiler
from openstore.core.authority import cap_aal_by_depth, cap_aal_multi_merchant
from openstore.delegation import (
    DELEGATION_DIGEST,
    SPENDCHAIN_DIGEST,
    BudgetEnvelope,
    DelegationLink,
    Grant,
    SpendEntry,
    detect_forks,
    sign_link,
    sign_spend_entry,
    verify_delegation_chain,
    verify_spend_chain,
)
from openstore.verify import _verify_delegation_section
from openstore.verify.cli import main


# ---------- §5.1 compiler v1.1.0 ----------


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _compiler_items():
    return (
        compiler.CompilerItem("GEL-VAN-500", 1, 5000, ("vegan",)),
        compiler.CompilerItem("GEL-VAN-300", 1, 3000, ("vegan",)),
    )


def _v11_policy(merchant_ids=("M1", "M2"), merchant_id="M1"):
    return compiler.CompilerPolicy(
        policy_version=2,
        merchant_id=merchant_id,
        merchant_ids=merchant_ids,
        currency="INR",
        max_spend_per_tx_minor=10000,
        max_spend_total_minor=1_000_000,
        max_transactions=50,
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        not_before=0,
        expires_at=9_000_000_000,
    )


def _ctx(merchant_id="M1", spent=0):
    return compiler.CompilerContext(
        merchant_id=merchant_id, currency="INR",
        evaluated_at_unix=1_000, spent_minor=spent, transactions_count=0,
    )


def test_compiler_v11_spend_envelope_pass():
    v = compiler.compile_decision_v11(
        _compiler_items(), _v11_policy(), _ctx(),
        envelope_budget_minor=10_000, chain_spent_minor=0,
    )
    assert v.verdict == "ALLOW"
    checks = {c["check"] for c in v.transcript}
    assert "spend_envelope" in checks
    assert "spend_cumulative" in checks


def test_compiler_v11_spend_envelope_exceeded():
    v = compiler.compile_decision_v11(
        _compiler_items(), _v11_policy(), _ctx(),
        envelope_budget_minor=7_000, chain_spent_minor=0,
    )
    assert v.verdict == "DENY"
    assert v.reason_code == "spend_envelope_exceeded"


def test_compiler_v11_merchant_lock_set_membership():
    # M1 in the allowlist -> allowed
    assert compiler.compile_decision_v11(
        _compiler_items(), _v11_policy(), _ctx(merchant_id="M2"),
        envelope_budget_minor=10_000, chain_spent_minor=0,
    ).verdict == "ALLOW"
    # M9 not in the allowlist -> merchant_mismatch
    v = compiler.compile_decision_v11(
        _compiler_items(), _v11_policy(), _ctx(merchant_id="M9"),
        envelope_budget_minor=10_000, chain_spent_minor=0,
    )
    assert v.verdict == "DENY"
    assert v.reason_code == "merchant_mismatch"


def test_compiler_v11_digest_constant():
    assert compiler.COMPILER_DIGEST_V11 == (
        "sha256:10230521796f94d9039a8f25d0c03c0e48f870b53ad54885001a3a440f9f7ed9"
    )


# ---------- §5.3 delegation section verification (R5.3) ----------


def _build_delegation_section():
    rkey, lkey = _key(), _key()
    root = DelegationLink(
        link_id="dl_root", parent_link_id=None, depth=0, envelope_id="env_A",
        delegator_thumbprint="cred_human", delegate_thumbprint="tA",
        grant=Grant(100000, "inr", ("M1", "M2"), ("vegan",), "all", (), 10, 0, 9_000),
        issued_at="2026-09-01T00:00:00Z", signature="",
    )
    leaf = DelegationLink(
        link_id="dl_A", parent_link_id="dl_root", depth=1, envelope_id="env_B",
        delegator_thumbprint="tA", delegate_thumbprint="tB",
        grant=Grant(20000, "inr", ("M2",), ("vegan",), "all", (), 3, 0, 8_000),
        issued_at="2026-09-01T00:01:00Z", signature="",
    )
    leaf = DelegationLink.from_dict(
        {**leaf.to_dict(), "signature": sign_link(leaf, lkey, "tA")}
    )
    chain = [root.to_dict(), leaf.to_dict()]

    mkey = _key()
    gen = "sha256:" + __import__("hashlib").sha256(
        __import__("openstore.canonical", fromlist=["canonical_json_bytes"]).canonical_json_bytes(
            {"envelope_id": "env_B"}
        )
    ).hexdigest()
    e0 = SpendEntry("env_B", 0, "SPEND", 5000, "bundle1", gen, "2026-09-01T00:00:00Z", "")
    e0 = SpendEntry.from_dict(
        {**e0.to_dict(), "signature": sign_spend_entry(e0, mkey, "merchant")}
    )
    spend_chain = [e0.to_dict()]

    return {
        "chain": chain,
        "delegation_digest": DELEGATION_DIGEST,
        "envelope_id": "env_B",
        "envelope_budget_minor": 20000,
        "currency": "inr",
        "expires_at": 8000,
        "spend_chain": spend_chain,
        "spendchain_digest": SPENDCHAIN_DIGEST,
        "sequencer": {"type": "none"},
        "depth": 1,
        "merchant_ids": ["M2"],
    }, mkey


def test_delegation_section_verifies():
    deleg, mkey = _build_delegation_section()
    assert _verify_delegation_section(deleg, mkey) is None


def test_delegation_section_bad_digest_rejected():
    deleg, mkey = _build_delegation_section()
    bad = {**deleg, "delegation_digest": "sha256:deadbeef"}
    assert _verify_delegation_section(bad, mkey) == "delegation_digest_mismatch"


def test_delegation_section_bad_spendchain_digest_rejected():
    deleg, mkey = _build_delegation_section()
    bad = {**deleg, "spendchain_digest": "sha256:deadbeef"}
    assert _verify_delegation_section(bad, mkey) == "spendchain_digest_mismatch"


# ---------- §5.4 persistence tables ----------


def test_delegation_tables_exist():
    from openstore.models import DelegationLinkRow, SpendChainEntry

    assert DelegationLinkRow.__tablename__ == "delegationlinkrow"
    assert SpendChainEntry.__tablename__ == "spendchainentry"
    # UniqueConstraint("envelope_id", "sequence")
    constraints = [
        c for c in SpendChainEntry.__table_args__
        if hasattr(c, "columns")
    ]
    assert any(
        set(c.columns.keys()) == {"envelope_id", "sequence"} for c in constraints
    )


# ---------- §6 AAL depth / multi-merchant caps ----------


def _all_true_preds():
    from openstore.aal import Predicates

    return Predicates(
        e1_agent_authenticated=True, e2_policy_signature_valid=True,
        e3_assertion_fresh=True, e4_user_verified=True, e5_cart_bound=True,
        e6_catalog_attested=True, e7_compiler_allow=True,
        e8_intent_recorded=True, e9_notified=True,
    )


def test_aal_depth_cap_zero_is_noop():
    from openstore.aal import resolve_aal

    lvl, reasons = resolve_aal(_all_true_preds(), 2)
    assert (lvl, reasons) == (3, ())
    assert cap_aal_by_depth(3, (), 0) == (3, ())


def test_aal_depth_cap_binds():
    assert cap_aal_by_depth(3, (), 1) == (2, ("delegated_depth_capped",))
    assert cap_aal_by_depth(3, (), 2) == (2, ("delegated_depth_capped",))
    assert cap_aal_by_depth(3, (), 3) == (1, ("delegated_depth_capped",))


def test_aal_multi_merchant_unsequenced_caps():
    assert cap_aal_multi_merchant(3, (), merchant_count=2, sequencer_type="none") == (
        1, ("multi_merchant_envelope_unsequenced",)
    )
    # single-merchant envelope is unaffected
    assert cap_aal_multi_merchant(3, (), merchant_count=1, sequencer_type="none") == (3, ())


# ---------- §4 fork detection + CLI ----------


def _bundle_with_spend(bundle_id, envelope_id, sequence, sig):
    return {
        "bundle_id": bundle_id,
        "authority": {
            "delegation": {
                "spend_chain": [
                    {"entry_type": "SPEND", "envelope_id": envelope_id,
                     "sequence": sequence, "ref": bundle_id, "signature": sig}
                ]
            }
        },
    }


def test_detect_forks_finds_conflict():
    b1 = _bundle_with_spend("b1", "env_X", 0, "sigA")
    b2 = _bundle_with_spend("b2", "env_X", 0, "sigB")
    proofs = detect_forks([b1, b2])
    assert len(proofs) == 1
    assert proofs[0]["envelope_id"] == "env_X"
    assert proofs[0]["reason"] == "fork_detected"


def test_detect_forks_no_false_positive():
    b1 = _bundle_with_spend("b1", "env_X", 0, "sigA")
    b2 = _bundle_with_spend("b2", "env_X", 1, "sigA")
    assert detect_forks([b1, b2]) == []


def test_cli_detect_forks(tmp_path):
    d = tmp_path / "bundles"
    d.mkdir()
    (d / "b1.json").write_text(json.dumps(_bundle_with_spend("b1", "env_X", 0, "sigA")))
    (d / "b2.json").write_text(json.dumps(_bundle_with_spend("b2", "env_X", 0, "sigB")))
    code = main(["--detect-forks", str(d)])
    assert code == 1  # forks found


def test_cli_detect_forks_clean(tmp_path):
    d = tmp_path / "bundles"
    d.mkdir()
    (d / "b1.json").write_text(json.dumps(_bundle_with_spend("b1", "env_X", 0, "sigA")))
    (d / "b2.json").write_text(json.dumps(_bundle_with_spend("b2", "env_X", 1, "sigA")))
    assert main(["--detect-forks", str(d)]) == 0
