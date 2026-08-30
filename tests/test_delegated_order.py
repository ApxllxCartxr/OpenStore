"""End-to-end test: a delegated (v1.1.0) order round-trips through the runtime
and verifies with the standalone verifier (DELEGATION_AND_ORCHESTRATION §3/§5/§6)."""

from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openstore.client import OpenStoreClient, build_signed_mandate
from openstore.compiler import COMPILER_DIGEST
from openstore.core.authority.native_webauthn import simulate_browser_assertion
from openstore.delegation import (
    BudgetEnvelope,
    DelegationLink,
    Grant,
    sign_link,
)
from openstore.evidence import public_jwk
from openstore.models import Policy
from openstore.runtime import DelegationSpec, MerchantRuntime
from openstore.verify import verify_bundle


def _rt():
    key = Ed25519PrivateKey.generate()
    catalog = {"GELATO": {"unit_price_paise": 5000, "tags": ["food"]}}
    return MerchantRuntime(
        merchant_signing_key=key,
        legal_name="Gelateria",
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog=catalog,
        policy=Policy(spend_limit_paise=1_000_000_00),
    )


def _root_grant(did, mids):
    return Grant(
        budget_minor=100000, currency="INR", merchant_ids=mids,
        allowed_tags=("food",), tag_mode="all", blocked_skus=(),
        max_transactions=10, not_before=0, expires_at=9_000_000_000,
    )


def _leaf_grant(did, mids):
    return Grant(
        budget_minor=20000, currency="INR", merchant_ids=mids,
        allowed_tags=("food",), tag_mode="all", blocked_skus=(),
        max_transactions=3, not_before=0, expires_at=8_000_000_000,
    )


def _delegation_spec(rt, link_key, *, sequencer_key=None, merchant_ids=None):
    did = rt.did
    mids = merchant_ids or (did,)
    root = DelegationLink(
        link_id="dl_root", parent_link_id=None, depth=0, envelope_id="env_root",
        delegator_thumbprint="cred_human", delegate_thumbprint="tA",
        grant=_root_grant(did, mids), issued_at="2026-09-01T00:00:00Z", signature="",
    )
    leaf = DelegationLink(
        link_id="dl_A", parent_link_id="dl_root", depth=1, envelope_id="env_B",
        delegator_thumbprint="tA", delegate_thumbprint="tB",
        grant=_leaf_grant(did, mids), issued_at="2026-09-01T00:01:00Z", signature="",
    )
    leaf = DelegationLink.from_dict(
        {**leaf.to_dict(), "signature": sign_link(leaf, link_key, "tA")}
    )
    envelope = BudgetEnvelope(
        envelope_id="env_B", budget_minor=20000, currency="INR",
        issued_at="2026-09-01T00:00:00Z", expires_at=8_000_000_000,
        parent_envelope_id="env_root",
    )
    return DelegationSpec(
        chain=(root, leaf), envelope=envelope, prior_spend=(),
        sequencer_type="none" if sequencer_key is None else "linear",
        sequencer_key=sequencer_key,
    )


def _confirm(rt, preview_hash):
    begin = rt.begin_confirmation(preview_hash)
    cred_id, cred_key = rt.webauthn._dev
    wa = simulate_browser_assertion(
        rp_id=rt.rp_id, origin=rt.origin, challenge_b64=begin["challenge"],
        policy=rt._merchant_policy_dict(), cart_hash=preview_hash,
        credential_id=cred_id, signer_key=cred_key,
    )
    return {
        "preview_hash": preview_hash,
        "webauthn_assertion": {"session_id": begin["session_id"], **wa},
    }


def _quote_and_mandate(rt, client):
    import time

    quote = client.create_quote(
        [{"sku": "GELATO", "title": "Gelato", "quantity": 2, "unit_price_paise": 5000, "tax_paise": 0}]
    )
    mandate = build_signed_mandate(
        payer_priv=Ed25519PrivateKey.generate(),
        mandate_id="M1", merchant_did=rt.did, scope="global",
        max_amount_paise=100_000, currency="INR", expires_at=int(time.time()) + 3600,
    )
    return quote, mandate


