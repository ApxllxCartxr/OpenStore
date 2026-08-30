import json

from cryptography.hazmat.primitives.asymmetric import ec

from openstore import evidence
from openstore.canonical import canonical_json_bytes


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _sections():
    return {
        "transaction": {"merchant_id": "m1", "checkout_id": "c1", "amount_minor": 100,
                        "currency": "INR", "psp": {"provider": "razorpay",
                        "order_id": "o1", "payment_link_id": "p1"},
                        "cart_created_at": "2026-08-27T09:14:22Z"},
        "human_intent": None,
        "authority": None,
        "goods": {"cart_hash": "sha256:aa", "cart_version": 1,
                  "items": [{"sku": "A", "qty": 1, "unit_minor": 100, "tags": ["x"],
                             "catalog_attestation": "jws"}]},
        "agent": {"client_id": "client", "display_name": "Buyer", "token_jti": "jti",
                  "scopes": ["checkout:confirm"], "consent_granted_at": "2026-08-27T09:14:00Z"},
        "adjudication": {"compiler_version": "1.0.0",
                         "compiler_digest": "sha256:9654...", "policy_schema_version": 2,
                         "evaluated_at": "2026-08-27T09:14:22Z",
                         "context": {"spent_minor": 0, "transactions_count": 0,
                                     "currency": "INR", "merchant_id": "m1",
                                     "evaluated_at_unix": 1787000000},
                         "verdict": "ALLOW", "reason_code": None, "transcript": []},
        "notification": None,
        "aal": {"level": 2, "predicates": {"e1_agent_authenticated": True},
                "reasons": ["no_per_transaction_binding"]},
    }


def test_chain_link0_is_sha256_of_transaction():
    s = _sections()
    chain = evidence.compute_chain(s)
    expected = "sha256:" + __import__("hashlib").sha256(
        canonical_json_bytes(s["transaction"])).hexdigest()
    assert chain["links"][0] == expected
    assert chain["root"] == chain["links"][-1]
    assert len(chain["links"]) == 8


def test_chain_deterministic():
    s = _sections()
    a = evidence.compute_chain(s)
    b = evidence.compute_chain(s)
    assert a == b


def test_build_bundle_has_all_eight_sections():
    s = _sections()
    bundle = evidence.build_bundle(evidence.BundleInput(
        bundle_id="poai_ABCD", transaction=s["transaction"], goods=s["goods"],
        agent=s["agent"], adjudication=s["adjudication"], aal=s["aal"]))
    assert bundle["poai_version"] == "0.1"
    for name in evidence.SECTION_ORDER:
        assert name in bundle
    assert bundle["chain"]["root"] == bundle["chain"]["links"][-1]


def test_verify_chain_passes_on_built_bundle():
    s = _sections()
    bundle = evidence.build_bundle(evidence.BundleInput(
        bundle_id="poai_ABCD", transaction=s["transaction"], goods=s["goods"],
        agent=s["agent"], adjudication=s["adjudication"], aal=s["aal"]))
    evidence.verify_chain(bundle)  # must not raise


def test_merchant_signature_roundtrip():
    s = _sections()
    bundle = evidence.build_bundle(evidence.BundleInput(
        bundle_id="poai_ABCD", transaction=s["transaction"], goods=s["goods"],
        agent=s["agent"], adjudication=s["adjudication"], aal=s["aal"]))
    key = _key()
    kid = "poai-es256-test"
    evidence.sign_and_anchor(bundle, key, kid, "2026-08-27T09:15:04Z")
    assert "merchant_signature" in bundle["chain"]
    pub = key.public_key()
    payload = evidence.verify_merchant_signature(
        bundle["chain"]["merchant_signature"], pub, expected_root=bundle["chain"]["root"])
    assert payload["bundle_id"] == "poai_ABCD"
    assert payload["root"] == bundle["chain"]["root"]


def test_merchant_signature_rejects_tampered_root():
    s = _sections()
    bundle = evidence.build_bundle(evidence.BundleInput(
        bundle_id="poai_ABCD", transaction=s["transaction"], goods=s["goods"],
        agent=s["agent"], adjudication=s["adjudication"], aal=s["aal"]))
    key = _key()
    evidence.sign_and_anchor(bundle, key, "k", "2026-08-27T09:15:04Z")
    import pytest
    with pytest.raises(ValueError):
        evidence.verify_merchant_signature(
            bundle["chain"]["merchant_signature"], key.public_key(),
            expected_root="sha256:deadbeef")


def test_jwks_shape():
    key = _key()
    jwk = evidence.public_jwk(key.public_key(), "kid1")
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256"
    assert jwk["alg"] == "ES256" and jwk["use"] == "sig"
    assert set(jwk) >= {"kty", "crv", "x", "y", "kid", "alg", "use"}
