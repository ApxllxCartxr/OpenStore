"""Tests for openstore.attest (IMPLEMENTATION_SPEC §4)."""

import time

from cryptography.hazmat.primitives.asymmetric import ec

from openstore import attest
from openstore.evidence import BundleInput, build_bundle, public_jwk, sign_and_anchor
from openstore.verify import verify_bundle


def _ec_key():
    return ec.generate_private_key(ec.SECP256R1())


CATALOG = [
    {"sku": "GEL-VAN-500", "price_minor": 21000, "tags": ["dairy-free", "vegan"]},
    {"sku": "GEL-CHO-300", "price_minor": 12000, "tags": ["vegan"]},
]


def test_compute_catalog_digest_is_deterministic():
    d1 = attest.compute_catalog_digest(CATALOG)
    d2 = attest.compute_catalog_digest(list(reversed(CATALOG)))
    assert d1 == d2
    assert d1.startswith("sha256:")


def test_attest_and_verify_roundtrip():
    key = _ec_key()
    digest = attest.compute_catalog_digest(CATALOG)
    jws = attest.attest_item(
        sku="GEL-VAN-500",
        price_minor=21000,
        tags=["vegan", "dairy-free"],
        catalog_digest=digest,
        merchant_id="did:example:merchant",
        iat_unix=1_787_000_000,
        signing_key=key,
        kid="did:example:merchant",
    )
    payload = attest.verify_attestation(jws, key.public_key())
    assert payload["sku"] == "GEL-VAN-500"
    assert payload["price_minor"] == 21000
    assert payload["tags"] == ["dairy-free", "vegan"]  # sorted


def test_verify_rejects_tampered_signature():
    key = _ec_key()
    digest = attest.compute_catalog_digest(CATALOG)
    jws = attest.attest_item(
        sku="GEL-VAN-500", price_minor=21000, tags=[], catalog_digest=digest,
        merchant_id="m", iat_unix=1, signing_key=key, kid="m",
    )
    other = _ec_key()
    try:
        attest.verify_attestation(jws, other.public_key())
        assert False, "expected AttestationError"
    except attest.AttestationError:
        pass


def test_registry_reuses_on_identical():
    key = _ec_key()
    reg = attest.AttestationRegistry(merchant_id="m", signing_key=key, kid="m")
    digest = attest.compute_catalog_digest(CATALOG)
    a = reg.get_or_create(sku="X", price_minor=100, tags=["vegan"], catalog_digest=digest, iat_unix=10)
    b = reg.get_or_create(sku="X", price_minor=100, tags=["vegan"], catalog_digest=digest, iat_unix=999)
    assert a.jws == b.jws
    assert a.iat == 10  # reused, original iat preserved (R4.2)


def test_registry_mints_new_on_tag_change():
    key = _ec_key()
    reg = attest.AttestationRegistry(merchant_id="m", signing_key=key, kid="m")
    digest = attest.compute_catalog_digest(CATALOG)
    a = reg.get_or_create(sku="X", price_minor=100, tags=["vegan"], catalog_digest=digest, iat_unix=10)
    b = reg.get_or_create(sku="X", price_minor=100, tags=["vegan", "new"], catalog_digest=digest, iat_unix=20)
    assert a.jws != b.jws


def test_attestation_freshness_rule():
    payload = {"iat": 100}
    assert attest.attestation_fresh(payload, 100) is True
    assert attest.attestation_fresh(payload, 99) is False  # retroactive (R4.3)


def _valid_bundle(attest_key, kid, iat_unix):
    """Minimal bundle with one attested goods item; returns (bundle, jwks)."""
    digest = attest.compute_catalog_digest(CATALOG)
    jws = attest.attest_item(
        sku="GEL-VAN-500", price_minor=21000, tags=["dairy-free", "vegan"],
        catalog_digest=digest, merchant_id=kid, iat_unix=iat_unix,
        signing_key=attest_key, kid=kid,
    )
    goods = {
        "cart_hash": "sha256:00",
        "items": [{"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 21000, "tags": ["dairy-free", "vegan"],
                   "catalog_attestation": jws}],
    }
    transaction = {"cart_created_at": "2026-08-28T00:00:00Z", "amount_minor": 21000, "currency": "INR"}
    adj = {
        "verdict": "ALLOW", "reason_code": None, "transcript": [],
        "compiler_digest": __import__("openstore.compiler", fromlist=["COMPILER_DIGEST"]).COMPILER_DIGEST,
        "policy_schema_version": 2,
        "evaluated_at": "2026-08-28T00:00:01Z",
        "context": {"merchant_id": kid, "currency": "INR", "evaluated_at_unix": 1_787_000_001,
                    "spent_minor": 0, "transactions_count": 0},
    }
    aal = {"level": 2, "predicates": {}, "reasons": ["no_per_transaction_binding"]}
    bundle = build_bundle(BundleInput(
        bundle_id="B1", transaction=transaction, goods=goods,
        agent={"client_id": "", "scopes": [], "token_jti": ""},
        adjudication=adj, aal=aal,
    ))
    bundle = sign_and_anchor(bundle, attest_key, kid, "2026-08-28T00:00:02Z")
    jwks = {"keys": [public_jwk(attest_key.public_key(), kid)]}
    return bundle, jwks


def test_fresh_attestation_passes_e6():
    from datetime import datetime, timezone

    key = _ec_key()
    cart_created = int(datetime(2026, 8, 28, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    bundle, jwks = _valid_bundle(key, "did:m", iat_unix=cart_created - 1)
    res = verify_bundle(bundle, jwks)
    cat = next(c for c in res.checks if c["name"] == "catalog_attestations")
    assert cat["result"] == "pass", cat


def test_retroactive_attestation_fails_e6():
    """R4.3 — an attestation issued after the cart was created must set E6 false."""
    from datetime import datetime, timezone

    key = _ec_key()
    cart_created = int(datetime(2026, 8, 28, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    # attestation iat is AFTER the cart was created (retroactive).
    bundle, jwks = _valid_bundle(key, "did:m", iat_unix=cart_created + 500)
    res = verify_bundle(bundle, jwks)
    cat = next(c for c in res.checks if c["name"] == "catalog_attestations")
    assert cat["result"] == "fail"
    assert cat["detail"] == "attestation_stale"