def test_delegated_order_round_trips_and_verifies():
    rt = _rt()
    client = OpenStoreClient(rt)
    link_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    quote, mandate = _quote_and_mandate(rt, client)
    spec = _delegation_spec(rt, link_key)

    # §6.2 — human must preview + explicitly confirm before the order finalizes.
    preview = client.preview_delegated_order(quote, spec)
    assert preview["aal_level"] == 2  # depth 1 -> AAL capped to 2 (§6.1)
    confirmation = _confirm(rt, preview["preview_hash"])

    res = client.create_order(quote, mandate, delegation_spec=spec, confirmation=confirmation)
    assert "poai_bundle" in res
    assert res["aal_level"] == 2

    bundle = res["poai_bundle"]
    assert bundle["adjudication"]["compiler_digest"] != COMPILER_DIGEST
    assert "delegation" in bundle["authority"]
    assert bundle["human_intent"]["preview_hash"] == preview["preview_hash"]

    vr = verify_bundle(bundle, {"keys": [public_jwk(rt.attest_key.public_key(), rt.did)]})
    assert vr.ok, vr.failures
    assert vr.failures == []
    assert vr.re_derived["aal_level"] == 2
    assert "delegated_depth_capped" in vr.re_derived["aal_reasons"]
    assert vr.checks[-1]["name"] == "delegation" and vr.checks[-1]["result"] == "pass"


def test_delegated_order_rejects_without_confirmation():
    rt = _rt()
    client = OpenStoreClient(rt)
    link_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    quote, mandate = _quote_and_mandate(rt, client)
    spec = _delegation_spec(rt, link_key)
    preview = client.preview_delegated_order(quote, spec)
    # No confirmation passed -> the runtime must refuse to finalize.
    try:
        client.create_order(quote, mandate, delegation_spec=spec)
        assert False, "expected refusal without explicit confirmation"
    except ValueError as e:
        assert "confirmation" in str(e).lower()


def test_delegated_order_rejects_unapproved_preview():
    rt = _rt()
    client = OpenStoreClient(rt)
    link_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    quote, mandate = _quote_and_mandate(rt, client)
    spec = _delegation_spec(rt, link_key)
    preview = client.preview_delegated_order(quote, spec)
    # Confirm a *different* preview hash than the one actually charged.
    wrong = _confirm(rt, "sha256:" + "0" * 64)
    try:
        client.create_order(quote, mandate, delegation_spec=spec, confirmation=wrong)
        assert False, "expected refusal on preview_hash mismatch"
    except ValueError as e:
        assert "preview_hash" in str(e).lower()


def test_multi_merchant_without_sequencer_rejected():
    rt = _rt()
    client = OpenStoreClient(rt)
    link_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    quote, mandate = _quote_and_mandate(rt, client)
    # Two merchants named but no sequencer -> §6.2b hard rule must reject.
    spec = _delegation_spec(rt, link_key, merchant_ids=(rt.did, "did:key:zOtherMerchant"))
    try:
        client.preview_delegated_order(quote, spec)
        assert False, "expected rejection of multi-merchant without sequencer"
    except ValueError as e:
        assert "sequencer" in str(e).lower()


def test_sequenced_order_carries_verifiable_sequencer_signature():
    rt = _rt()
    client = OpenStoreClient(rt)
    link_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    seq_key = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(SECP256R1())
    quote, mandate = _quote_and_mandate(rt, client)
    spec = _delegation_spec(rt, link_key, sequencer_key=seq_key)

    preview = client.preview_delegated_order(quote, spec)
    confirmation = _confirm(rt, preview["preview_hash"])
    res = client.create_order(quote, mandate, delegation_spec=spec, confirmation=confirmation)

    bundle = res["poai_bundle"]
    seq = bundle["authority"]["delegation"]["sequencer"]
    assert seq["type"] == "linear"
    assert "signature" in seq and "jwk" in seq

    vr = verify_bundle(bundle, {"keys": [public_jwk(rt.attest_key.public_key(), rt.did)]})
    assert vr.ok, vr.failures
    assert vr.failures == []
