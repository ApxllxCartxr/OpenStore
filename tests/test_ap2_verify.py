"""Acceptance tests for SECURITY FIX N2 / P1 (AP2 mandate verifier).

The original AP2 verifier trusted caller-supplied booleans for the mandate's
human-auth predicates: ``e2_policy_signature_valid`` defaulted to True, so an
empty/missing signature still minted AAL3, and ``e4_user_verified`` was a bare
caller boolean. These tests pin the corrected fail-closed behaviour.

Note: AP2 cannot mint AAL3 from a real signature because ``e3`` (assertion
freshness) and ``e6`` (catalog attested) are out of N2 scope (decision Q3 scoped
``e3`` to C1), so the realistic happy-path level is AAL1. The security property
that matters is fail-closed: an empty signature yields AAL 0, never AAL3.
"""

from __future__ import annotations

from openstore.aal import Predicates, resolve_aal
from openstore.core.authority import ap2
from openstore.core.types import AuthorityPresentation
from openstore.protocols.ap2.adapter import AP2UnmappableConstraint, ingest_mandate


def test_cart_mandate_real_signature_derives_predicates():
    mandate = {
        "type": "cart_mandate",
        "vct": "mandate.checkout.closed.1",
        "constraints": {"cart_hash_bound": "sha256:abc"},
        "signature": {"human_held_key_signature": True},
    }
    pres = ingest_mandate(mandate)
    va = ap2.verify_cart_mandate(pres)

    assert va.predicates["e2_policy_signature_valid"] is True
    assert va.predicates["e4_user_verified"] is True
    assert va.predicates["e5_cart_bound"] is True


def test_cart_mandate_empty_signature_fails_closed_to_aal0():
    mandate = {
        "type": "cart_mandate",
        "vct": "mandate.checkout.closed.1",
        "constraints": {"cart_hash_bound": "sha256:abc"},
        "signature": {},
    }
    pres = ingest_mandate(mandate)
    va = ap2.verify_cart_mandate(pres)

    assert va.predicates["e2_policy_signature_valid"] is False
    # Fail-closed: no verifiable signature artifact => AAL 0, never AAL3.
    assert resolve_aal(Predicates(**va.predicates), 2)[0] == 0


def test_cart_mandate_missing_human_key_yields_e4_false():
    mandate = {
        "type": "cart_mandate",
        "vct": "mandate.checkout.closed.1",
        "constraints": {"cart_hash_bound": "sha256:abc"},
        # Signature artifact present but carries no human-held-key signature.
        "signature": {"some_other_claim": 1},
    }
    pres = ingest_mandate(mandate)
    va = ap2.verify_cart_mandate(pres)

    assert va.predicates["e2_policy_signature_valid"] is True  # artifact present
    assert va.predicates["e4_user_verified"] is False


def test_intent_mandate_never_aal3():
    mandate = {
        "type": "intent_mandate",
        "vct": "mandate.open.1",
        "constraints": {},
        "signature": {"human_held_key_signature": True},
    }
    pres = ingest_mandate(mandate)
    va = ap2.verify_intent_mandate(pres)

    assert va.predicates["e2_policy_signature_valid"] is True
    # Policy-level mandate: no per-transaction human act / cart binding => capped,
    # never AAL3.
    assert resolve_aal(Predicates(**va.predicates), 2)[0] < 3


def test_unmappable_constraint_still_fails_closed():
    mandate = {
        "type": "cart_mandate",
        "vct": "mandate.checkout.closed.1",
        "constraints": {"recurrence": "weekly"},
        "signature": {},
    }
    try:
        ingest_mandate(mandate)
        raise AssertionError("expected AP2UnmappableConstraint")
    except AP2UnmappableConstraint as exc:
        assert "ap2.unmappable_constraint" in str(exc)
