# OpenStore core — Hold/Cancel state machine (AAL ladder)

from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.database import check_spend_cap
from openstore.core.ledger import (
    create_capture_entry,
    create_refund_entry,
    create_release_entry,
    create_reserve_entry,
)
from openstore.models import Checkout, OrderState


class AALLevel(IntEnum):
    AAL0 = 0  # No human authority (policy.no_human_authority)
    AAL1 = 1  # Hold 3600s (1 hour)
    AAL2 = 2  # Hold 900s (15 minutes)
    AAL3 = 3  # Hold 0s (immediate release)


# AAL hold durations in seconds
AAL_HOLD_SECONDS = {
    AALLevel.AAL0: None,  # No hold - no order created
    AALLevel.AAL1: 3600,
    AALLevel.AAL2: 900,
    AALLevel.AAL3: 0,
}

# AAL liability sentences (for verifier output)
AAL_LIABILITY = {
    AALLevel.AAL0: "Proposed liability position (not a network rule): Merchant bears full liability; no human authorization captured.",
    AALLevel.AAL1: "Proposed liability position (not a network rule): Merchant bears liability; human authorized via WebAuthn with 1-hour cancellation window.",
    AALLevel.AAL2: "Proposed liability position (not a network rule): Shared liability; human authorized via WebAuthn with 15-minute cancellation window.",
    AALLevel.AAL3: "Proposed liability position (not a network rule): Customer bears liability; human authorized via WebAuthn with immediate settlement.",
}


def compute_aal_level(
    has_webauthn_assertion: bool,
    policy_allows_no_human_authority: bool,
    amount_minor: int,
    threshold_aal3: int = 10000,  # ₹100
    threshold_aal2: int = 50000,  # ₹500
) -> AALLevel:
    """
    Compute AAL level based on policy and amount.

    AAL3: Small amounts, immediate release
    AAL2: Medium amounts, 15-min hold
    AAL1: Large amounts, 1-hour hold
    AAL0: Policy explicitly allows no human authority
    """
    if policy_allows_no_human_authority:
        return AALLevel.AAL0

    if not has_webauthn_assertion:
        raise ValueError("WebAuthn assertion required for AAL > 0")

    if amount_minor <= threshold_aal3:
        return AALLevel.AAL3
    elif amount_minor <= threshold_aal2:
        return AALLevel.AAL2
    else:
        return AALLevel.AAL1


def get_hold_duration(aal_level: AALLevel) -> int | None:
    """Get hold duration in seconds for AAL level."""
    return AAL_HOLD_SECONDS.get(aal_level)


def get_aal_liability_sentence(aal_level: AALLevel) -> str:
    """Get liability sentence for AAL level."""
    return AAL_LIABILITY.get(aal_level, "Unknown AAL level")


def calculate_expires_at(aal_level: AALLevel, created_at: datetime | None = None) -> datetime:
    """Calculate checkout expiry based on AAL level."""
    if created_at is None:
        created_at = datetime.utcnow()

    hold_seconds = get_hold_duration(aal_level)

    if hold_seconds is None:
        # AAL0 - no expiry (policy.no_human_authority)
        return created_at + timedelta(days=365)
    elif hold_seconds == 0:
        # AAL3 - immediate expiry (release immediately)
        return created_at
    else:
        # AAL1/AAL2 - hold period + buffer
        return created_at + timedelta(seconds=hold_seconds + 60)  # 60s buffer


def initiate_hold(
    session: Session,
    config: Settings,
    checkout_id: str,
    trace_id: str,
    client_id: str,
    merchant_id: str,
    amount_minor: int,
    currency: str,
    aal_level: AALLevel,
    policy_id: str | None,
    policy_hash: str | None,
) -> Checkout:
    """
    Initiate hold period after successful payment authorization.

    INV-4: Intent-first then outbox (dual-write with reference_id = checkout_id).
    INV-5: Ledger RESERVE entry created.
    INV-8: checkout.expires_at enforced.
    """
    from openstore.core.idempotency import generate_idempotency_key
    from openstore.models import Checkout

    checkout = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if not checkout:
        raise ValueError(f"Checkout not found: {checkout_id}")

    if checkout.state != OrderState.CREATED:
        raise ValueError(f"Checkout not in CREATED state: {checkout.state}")

    # INV-11: Check spend cap in IMMEDIATE transaction
    if policy_id:
        from openstore.models import IntentPolicy
        policy = session.exec(
            select(IntentPolicy).where(IntentPolicy.id == policy_id)
        ).first()
        if policy:
            allowed, reason = check_spend_cap(
                session=session,
                merchant_id=merchant_id,
                policy_id=policy_id,
                amount_minor=amount_minor,
                max_spend_per_tx_minor=policy.max_spend_per_tx_minor,
                max_spend_total_minor=policy.max_spend_total_minor,
            )
            if not allowed:
                raise ValueError(f"Spend cap check failed: {reason}")

    # Create RESERVE ledger entry (INV-5)
    generate_idempotency_key("reserve", trace_id, client_id, checkout_id)
    create_reserve_entry(
        session=session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout_id,
        amount_minor=amount_minor,
        currency=currency,
        description=f"Hold initiated for checkout {checkout_id}",
    )

    # Calculate expiry
    expires_at = calculate_expires_at(aal_level)

    # Update checkout
    checkout.state = OrderState.HELD
    checkout.aal_level = int(aal_level)
    checkout.expires_at = expires_at
    checkout.updated_at = datetime.utcnow()
    session.add(checkout)
    session.flush()

    return checkout


