# OpenStore core — Commerce Core API (single entry point for all adapters)

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.audit import audit_log
from openstore.core.compiler import CompilerContext, CompilerResult, compile_decision
from openstore.core.database import (
    check_spend_cap,
    compute_policy_exposure,
    get_or_create_checkout,
    update_checkout_state,
)
from openstore.core.holdcancel import (
    AALLevel,
    cancel_hold,
    check_and_expire_checkouts,
    initiate_hold,
)
from openstore.core.idempotency import (
    generate_idempotency_key,
)
from openstore.core.ledger import verify_ledger_balances
from openstore.core.webauthn_rp import WebAuthnError, complete_assertion
from openstore.core.webhooks import (
    process_webhook_retry_queue,
)
from openstore.models import (
    AuditLog,
    Campaign,
    Checkout,
    IntentPolicy,
    LedgerEntry,
    OrderState,
)


class CommerceError(Exception):
    def __init__(self, reason_code: str, message: str, status_code: int = 400):
        self.reason_code = reason_code
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{reason_code}] {message}")


def create_checkout(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    merchant_id: str,
    cart_items: list[dict[str, Any]],
    cart_hash: str,
    cart_version: int,
    policy_id: str,
    webauthn_assertion: dict[str, Any] | None = None,
    agent_plan: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> CompilerResult:
    """
    Core checkout creation flow.

    1. Load policy
    2. Compile decision (12 checks)
    3. If allowed: create checkout record, initiate hold
    4. Return CompilerResult with AAL level
    """
    # Load policy
    policy = session.exec(
        select(IntentPolicy).where(
            IntentPolicy.id == policy_id,
            IntentPolicy.merchant_id == merchant_id,
            IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
        )
    ).first()

    if not policy:
        raise CommerceError("policy_not_found", "Active policy not found for merchant", 404)

    # Verify WebAuthn assertion if provided
    assertion_verified = False
    assertion_age = 0

    if webauthn_assertion:
        try:
            verified, sign_count = complete_assertion(
                session=session,
                config=config,
                user_handle=merchant_id,
                credential_id=webauthn_assertion["credential_id"],
                client_data_json=webauthn_assertion["client_data_json"],
                authenticator_data=webauthn_assertion["authenticator_data"],
                signature=webauthn_assertion["signature"],
                challenge_b64url=webauthn_assertion["challenge"],
                binding={"mode": "cart", "cart_hash": cart_hash},
            )
            assertion_verified = verified
            # Calculate assertion age
            assertion_age = webauthn_assertion.get("age_seconds", 0)
        except WebAuthnError:
            assertion_verified = False

    return create_checkout_from_policy(
        config=config,
        session=session,
        trace_id=trace_id,
        client_id=client_id,
        merchant_id=merchant_id,
        cart_items=cart_items,
        cart_hash=cart_hash,
        cart_version=cart_version,
        policy=policy,
        assertion_verified=assertion_verified,
        assertion_age_seconds=assertion_age,
        agent_plan=agent_plan,
        idempotency_key=idempotency_key,
    )


def create_checkout_from_policy(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    merchant_id: str,
    cart_items: list[dict[str, Any]],
    cart_hash: str,
    cart_version: int,
    policy: IntentPolicy,
    assertion_verified: bool = False,
    assertion_age_seconds: int = 0,
    agent_plan: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> CompilerResult:
    """
    Checkout creation against an already-resolved policy object.

    create_checkout()'s body (policy lookup, campaign resolution, compile,
    checkout persistence) minus the policy lookup and WebAuthn re-verification
    — factored out so a caller that already has a policy snapshot (S11 Phase
    4: an amendment-relieved, unpersisted, one-time policy override — Q-020)
    can drive the exact same compile-and-persist path without a second DB
    round-trip re-fetching the (unrelieved) real policy row.
    """
    # Audit log
    audit_log(
        session,
        trace_id,
        client_id,
        "create_checkout",
        "checkout",
        request_path="/checkout",
        request_method="POST",
    )

    policy_id = policy.id

    # Load campaigns referenced in cart
    campaign_lookup = {}
    for item in cart_items:
        campaign_id = item.get("campaign_id")
        if campaign_id and campaign_id not in campaign_lookup:
            campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
            if campaign:
                campaign_lookup[campaign_id] = {
                    "id": campaign.id,
                    "state": campaign.state.value,
                    "offer_terms": {
                        "discount_bps": campaign.discount_bps,
                        "applies_to_skus": campaign.applies_to_skus,
                        "starts_at": campaign.starts_at.isoformat() + "Z",
                        "ends_at": campaign.ends_at.isoformat() + "Z",
                    },
                }

    # Compute cumulative spend for this policy (§3.2c / Q-003: per-policy via
    # reference_id -> Checkout.policy_id; server-side R0.8). Exposure (settled +
    # in-flight RESERVE legs on open checkouts) is authoritative, so concurrent
    # checkouts cannot silently exceed the signed budget (TOCTOU, INV-11).
    cumulative_spend = compute_policy_exposure(session, policy_id)

    # Count checkouts for this policy
    checkout_count = len(
        list(session.exec(select(Checkout).where(Checkout.policy_id == policy_id)).all())
    )

    # Build compiler context
    ctx = CompilerContext(
        cart_items=cart_items,
        policy=policy,
        merchant_id=merchant_id,
        currency=policy.currency,
        checkout_count=checkout_count,
        cumulative_spend_minor=cumulative_spend,
        has_webauthn_assertion=assertion_verified,
        assertion_age_seconds=assertion_age_seconds,
        now_unix=int(datetime.now(UTC).timestamp()),
        campaign_lookup=campaign_lookup,
    )

    # Compile decision
    result = compile_decision(ctx)

    if not result.allowed:
        # Audit failure
        audit_log(
            session,
            trace_id,
            client_id,
            "checkout_denied",
            "checkout",
            resource_id=policy_id,
            response_status=400,
            metadata={"reason_code": result.reason_code, "transcript": result.transcript},
        )
        return result

    # Generate checkout ID if not provided
    checkout_id = idempotency_key or f"chk_{trace_id[:16]}"

    # Create or get checkout (idempotent)
    cart_snapshot = {
        "items": cart_items,
        "cart_hash": cart_hash,
        "cart_version": cart_version,
        "amount_minor": result.effective_amount_minor,
        "currency": policy.currency,
        "campaign_lookup": campaign_lookup,
    }

    idem_key = idempotency_key or generate_idempotency_key(
        "checkout", trace_id, client_id, checkout_id
    )

    checkout, created = get_or_create_checkout(
        session=session,
        checkout_id=checkout_id,
        trace_id=trace_id,
        client_id=client_id,
        merchant_id=merchant_id,
        cart_hash=cart_hash,
        cart_version=cart_version,
        amount_minor=result.effective_amount_minor,
        currency=policy.currency,
        policy_id=policy_id,
        policy_hash=policy.policy_hash,
        aal_level=result.aal_level,
        expires_at=datetime.now(UTC).replace(tzinfo=None),  # Will be updated by initiate_hold
        idempotency_key=idem_key,
        cart_snapshot=cart_snapshot,
        agent_plan=agent_plan,
    )

    # Initiate hold (creates RESERVE ledger entry) only on first creation.
    # On an idempotent re-invocation the checkout already exists in a lifecycle
    # state (e.g. HELD), so re-running initiate_hold would wrongly raise.
    if created:
        initiate_hold(
            session=session,
            config=config,
            checkout_id=checkout.id,
            trace_id=trace_id,
            client_id=client_id,
            merchant_id=merchant_id,
            amount_minor=result.effective_amount_minor,
            currency=policy.currency,
            aal_level=AALLevel(result.aal_level),
            policy_id=policy_id,
            policy_hash=policy.policy_hash,
            max_spend_per_tx_minor=policy.max_spend_per_tx_minor,
            max_spend_total_minor=policy.max_spend_total_minor,
        )

    # Audit success
    audit_log(
        session,
        trace_id,
        client_id,
        "checkout_created",
        "checkout",
        resource_id=checkout.id,
        response_status=201,
        metadata={"aal_level": result.aal_level, "amount_minor": result.effective_amount_minor},
    )

    return dataclasses.replace(result, checkout_id=checkout.id)


def get_checkout(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
) -> Checkout | None:
    """Get checkout by ID with audit."""
    audit_log(
        session,
        trace_id,
        client_id,
        "get_checkout",
        "checkout",
        resource_id=checkout_id,
        request_path=f"/checkout/{checkout_id}",
        request_method="GET",
    )

    return session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()


def confirm_checkout(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    psp_order_id: str,
    psp_payment_link_id: str,
    webauthn_assertion: dict[str, Any] | None = None,
) -> Checkout:
    """
    Confirm a checkout after the payment link was paid.

    Re-verifies on the server, never trusts client state (R0.8):
      - HELD → RELEASED (a real transition; no HELD→HELD no-op). Already-terminal
        checkouts are returned idempotently.
      - AAL0 (no_human_authority) authorizes the agent, so per-cart human
        authority verification is skipped; the spend cap is still re-checked.
      - AAL1+ requires (and re-verifies) a fresh WebAuthn assertion.
    """
    audit_log(
        session,
        trace_id,
        client_id,
        "confirm_checkout",
        "checkout",
        resource_id=checkout_id,
        request_method="POST",
    )

    checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

    if not checkout:
        raise CommerceError("checkout_not_found", "Checkout not found", 404)

    # Terminal states are absorbing: confirm is idempotent once released/refunded.
    if checkout.state in (OrderState.RELEASED, OrderState.PAID, OrderState.REFUNDED):
        return checkout

    if checkout.state != OrderState.HELD:
        raise CommerceError("invalid_state", f"Checkout not in HELD state: {checkout.state}", 400)

    policy = None
    if checkout.policy_id:
        policy = session.exec(
            select(IntentPolicy).where(IntentPolicy.id == checkout.policy_id)
        ).first()

    # AAL0 (no_human_authority): the agent is authorized to transact autonomously,
    # so no per-cart human assertion is required at confirm time.
    allow_autonomous = bool(policy and policy.no_human_authority)

    # AAL1+ requires a fresh, verified WebAuthn assertion at confirm time.
    if not allow_autonomous:
        if not webauthn_assertion:
            raise CommerceError(
                "assertion_required",
                "Cataloged purchase requires a WebAuthn assertion to confirm",
                400,
            )
        try:
            verified, _sign_count = complete_assertion(
                session=session,
                config=config,
                user_handle=checkout.merchant_id,
                credential_id=webauthn_assertion["credential_id"],
                client_data_json=webauthn_assertion["client_data_json"],
                authenticator_data=webauthn_assertion["authenticator_data"],
                signature=webauthn_assertion["signature"],
                challenge_b64url=webauthn_assertion["challenge"],
            )
        except WebAuthnError:
            raise CommerceError("assertion_required", "WebAuthn assertion failed to verify", 400)

    # Re-check the spend cap server-side (exposure: settled + in-flight RESERVE
    # legs for this policy under evaluation) so a confirm cannot blow the budget.
    if policy:
        allowed, reason = check_spend_cap(
            session=session,
            merchant_id=checkout.merchant_id,
            policy_id=checkout.policy_id or "",
            amount_minor=checkout.amount_minor,
            max_spend_per_tx_minor=policy.max_spend_per_tx_minor,
            max_spend_total_minor=policy.max_spend_total_minor,
        )
        if not allowed:
            raise CommerceError(reason, f"Spend cap exceeded at confirm time: {reason}", 400)

    # HELD -> RELEASED: the funds are reserved and settlement confirmed. This is a
    # real transition (stamps released_at / paid_at), not a HELD->HELD no-op.
    checkout = update_checkout_state(
        session=session,
        checkout_id=checkout_id,
        new_state=OrderState.RELEASED,
        psp_order_id=psp_order_id or checkout.psp_order_id,
        psp_payment_link_id=psp_payment_link_id or checkout.psp_payment_link_id,
    )

    return checkout


def cancel_hold_flow(
    session: Session,
    trace_id: str,
    client_id: str,
    cancel_token: str,
) -> Checkout:
    """
    Cancel hold via cancel token (idempotent).

    Called from /hold/{cancel_token}/cancel endpoint.
    """
    audit_log(session, trace_id, client_id, "cancel_hold", "checkout", request_method="POST")

    checkout = session.exec(select(Checkout).where(Checkout.cancel_token == cancel_token)).first()

    if not checkout:
        raise CommerceError("invalid_cancel_token", "Cancel token not found", 404)

    if checkout.state != OrderState.HELD:
        raise CommerceError("invalid_state", f"Checkout not in HELD state: {checkout.state}", 400)

    return cancel_hold(
        session=session,
        checkout_id=checkout.id,
        trace_id=trace_id,
        client_id=client_id,
        reason="Cancelled via hold/cancel link",
    )


def verify_checkout_evidence(
    session: Session,
    checkout_id: str,
) -> dict[str, Any]:
    """Get complete evidence for a checkout (for PoAI bundle)."""
    checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

    if not checkout:
        return {"error": "checkout_not_found"}

    # Get ledger entries
    ledger_entries = list(
        session.exec(select(LedgerEntry).where(LedgerEntry.reference_id == checkout_id)).all()
    )

    # Get audit logs
    audit_logs = list(
        session.exec(select(AuditLog).where(AuditLog.resource_id == checkout_id)).all()
    )

    # Verify ledger balances
    balanced = verify_ledger_balances(session, checkout_id)

    return {
        "checkout": {
            "id": checkout.id,
            "trace_id": checkout.trace_id,
            "client_id": checkout.client_id,
            "merchant_id": checkout.merchant_id,
            "amount_minor": checkout.amount_minor,
            "currency": checkout.currency,
            "state": checkout.state.value,
            "aal_level": checkout.aal_level,
            "cart_hash": checkout.cart_hash,
            "cart_version": checkout.cart_version,
            "expires_at": checkout.expires_at.isoformat() + "Z",
            "created_at": checkout.created_at.isoformat() + "Z",
            "paid_at": checkout.paid_at.isoformat() + "Z" if checkout.paid_at else None,
            "released_at": checkout.released_at.isoformat() + "Z" if checkout.released_at else None,
            "cancelled_at": checkout.cancelled_at.isoformat() + "Z"
            if checkout.cancelled_at
            else None,
        },
        "ledger_entries": [
            {
                "id": e.id,
                "entry_type": e.entry_type.value,
                "amount_minor": e.amount_minor,
                "currency": e.currency,
                "account": e.account,
                "counterparty_account": e.counterparty_account,
                "description": e.description,
                "created_at": e.created_at.isoformat() + "Z",
            }
            for e in ledger_entries
        ],
        "audit_logs": [
            {
                "id": a.id,
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "response_status": a.response_status,
                "created_at": a.created_at.isoformat() + "Z",
            }
            for a in audit_logs
        ],
        "ledger_balanced": balanced,
    }


def run_sweepers(config: Settings, session: Session) -> dict[str, int]:
    """Run all maintenance sweepers (INV-7, INV-8)."""
    expired_checkouts = check_and_expire_checkouts(session)
    retried_webhooks = process_webhook_retry_queue(session)

    return {
        "expired_checkouts_processed": expired_checkouts,
        "webhooks_retried": retried_webhooks,
    }
