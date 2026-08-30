import itertools

import pytest

from openstore.aal import AAL_REASONS, Predicates, resolve_aal


def _p(**over):
    base = dict(
        e1_agent_authenticated=True,
        e2_policy_signature_valid=True,
        e3_assertion_fresh=True,
        e4_user_verified=True,
        e5_cart_bound=False,
        e6_catalog_attested=True,
        e7_compiler_allow=True,
        e8_intent_recorded=True,
        e9_notified=True,
    )
    base.update(over)
    return Predicates(**base)


def test_level2_default():
    level, reasons = resolve_aal(_p(), 2)
    assert level == 2
    assert reasons == ("no_per_transaction_binding",)


def test_level3_cart_bound():
    level, reasons = resolve_aal(_p(e5_cart_bound=True), 2)
    assert level == 3
    assert reasons == ()


def test_level0_policy_signature_invalid():
    level, reasons = resolve_aal(_p(e2_policy_signature_valid=False), 2)
    assert level == 0
    assert reasons == ("policy_signature_invalid",)


def test_level0_compiler_denied():
    level, reasons = resolve_aal(_p(e7_compiler_allow=False), 2)
    assert level == 0
    assert reasons == ("compiler_denied_or_transcript_mismatch",)


def test_level0_agent_unauthenticated():
    level, reasons = resolve_aal(_p(e1_agent_authenticated=False), 2)
    assert level == 0
    assert reasons == ("agent_unauthenticated",)


def test_level1_policy_schema_legacy():
    level, reasons = resolve_aal(_p(), 1)
    assert level == 1
    assert reasons == ("policy_schema_legacy",)


def test_level1_assertion_stale():
    level, reasons = resolve_aal(_p(e3_assertion_fresh=False), 2)
    assert level == 1
    assert reasons == ("assertion_stale",)


def test_level1_intent_and_notification():
    level, reasons = resolve_aal(_p(e8_intent_recorded=False, e9_notified=False), 2)
    assert level == 1
    assert reasons == ("intent_unrecorded", "notification_missing")


def test_all_512_predicate_combinations():
    """Enumerate every 2^9 predicate tuple x both policy versions.

    Confirms resolve_aal always returns a level in 0..3 and reasons drawn
    solely from the closed set — the invariant the verifier depends on.
    """
    fields = [f for f in Predicates.__dataclass_fields__]
    for combo in itertools.product([False, True], repeat=9):
        preds = Predicates(**dict(zip(fields, combo)))
        for pv in (1, 2):
            level, reasons = resolve_aal(preds, pv)
            assert level in (0, 1, 2, 3)
            assert all(r in AAL_REASONS for r in reasons)
