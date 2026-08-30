"""Tests for DELEGATION_AND_ORCHESTRATION §3.1 — delegation chain (Layer 1)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from openstore import delegation
from openstore.delegation import (
    DELEGATION_DIGEST,
    BudgetEnvelope,
    DelegationLink,
    Grant,
    SpendEntry,
    verify_delegation_chain,
    verify_spend_chain,
)

PINNED_DIGEST = "sha256:f4d24d08ca1f31813479584ffa5c514d0267ec00ada416b33f497ee9d4a04016"
PINNED_SPENDCHAIN_DIGEST = (
    "sha256:80fc5c08cab7ae4c9a82d6dec4eece8c6965c14554ab88097fe86ef3db7623ff"
)


def _signed_entry(env_id, seq, etype, amount, ref, prev, key, kid, issued_at="2026-09-01T00:00:00Z"):
    e = SpendEntry(env_id, seq, etype, amount, ref, prev, issued_at, "")
    sig = delegation.sign_spend_entry(e, key, kid)
    return SpendEntry(**{**e.to_dict(), "signature": sig})


def _link_of(prev, entry):
    return "sha256:" + delegation._link_raw(prev, entry).hex()


def _root_grant() -> Grant:
    return Grant(
        budget_minor=100000,
        currency="inr",
        merchant_ids=("M1", "M2", "M3"),
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        max_transactions=10,
        not_before=1_000,
        expires_at=9_000,
    )


def _leaf_grant() -> Grant:
    return Grant(
        budget_minor=20000,
        currency="inr",
        merchant_ids=("M2",),
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=("SKU-BAD",),
        max_transactions=3,
        not_before=1_500,
        expires_at=8_000,
    )


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _resign(link: DelegationLink, key, kid: str) -> DelegationLink:
    d = link.to_dict()
    d["signature"] = delegation.sign_link(link, key, kid)
    return DelegationLink.from_dict(d)


def _make_chain(root_key, leaf_key, *, leaf_grant=None, root_grant=None, depth2=False):
    rg = root_grant or _root_grant()
    lg = leaf_grant or _leaf_grant()
    root = DelegationLink(
        link_id="dl_root",
        parent_link_id=None,
        depth=0,
        envelope_id="env_root",
        delegator_thumbprint="cred_human",
        delegate_thumbprint="thumb_A",
        grant=rg,
        issued_at="2026-09-01T00:00:00Z",
        signature="",  # depth-0 human WebAuthn sig; verified via flag
    )
    leaf = DelegationLink(
        link_id="dl_A",
        parent_link_id="dl_root",
        depth=1,
        envelope_id="env_A",
        delegator_thumbprint="thumb_A",
        delegate_thumbprint="thumb_B",
        grant=lg,
        issued_at="2026-09-01T00:01:00Z",
        signature="",
    )
    leaf = _resign(leaf, leaf_key, "kA")
    links = [root, leaf]
    if depth2:
        leaf2 = DelegationLink(
            link_id="dl_B",
            parent_link_id="dl_A",
            depth=2,
            envelope_id="env_B",
            delegator_thumbprint="thumb_B",
            delegate_thumbprint="thumb_C",
            grant=lg,
            issued_at="2026-09-01T00:02:00Z",
            signature="",
        )
        leaf2 = _resign(leaf2, _key(), "kB")
        links.append(leaf2)
    return links


def test_delegation_digest_is_pinned():
    assert DELEGATION_DIGEST == PINNED_DIGEST


def test_max_depth_constant_is_three():
    assert delegation.MAX_DEPTH == 3


def test_valid_chain_verifies_and_returns_effective_policy():
    root_key, leaf_key = _key(), _key()
    links = _make_chain(root_key, leaf_key)
    keys = {"thumb_A": leaf_key.public_key()}
    eff = verify_delegation_chain(tuple(links), keys=keys, root_is_human_signed=True)
    # effective policy is the meet of root and leaf
    assert eff.budget_minor == 20000
    assert eff.merchant_ids == ("M2",)
    assert eff.allowed_tags == ("vegan",)
    assert eff.tag_mode == "all"
    assert eff.not_before == 1500
    assert eff.expires_at == 8000
    assert eff.max_transactions == 3
    assert "SKU-BAD" in eff.blocked_skus


def test_chain_depth2_verifies():
    root_key, leaf_key = _key(), _key()
    links = _make_chain(root_key, leaf_key, depth2=True)
    # re-sign the depth-2 link with a key we hold so verification succeeds.
    k2 = _key()
    l2 = links[2]
    links[2] = _resign(l2, k2, "kB2")
    eff = verify_delegation_chain(
        tuple(links),
        keys={"thumb_A": leaf_key.public_key(), "thumb_B": k2.public_key()},
    )
    assert eff.budget_minor == 20000


def test_root_not_human_signed_rejected():
    root_key, leaf_key = _key(), _key()
    links = _make_chain(root_key, leaf_key)
    keys = {"thumb_A": leaf_key.public_key()}
    with pytest.raises(ValueError, match="root_not_human_signed"):
        verify_delegation_chain(tuple(links), keys=keys, root_is_human_signed=False)


def test_depth_exceeded_rejected():
    # Build a contiguous chain root(0)->A(1)->B(2)->C(3)->D(4); depth 4 must be rejected.
    root = DelegationLink(
        link_id="dl_root",
        parent_link_id=None,
        depth=0,
        envelope_id="env_root",
        delegator_thumbprint="cred_human",
        delegate_thumbprint="t1",
        grant=_root_grant(),
        issued_at="2026-09-01T00:00:00Z",
        signature="",
    )
    prev = root
    thumb = "t1"
    links = [root]
    keys_holder = {}
    for d in range(1, 5):
        nxt = f"t{d + 1}"
        link = DelegationLink(
            link_id=f"dl_{d}",
            parent_link_id=prev.link_id,
            depth=d,
            envelope_id=f"env_{d}",
            delegator_thumbprint=thumb,
            delegate_thumbprint=nxt,
            grant=_leaf_grant(),
            issued_at="2026-09-01T00:00:00Z",
            signature="",
        )
        key = _key()
        link = _resign(link, key, f"k{d}")
        links.append(link)
        keys_holder[d] = key.public_key()
        prev = link
        thumb = nxt

    # Keep every key so signatures pass; the depth_limit check (after the loop)
    # must reject the depth-4 link.
    keys = {f"t{d}": keys_holder[d] for d in range(1, 5)}
    with pytest.raises(ValueError, match="depth_exceeded"):
        verify_delegation_chain(tuple(links), keys=keys, root_is_human_signed=True)


def test_budget_not_attenuating_rejected():
    root_key, leaf_key = _key(), _key()
    bad = Grant(
        budget_minor=200000,  # exceeds root 100000
        currency="inr",
        merchant_ids=("M2",),
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        max_transactions=3,
        not_before=1_500,
        expires_at=8_000,
    )
    links = _make_chain(root_key, leaf_key, leaf_grant=bad)
    with pytest.raises(ValueError, match="budget_not_attenuating"):
        verify_delegation_chain(tuple(links), keys={"thumb_A": leaf_key.public_key()})


def test_merchant_not_attenuating_rejected():
    root_key, leaf_key = _key(), _key()
    bad = Grant(
        budget_minor=20000,
        currency="inr",
        merchant_ids=("M9",),  # not in root {M1,M2,M3}
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        max_transactions=3,
        not_before=1_500,
        expires_at=8_000,
    )
    links = _make_chain(root_key, leaf_key, leaf_grant=bad)
    with pytest.raises(ValueError, match="merchant_not_attenuating"):
        verify_delegation_chain(tuple(links), keys={"thumb_A": leaf_key.public_key()})


def test_tag_not_attenuating_rejected():
    root_key, leaf_key = _key(), _key()
    bad = Grant(
        budget_minor=20000,
        currency="inr",
        merchant_ids=("M2",),
        allowed_tags=("non-vegan",),  # not subset of root's {vegan}
        tag_mode="all",
        blocked_skus=(),
        max_transactions=3,
        not_before=1_500,
        expires_at=8_000,
    )
    links = _make_chain(root_key, leaf_key, leaf_grant=bad)
    with pytest.raises(ValueError, match="tag_not_attenuating"):
        verify_delegation_chain(tuple(links), keys={"thumb_A": leaf_key.public_key()})


def test_expiry_not_attenuating_rejected():
    root_key, leaf_key = _key(), _key()
    bad = Grant(
        budget_minor=20000,
        currency="inr",
        merchant_ids=("M2",),
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        max_transactions=3,
        not_before=1_500,
        expires_at=20_000,  # exceeds root 9_000
    )
    links = _make_chain(root_key, leaf_key, leaf_grant=bad)
    with pytest.raises(ValueError, match="expiry_not_attenuating"):
        verify_delegation_chain(tuple(links), keys={"thumb_A": leaf_key.public_key()})


def test_tx_count_not_attenuating_rejected():
    root_key, leaf_key = _key(), _key()
    bad = Grant(
        budget_minor=20000,
        currency="inr",
        merchant_ids=("M2",),
        allowed_tags=("vegan",),
        tag_mode="all",
        blocked_skus=(),
        max_transactions=99,  # exceeds root 10
        not_before=1_500,
        expires_at=8_000,
    )
    links = _make_chain(root_key, leaf_key, leaf_grant=bad)
    with pytest.raises(ValueError, match="tx_count_not_attenuating"):
        verify_delegation_chain(tuple(links), keys={"thumb_A": leaf_key.public_key()})


def test_link_signature_invalid_rejected():
    root_key, leaf_key = _key(), _key()
    links = _make_chain(root_key, leaf_key)
    # tamper with the leaf payload after signing
    l = links[1]
    tampered = DelegationLink(
        **{**l.to_dict(), "grant": Grant(**{**l.grant.to_dict(), "budget_minor": 1})}
    )
    with pytest.raises(ValueError, match="link_signature_invalid"):
        verify_delegation_chain((links[0], tampered), keys={"thumb_A": leaf_key.public_key()})


def test_link_order_invalid_rejected():
    root_key, leaf_key = _key(), _key()
    links = _make_chain(root_key, leaf_key)
    bad = DelegationLink(
        link_id="dl_X",
        parent_link_id="dl_root",
        depth=5,  # wrong position
        envelope_id="env_X",
        delegator_thumbprint="thumb_A",
        delegate_thumbprint="thumb_B",
        grant=_leaf_grant(),
        issued_at="2026-09-01T00:01:00Z",
        signature="",
    )
    with pytest.raises(ValueError, match="link_order_invalid"):
        verify_delegation_chain((links[0], bad), keys={"thumb_A": leaf_key.public_key()})


# ---------- §3.3 spend chain + §3.2 envelope ----------


def test_spendchain_digest_is_pinned():
    assert delegation.SPENDCHAIN_DIGEST == PINNED_SPENDCHAIN_DIGEST


def test_spend_chain_verifies_and_accounts():
    mkey, hkey = _key(), _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 5000, "b1", gen, mkey, "merch")
    l0 = _link_of(gen, e0)
    e1 = _signed_entry("env_A", 1, "DELEGATE", 20000, "env_B", l0, hkey, "holder")
    l1 = _link_of(l0, e1)
    e2 = _signed_entry("env_A", 2, "SPEND", 3000, "b2", l1, mkey, "merch")
    keys = {"merch": mkey.public_key(), "holder": hkey.public_key()}
    st = verify_spend_chain((e0, e1, e2), env, keys=keys)
    assert st.spent_minor == 8000
    assert st.delegated_minor == 20000
    assert st.available_minor == 72000
    assert st.entry_count == 3


def test_release_returns_budget():
    hkey = _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 5000, "b1", gen, hkey, "holder")
    l0 = _link_of(gen, e0)
    e1 = _signed_entry("env_A", 1, "RELEASE", 95000, "", l0, hkey, "holder")
    st = verify_spend_chain((e0, e1), env, keys={"holder": hkey.public_key()})
    assert st.released_minor == 95000
    assert st.available_minor == 0


def test_genesis_mismatch_rejected():
    mkey = _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 1000, "b", gen, mkey, "merch")
    bad_env = BudgetEnvelope("env_OTHER", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    with pytest.raises(ValueError, match="genesis_mismatch"):
        verify_spend_chain((e0,), bad_env, keys={"merch": mkey.public_key()})


def test_sequence_gap_rejected():
    mkey = _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 1000, "b", gen, mkey, "merch")
    l0 = _link_of(gen, e0)
    # gap: next entry claims sequence 2, skipping 1
    e2 = _signed_entry("env_A", 2, "SPEND", 1000, "b", l0, mkey, "merch")
    with pytest.raises(ValueError, match="sequence_gap"):
        verify_spend_chain((e0, e2), env, keys={"merch": mkey.public_key()})


def test_prev_link_mismatch_rejected():
    mkey = _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 1000, "b", gen, mkey, "merch")
    l0 = _link_of(gen, e0)
    e1 = _signed_entry("env_A", 1, "SPEND", 1000, "b", "sha256:" + "0" * 64, mkey, "merch")
    with pytest.raises(ValueError, match="prev_link_mismatch"):
        verify_spend_chain((e0, e1), env, keys={"merch": mkey.public_key()})


def test_fork_detected_rejected():
    mkey = _key()
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 1000, "b", gen, mkey, "merch")
    e0_dup = _signed_entry("env_A", 0, "SPEND", 2000, "b", gen, mkey, "merch")
    with pytest.raises(ValueError, match="fork_detected"):
        verify_spend_chain((e0, e0_dup), env, keys={"merch": mkey.public_key()})


def test_envelope_overspent_rejected():
    mkey = _key()
    env = BudgetEnvelope("env_A", 5000, "inr", "2026-09-01T00:00:00Z", 9_000)
    gen = delegation._genesis_link("env_A")
    e0 = _signed_entry("env_A", 0, "SPEND", 4000, "b", gen, mkey, "merch")
    l0 = _link_of(gen, e0)
    e1 = _signed_entry("env_A", 1, "SPEND", 2000, "b", l0, mkey, "merch")
    with pytest.raises(ValueError, match="envelope_overspent"):
        verify_spend_chain((e0, e1), env, keys={"merch": mkey.public_key()})


def test_envelope_available_helper():
    env = BudgetEnvelope("env_A", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    st = delegation.ChainState("env_A", 8000, 20000, 0, 72000, 3)
    assert delegation.envelope_available(env, st) == 72000


def test_sibling_disjointness_enforced():
    parent = BudgetEnvelope("env_P", 100000, "inr", "2026-09-01T00:00:00Z", 9_000)
    pstate = delegation.ChainState("env_P", 0, 0, 0, 100000, 0)
    children = (
        BudgetEnvelope("env_A", 60000, "inr", "t", 9_000),
        BudgetEnvelope("env_B", 60000, "inr", "t", 9_000),
    )
    with pytest.raises(ValueError, match="envelope_overlaps_sibling"):
        delegation.check_sibling_disjointness(parent, pstate, children)
    # within budget: ok
    ok = (BudgetEnvelope("env_A", 60000, "inr", "t", 9_000),)
    delegation.check_sibling_disjointness(parent, pstate, ok)