def release_hold(
    session: Session,
    checkout_id: str,
    trace_id: str,
    client_id: str,
) -> Checkout:
    """
    Release hold after hold period expires (AAL1/AAL2) or immediately (AAL3).

    INV-5: Ledger CAPTURE entry created.
    """
    from openstore.core.idempotency import generate_idempotency_key
    from openstore.models import Checkout

    checkout = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if not checkout:
        raise ValueError(f"Checkout not found: {checkout_id}")

    if checkout.state != OrderState.HELD:
        raise ValueError(f"Checkout not in HELD state: {checkout.state}")

    # Create CAPTURE ledger entry (INV-5)
    generate_idempotency_key("capture", trace_id, client_id, checkout_id)
    create_capture_entry(
        session=session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout_id,
        amount_minor=checkout.amount_minor,
        currency=checkout.currency,
        description=f"Hold released for checkout {checkout_id}",
    )

    checkout.state = OrderState.RELEASED
    checkout.released_at = datetime.utcnow()
    checkout.updated_at = datetime.utcnow()
    session.add(checkout)
    session.flush()

    return checkout


def cancel_hold(
    session: Session,
    checkout_id: str,
    trace_id: str,
    client_id: str,
    reason: str = "Cancelled by user",
) -> Checkout:
    """
    Cancel hold while in HELD state.

    INV-5: Ledger RELEASE entry created (reverses RESERVE).
    Returns budget to ledger.
    """
    from openstore.core.idempotency import generate_idempotency_key
    from openstore.models import Checkout

    checkout = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if not checkout:
        raise ValueError(f"Checkout not found: {checkout_id}")

    if checkout.state != OrderState.HELD:
        raise ValueError(f"Checkout not in HELD state (cannot cancel): {checkout.state}")

    # Create RELEASE ledger entry (INV-5)
    generate_idempotency_key("release", trace_id, client_id, checkout_id)
    create_release_entry(
        session=session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout_id,
        amount_minor=checkout.amount_minor,
        currency=checkout.currency,
        description=f"Hold cancelled: {reason}",
    )

    checkout.state = OrderState.CANCELLED
    checkout.cancelled_at = datetime.utcnow()
    checkout.updated_at = datetime.utcnow()
    session.add(checkout)
    session.flush()

    return checkout


def refund_checkout(
    session: Session,
    checkout_id: str,
    trace_id: str,
    client_id: str,
    reason: str = "Refund requested",
) -> Checkout:
    """
    Refund a released checkout.

    INV-5: Ledger REFUND entry created (reverses CAPTURE).
    """
    from openstore.core.idempotency import generate_idempotency_key
    from openstore.models import Checkout

    checkout = session.exec(
        select(Checkout).where(Checkout.id == checkout_id)
    ).first()

    if not checkout:
        raise ValueError(f"Checkout not found: {checkout_id}")

    if checkout.state not in (OrderState.RELEASED, OrderState.CANCELLED):
        raise ValueError(f"Checkout not refundable: {checkout.state}")

    # Create REFUND ledger entry (INV-5)
    generate_idempotency_key("refund", trace_id, client_id, checkout_id)
    create_refund_entry(
        session=session,
        trace_id=trace_id,
        client_id=client_id,
        checkout_id=checkout_id,
        amount_minor=checkout.amount_minor,
        currency=checkout.currency,
        description=f"Refund: {reason}",
    )

    checkout.state = OrderState.REFUNDED
    checkout.cancelled_at = datetime.utcnow()
    checkout.updated_at = datetime.utcnow()
    session.add(checkout)
    session.flush()

    return checkout


def check_and_expire_checkouts(session: Session) -> int:
    """
    INV-8: Sweeper to enforce checkout.expires_at.

    - Expired HELD checkouts -> auto-release (CAPTURE) for AAL3
    - Expired HELD checkouts -> auto-cancel (RELEASE) for AAL1/AAL2
    - Expired CREATED checkouts -> CANCELLED

    Returns number of checkouts processed.
    """
    from openstore.models import Checkout

    now = datetime.utcnow()

    # Find expired checkouts
    expired = session.exec(
        select(Checkout).where(
            Checkout.expires_at < now,
            Checkout.state.in_([OrderState.CREATED, OrderState.HELD]),  # type: ignore[attr-defined]
        )
    ).all()

    count = 0
    for checkout in expired:
        if checkout.state == OrderState.CREATED:
            # Never paid - just cancel
            checkout.state = OrderState.CANCELLED
            checkout.cancelled_at = now
            checkout.updated_at = now
            session.add(checkout)
            count += 1
        elif checkout.state == OrderState.HELD:
            aal = AALLevel(checkout.aal_level)
            if aal == AALLevel.AAL3:
                # Immediate release
                release_hold(session, checkout.id, checkout.trace_id, checkout.client_id)
            else:
                # AAL1/AAL2 - hold expired, cancel and release funds
                cancel_hold(session, checkout.id, checkout.trace_id, checkout.client_id, "Hold period expired")
            count += 1

    session.flush()
    return count
