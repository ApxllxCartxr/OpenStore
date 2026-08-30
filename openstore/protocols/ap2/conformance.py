"""AP2 adapter conformance (INTEROP_SPEC §7)."""

from __future__ import annotations


def conformance_pass() -> bool:
    from openstore.protocols.ap2.adapter import (
        AP2UnmappableConstraint, AP2_EXTERNAL_FIELDS, ingest_mandate,
    )
    from openstore.protocols.ap2.emit import emit_authority

    # A clean cart mandate ingests to ap2_cart_mandate.
    m = {"type": "cart_mandate", "vct": "mandate.checkout.closed.1",
         "constraints": {"cart_hash_bound": "sha256:abc", "reference": "r1"},
         "signature": {"human_held_key_signature": True}}
    pres = ingest_mandate(m)
    assert pres.scheme == "ap2_cart_mandate"

    # An unmappable constraint fails closed (R6.3a).
    bad = dict(m); bad["constraints"] = {"recurrence": "weekly"}
    raised = False
    try:
        ingest_mandate(bad)
    except AP2UnmappableConstraint:
        raised = True
    assert raised

    # Emission drops fields and reports them (R6.3b).
    bundle = {"poai_version": "0.1",
               "authority": {"scheme": "ap2_cart_mandate", "webauthn": {"uv": True},
                             "presentation": {"a": 1}, "policy": None, "policy_hash": None},
               "aal": {"predicates": {"e1": True}}}
    _obj, dropped = emit_authority(bundle)
    assert "poai_version" in dropped and "authority.webauthn" in dropped
    assert set(AP2_EXTERNAL_FIELDS)
    return True
