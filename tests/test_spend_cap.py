# tests/test_spend_cap.py
# §3.2c (Q-003): cumulative spend is scoped PER-POLICY via
# LedgerEntry.reference_id -> Checkout.policy_id. A CAPTURE under one policy must
# never erode another policy's signed budget.

from __future__ import annotations

from datetime import datetime

from openstore.core.database import check_spend_cap, compute_policy_spend
from openstore.core.ledger import create_capture_entry, create_reserve_entry
from openstore.models import Checkout, OrderState
from sqlmodel import Session


def _seed_checkout(session: Session, checkout_id: str, policy_id: str) -> None:
    session.add(Checkout(
        id=checkout_id,
        trace_id="t",
        client_id="c",
        merchant_id="m",
        cart_hash="h",
        cart_version=1,
        amount_minor=10000,
        currency="INR",
        state=OrderState.CREATED,
        policy_id=policy_id,
        policy_hash="ph",
        aal_level=0,
        expires_at=datetime.utcnow(),
        idempotency_key=f"ik_{checkout_id}",
        cart_snapshot={"items": []},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    session.commit()


def test_compute_policy_spend_is_scoped_per_policy(session: Session) -> None:
    # Two checkouts under two different policies.
    _seed_checkout(session, "chk_A", "pol_A")
    _seed_checkout(session, "chk_B", "pol_B")

    # Only pol_A gets an economic CAPTURE (via an escrow RESERVE -> CAPTURE).
    create_reserve_entry(session, "t", "c", "chk_A", 10000, "INR", "r")
    create_capture_entry(session, "t", "c", "chk_A", 10000, "INR", "c")
    session.commit()

    assert compute_policy_spend(session, "pol_A") == 10000
    assert compute_policy_spend(session, "pol_B") == 0

    # check_spend_cap for pol_A sees the 10000 already spent ...
    allowed, _ = check_spend_cap(
        session, merchant_id="m", policy_id="pol_A", amount_minor=20000,
        max_spend_per_tx_minor=100000, max_spend_total_minor=25000,
    )
    assert not allowed  # 10000 + 20000 > 25000

    # ... while pol_B's budget is untouched: 20000 <= 25000 passes.
    allowed, _ = check_spend_cap(
        session, merchant_id="m", policy_id="pol_B", amount_minor=20000,
        max_spend_per_tx_minor=100000, max_spend_total_minor=25000,
    )
    assert allowed
