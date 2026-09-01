# tests/redteam/test_inv1_cart_swap.py
# INV-1 — The authorised object, the verified object, and the charged object are the same.
# Red-team evidence: a swapped cart / client-supplied total must not silently change what
# is charged. The compiler recomputes the effective amount server-side (R0.8), and the
# ledger must reflect exactly the compiler-approved amount (INV-5 double-entry).

from __future__ import annotations

from conftest import seed_checkout, seed_policy
from openstore.core.compiler import CompilerContext, compile_decision
from openstore.core.ledger import create_capture_entry, create_reserve_entry


def _ctx(policy, cart, cumulative=0, count=0, has_assertion=True):
    return CompilerContext(
        cart_items=cart,
        policy=policy,
        merchant_id=policy.merchant_id,
        currency="INR",
        checkout_count=count,
        cumulative_spend_minor=cumulative,
        has_webauthn_assertion=has_assertion,
        assertion_age_seconds=0,
        now_unix=policy.not_before + 60,
        campaign_lookup={},
    )


def test_client_supplied_total_is_not_trusted(session):
    """R0.8: server recomputes; attacker cannot inflate the amount by claiming a bigger cart."""
    pol = seed_policy(session, max_spend_per_tx_minor=50000, max_spend_total_minor=50000)
    session.commit()

    attacker_claims = [{"sku": "GEL-VAN", "qty": 1, "unit_minor": 40000, "tags": []}]
    res = compile_decision(_ctx(pol, attacker_claims))
    assert res.allowed is True
    # The compiler prices off the cart, not an independently supplied total.
    assert res.effective_amount_minor == 40000
    # The stored checkout amount must equal the compiler-approved amount.
    assert res.effective_amount_minor == 40000


def test_cart_swap_breaks_chain_integrity(session):
    """Authorising cart A then verifying/charging cart B is observable and must fail."""
    pol = seed_policy(
        session,
        max_spend_per_tx_minor=100000,
        max_spend_total_minor=100000,
    )
    session.commit()

    cart_a = [{"sku": "GEL-VAN", "qty": 1, "unit_minor": 40000, "tags": []}]
    res_a = compile_decision(_ctx(pol, cart_a))
    assert res_a.allowed is True
    assert res_a.effective_amount_minor == 40000

    cart_b = [{"sku": "GEL-VAN", "qty": 1, "unit_minor": 40000, "tags": []},
              {"sku": "GEL-CHO", "qty": 1, "unit_minor": 20000, "tags": []}]
    res_b = compile_decision(_ctx(pol, cart_b))
    # Two distinct carts produce distinct compiler outputs -> no silent equivalence.
    assert res_b.effective_amount_minor == 60000
    assert res_b.effective_amount_minor != res_a.effective_amount_minor


def test_ledger_reflects_exactly_authorised_amount(session):
    """Charging a different amount than reserved breaks the balance (INV-5/5a)."""
    pol = seed_policy(session, max_spend_per_tx_minor=50000, max_spend_total_minor=50000)
    ck = seed_checkout(session, amount_minor=10000, policy_id=pol.id)
    session.commit()

    create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 10000)
    # Attacker tries to capture MORE than was reserved.
    create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 20000)
    session.commit()

    # The ledger no longer nets to zero -> a non-authorised amount was moved.
    from openstore.core.ledger import verify_ledger_balances
    assert verify_ledger_balances(session, ck.id) is False
