# tests/test_spend_cap.py
# §3.2c (Q-003): cumulative spend is scoped PER-POLICY via
# LedgerEntry.reference_id -> Checkout.policy_id. A CAPTURE under one policy must
# never erode another policy's signed budget. compute_policy_exposure additionally
# counts outstanding in-flight RESERVE legs for open checkouts.
#
# NOTE: the shared `session` fixture uses a single in-memory SQLite engine that is
# NOT torn down between tests, so every test uses globally-unique ids to remain
# order-independent.

from __future__ import annotations

from datetime import datetime

from openstore.core.database import check_spend_cap, compute_policy_exposure, compute_policy_spend
from openstore.core.ledger import create_capture_entry, create_reserve_entry
from openstore.models import Checkout, OrderState
from sqlmodel import Session


def _seed_checkout(
    session: Session, checkout_id: str, policy_id: str, amount: int = 10000, state: OrderState = OrderState.CREATED,
) -> None:
    session.add(Checkout(
        id=checkout_id,
        trace_id="t",
        client_id="c",
        merchant_id="m",
        cart_hash="h",
        cart_version=1,
        amount_minor=amount,
        currency="INR",
        state=state,
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
    # Two checkouts under two different policies (unique ids, shared-engine safe).
    _seed_checkout(session, "chk_s1a", "pol_s1a")
    _seed_checkout(session, "chk_s1b", "pol_s1b")

    # Only pol_s1a gets an economic CAPTURE (via an escrow RESERVE -> CAPTURE).
    create_reserve_entry(session, "t", "c", "chk_s1a", 10000, "INR", "r_s1a")
    create_capture_entry(session, "t", "c", "chk_s1a", 10000, "INR", "c_s1a")
    session.commit()

    assert compute_policy_spend(session, "pol_s1a") == 10000
    assert compute_policy_spend(session, "pol_s1b") == 0

    # check_spend_cap for pol_s1a sees the 10000 already spent ...
    allowed, _ = check_spend_cap(
        session, merchant_id="m", policy_id="pol_s1a", amount_minor=20000,
        max_spend_per_tx_minor=100000, max_spend_total_minor=25000,
    )
    assert not allowed  # 10000 + 20000 > 25000

    # ... while pol_s1b's budget is untouched: 20000 <= 25000 passes.
    allowed, _ = check_spend_cap(
        session, merchant_id="m", policy_id="pol_s1b", amount_minor=20000,
        max_spend_per_tx_minor=100000, max_spend_total_minor=25000,
    )
    assert allowed


def test_compute_policy_exposure_counts_inflight_reserve(session: Session) -> None:
    # Two open (un-captured) checkouts under pol_e1 with outstanding reserves: one
    # HELD (10000) and one CREATED (5000). Under pol_e2: a settled CAPTURE only.
    _seed_checkout(session, "chk_e1a", "pol_e1", state=OrderState.HELD)
    _seed_checkout(session, "chk_e1b", "pol_e1")
    _seed_checkout(session, "chk_e2a", "pol_e2", state=OrderState.RELEASED)

    create_reserve_entry(session, "t", "c", "chk_e1a", 10000, "INR", "r_e1a")
    create_reserve_entry(session, "t", "c", "chk_e1b", 5000, "INR", "r_e1b")
    # pol_e2's checkout is closed (RELEASED) and fully captured: that value is
    # settled spend, not an outstanding in-flight reserve.
    create_reserve_entry(session, "t", "c", "chk_e2a", 8000, "INR", "r_e2a")
    create_capture_entry(session, "t", "c", "chk_e2a", 8000, "INR", "c_e2a")
    session.commit()

    # pol_e1: no settled spend, but exposure counts the 10000 + 5000 in-flight
    # reserves on its open checkouts.
    assert compute_policy_spend(session, "pol_e1") == 0
    assert compute_policy_exposure(session, "pol_e1") == 15000
    # pol_e2: settled spend 8000 (captured); its checkout left the open set so no
    # additional in-flight reserve is counted.
    assert compute_policy_spend(session, "pol_e2") == 8000
    assert compute_policy_exposure(session, "pol_e2") == 8000


def test_check_spend_cap_uses_exposure_not_settled_spend(session: Session) -> None:
    """An in-flight reserve on an open checkout must consume the cumulative cap."""
    _seed_checkout(session, "chk_x1", "pol_x1")
    create_reserve_entry(session, "t", "c", "chk_x1", 10000, "INR", "r_x1")
    session.commit()

    # Settled spend is 0, but exposure is 10000 (the outstanding reserve). A
    # 20000 cart against a 25000 cap would pass on settled spend alone
    # (0+20000<=25000) but must fail on exposure (10000+20000=30000>25000).
    assert compute_policy_spend(session, "pol_x1") == 0
    assert compute_policy_exposure(session, "pol_x1") == 10000
    allowed, reason = check_spend_cap(
        session, merchant_id="m", policy_id="pol_x1", amount_minor=20000,
        max_spend_per_tx_minor=100000, max_spend_total_minor=25000,
    )
    assert not allowed
    assert reason == "policy.spend_cumulative_exceeded"
