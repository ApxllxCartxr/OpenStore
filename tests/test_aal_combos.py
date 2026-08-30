"""§10 AAL predicate-combination test (IMPLEMENTATION_SPEC §5 R5.3b)."""

from itertools import product

from openstore.aal import Predicates, resolve_aal

FIELDS = list(Predicates.__slots__)


def test_all_512_predicate_combinations():
    # 2^9 predicate combinations, two schema versions = 1024 verdicts.
    for version in (1, 2):
        for combo in product((False, True), repeat=9):
            preds = Predicates(**dict(zip(FIELDS, combo)))
            level, reasons = resolve_aal(preds, version)
            assert level in (0, 1, 2, 3)
            assert isinstance(reasons, tuple)
            # determinism
            assert resolve_aal(preds, version) == (level, reasons)


def test_aal_cornerstones():
    all_true = Predicates(**{f: True for f in FIELDS})
    assert resolve_aal(all_true, 2)[0] == 3
    all_false = Predicates(**{f: False for f in FIELDS})
    assert resolve_aal(all_false, 2)[0] == 0
    # cart-bound missing but everything else present -> AAL 2
    no_bind = Predicates(e1_agent_authenticated=True, e2_policy_signature_valid=True,
                         e3_assertion_fresh=True, e4_user_verified=True, e5_cart_bound=False,
                         e6_catalog_attested=True, e7_compiler_allow=True,
                         e8_intent_recorded=True, e9_notified=True)
    assert resolve_aal(no_bind, 2)[0] == 2
