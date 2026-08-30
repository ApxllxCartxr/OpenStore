"""§10 evidence-bundle tests (IMPLEMENTATION_SPEC §1.3 / §4)."""

from openstore.evidence import (
    SECTION_ORDER,
    build_bundle,
    canonical_json_bytes,
    compute_chain,
    verify_chain,
)
from openstore.verify import verify_bundle

from tests.golden_mint import base_bundle, merchant_jwks, rechain


def _fresh_bundle():
    b = base_bundle()
    return b, merchant_jwks()


def test_bundle_has_all_8_sections_and_chain():
    b, _ = _fresh_bundle()
    for name in SECTION_ORDER:
        assert name in b, f"missing section {name}"
    assert len(b["chain"]["links"]) == 8
    assert b["chain"]["root"] == b["chain"]["links"][-1]
    # recomputed chain matches
    verify_chain(b)


def test_chain_edit_detection():
    b, _ = _fresh_bundle()
    b["goods"]["items"][0]["sku"] = "TAMPERED"  # edit without rechaining
    try:
        verify_chain(b)
    except ValueError as e:
        assert "chain_broken" in str(e)
    else:
        raise AssertionError("tampered section must break the chain")


def test_retroactive_attestation_fails_e6():
    import json

    from tests.golden_mint import _sign_jws, merchant_key

    b, jwks = _fresh_bundle()
    mkey = merchant_key()
    item = b["goods"]["items"][0]
    # forge an attestation dated in the FUTURE relative to cart creation -> stale
    item["catalog_attestation"] = _sign_jws(
        {"sku": item["sku"], "price_minor": item["unit_minor"],
         "tags": item.get("tags", []), "catalog_digest": "sha256:aa",
         "merchant_id": b["transaction"]["merchant_id"], "iat": 2_000_000_000},
        mkey, "poai-es256-golden",
    )
    rechain(b)
    res = verify_bundle(b, jwks)
    assert not res.ok
    assert "catalog_attestations" in res.failures
    assert "attestation_stale" in [c["detail"] for c in res.checks if c["name"] == "catalog_attestations"]
