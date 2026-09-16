# OpenStore PSP — Razorpay driver (Stage 5)
#
# Implements INV-4 (Intent-first, then outbox dual-write), INV-6 (webhooks),
# INV-7 (reconciliation), the S5.2 driver per PRD §3.4.
#
# Live test mode only. Hard-fails on live-mode keys (key prefix rzp_live_).
# Reference ID is the checkout_id (deterministic; max 40 chars; UUID4 is 36).

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.audit import audit_log
from openstore.core.idempotency import (
    compute_request_hash,
    generate_idempotency_key,
)
from openstore.core.ledger import create_capture_entry, create_release_entry
from openstore.models import (
    Checkout,
    IdempotencyKey,
    OrderState,
    WebhookEvent,
    WebhookStatus,
)

# [verify-at-build] Pinned Razorpay duplicate-reference markers — captured
# against live test-mode on 2026-09-10 (see scripts/capture_constants.py and
# tests/GOLDEN/razorpay/duplicate_reference_id_error.json). Do NOT invent
# these from memory (R0.7).
#
# Live truth: the SDK raises BadRequestError whose str() carries NO code —
# only the description text, e.g. "payment link with given reference_id:
# <ref> already exists. Please create a payment link with a different
# reference_id". The raw HTTP body carries code BAD_REQUEST_ERROR with the
# same description. An older observed variant reads "reference_id provided
# is already used. Please provide another value". The driver therefore
# matches on the two description markers below (never bare-Exception
# catching), keeping the legacy code match only so old mocks still recover.
RAZORPAY_DUPLICATE_REFERENCE_ID_ERROR_CODE = "REFERENCE_ID_ALREADY_EXISTS"
# Substring markers observed live in duplicate-reference rejections, matched
# case-insensitively; BOTH must be present (either "already exists" or
# "already used" variant).
RAZORPAY_DUPLICATE_REFERENCE_MARKERS = ("reference_id", ("already exists", "already used"))


def is_duplicate_reference_error(err_code: str | None, err_str: str) -> bool:
    """True when a payment-link create failure is a duplicate-reference_id
    rejection (live-verified shapes only)."""
    if err_code == RAZORPAY_DUPLICATE_REFERENCE_ID_ERROR_CODE:
        return True
    lowered = (err_str or "").lower()
    anchor, variants = RAZORPAY_DUPLICATE_REFERENCE_MARKERS
    return anchor in lowered and any(v in lowered for v in variants)

# Cancel-already-paid: Razorpay returns HTTP 400 with this body shape when
# attempting to cancel a payment_link that has already been paid. Per
# DECISIONS §11.1.2, on this exact response the driver issues an idempotent
# refund instead of a release.
RAZORPAY_CANCEL_ALREADY_PAID_HTTP_STATUS = 400


class RazorpayError(Exception):
    """Razorpay driver error. Catches ONLY pinned error codes (S5.2 / R0.5)."""

    def __init__(self, error_code: str, message: str, http_status: int | None = None):
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[{error_code}] {message} (http={http_status})")


@dataclass
class PspIntent:
    """Local record of a payment intent. Persisted BEFORE the network call (INV-4)."""

    checkout_id: str
    psp_provider: str
    state: str  # PENDING | SUCCEEDED | FAILED
    psp_order_id: str | None = None
    psp_payment_link_id: str | None = None
    short_url: str | None = None
    trace_id: str = ""
    client_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# Webhook event types the driver handles (PRD Part 4 / S5.1).
HANDLED_WEBHOOK_EVENTS = frozenset(
    {
        "payment_link.paid",
        "payment_link.cancelled",
        "payment_link.partially_paid",
        "payment.failed",
    }
)


# Allowed order-state transitions (INV-6, terminal states absorbing).
ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.HELD, OrderState.CANCELLED, OrderState.FAILED}),
    OrderState.HELD: frozenset(
        {OrderState.PAID, OrderState.RELEASED, OrderState.CANCELLED, OrderState.FAILED}
    ),
    OrderState.PAID: frozenset({OrderState.RELEASED, OrderState.REFUNDED}),
    OrderState.RELEASED: frozenset({OrderState.REFUNDED}),
    OrderState.CANCELLED: frozenset({OrderState.REFUNDED}),
    OrderState.FAILED: frozenset(),
    OrderState.REFUNDED: frozenset(),
}


def assert_test_mode_key(key_id: str) -> None:
    """S5: Hard-fail on live-mode keys."""
    if not key_id:
        raise RazorpayError("psp.live_key_forbidden", "Empty Razorpay key_id", None)
    if key_id.startswith("rzp_live_"):
        raise RazorpayError(
            "psp.live_key_forbidden",
            "Live-mode Razorpay keys are not permitted. Use test-mode keys (rzp_test_).",
            None,
        )


# ---------------------------------------------------------------------------
# S5.2: Driver — payment link create/fetch/cancel/refund
# ---------------------------------------------------------------------------


def create_payment_link(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int,
    currency: str = "INR",
    description: str = "",
    customer: dict[str, Any] | None = None,
    notes: dict[str, Any] | None = None,
    mock_razorpay: Any | None = None,
) -> Checkout:
    """
    Create a Razorpay payment link per INV-4 dual-write ordering.

    Order:
      1. BEGIN: write IdempotencyRecord(IN_FLIGHT) + PspIntent(PENDING) → COMMIT
      2. Call Razorpay with reference_id=checkout_id
      3. BEGIN: write PspIntent→SUCCEEDED + Order + ledger CAPTURE-pending +
         IdempotencyRecord→COMPLETED → COMMIT

    Duplicate-create caught by RAZORPAY_DUPLICATE_REFERENCE_ID_ERROR_CODE ONLY
    (never bare Exception). On that code, fetches the existing link and
    adopts it.

    Args:
        mock_razorpay: Optional pre-configured Razorpay client for tests
                       (avoids requiring network). Same interface as razorpay.Client.
    """
    assert_test_mode_key(config.razorpay.key_id)

    if currency != "INR":
        raise RazorpayError(
            "psp.currency_mismatch",
            f"Only INR is supported (got {currency})",
            None,
        )

    if amount_minor <= 0:
        raise RazorpayError(
            "psp.amount_invalid",
            f"amount_minor must be > 0 (got {amount_minor})",
            None,
        )

    # Lookup the checkout
    checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

    if not checkout:
        raise RazorpayError("psp.checkout_not_found", f"Checkout {checkout_id} not found", 404)

    if checkout.state != OrderState.HELD:
        raise RazorpayError(
            "psp.invalid_state",
            f"Checkout must be HELD (got {checkout.state})",
            400,
        )

    # INV-4 step 1: write IdempotencyRecord(IN_FLIGHT) BEFORE the network call.
    idem_key = generate_idempotency_key("razorpay_create", trace_id, client_id, checkout_id)
    request_body = {
        "checkout_id": checkout_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "description": description,
    }
    request_hash = compute_request_hash(request_body)

    existing_idem = session.exec(
        select(IdempotencyKey).where(IdempotencyKey.key == idem_key)
    ).first()

    if existing_idem:
        if existing_idem.request_hash != request_hash:
            raise RazorpayError(
                "idempotency_key_reuse_with_different_payload",
                f"Idempotency key {idem_key} reused with different payload",
                422,
            )
        if existing_idem.response_status == 200:
            # Bug (S14): this replay path restored only psp_order_id/
            # psp_payment_link_id from the cached response, dropping
            # short_url and cancel_token — the checkout came back "success"
            # with no way to pay and no way to cancel. _finalize_payment_link_
            # create (below) stores all four in response_body; a replay must
            # restore all four too, not a subset.
            existing_resp = existing_idem.response_body
            checkout.psp_order_id = existing_resp.get("psp_order_id")
            checkout.psp_payment_link_id = existing_resp.get("psp_payment_link_id")
            checkout.short_url = existing_resp.get("short_url")
            checkout.cancel_token = existing_resp.get("cancel_token") or checkout.cancel_token
            session.add(checkout)
            session.flush()
            return checkout
    else:
        idem = IdempotencyKey(
            key=idem_key,
            trace_id=trace_id,
            client_id=client_id,
            request_hash=request_hash,
            response_status=0,
            response_body={},
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
        )
        session.add(idem)
        session.flush()

    # INV-4 step 2: call Razorpay
    notes_with_trace = {**(notes or {}), "trace_id": trace_id, "checkout_id": checkout_id}
    link_request = {
        "amount": amount_minor,
        "currency": currency,
        "reference_id": checkout_id,
        "description": description or f"Order {checkout_id}",
        "notes": notes_with_trace,
        # SID-1 precedence (mirrors server.py's resolve_public_origin for the
        # request=None case): public_base_url wins when set (subdomain /
        # explicit deployment), else the WebAuthn origin, else localhost.
        # callback_url is Razorpay's post-payment BROWSER redirect (a GET),
        # distinct from the server-to-server webhook POST below — pointing it
        # at /webhooks/razorpay (POST-only) made every real payment redirect
        # into a 405. "/" is the storefront's own GET landing page.
        "callback_url": (
            f"{(config.public_base_url or config.webauthn.origin or 'http://localhost:8000').rstrip('/')}/"
        ),
        "callback_method": "get",
    }
    # Razorpay rejects an empty {} customer object outright — only attach the
    # key when the caller actually has identity to send (R0.3: never fabricate).
    if customer:
        link_request["customer"] = customer

    try:
        if mock_razorpay is not None:
            link = mock_razorpay.payment_link.create(link_request)
        else:
            link = _get_client(config).payment_link.create(link_request)
    except Exception as e:
        err_code = getattr(e, "code", None) or getattr(e, "error", {}).get("code", None)
        err_str = str(e)

        if is_duplicate_reference_error(err_code, err_str):
            existing_link = _fetch_existing_payment_link_by_reference_id(
                config, session, checkout_id, mock_razorpay=mock_razorpay
            )
            if existing_link:
                _finalize_payment_link_create(
                    session, trace_id, client_id, checkout, existing_link, idem_key, request_hash
                )
                audit_log(
                    session,
                    trace_id,
                    client_id,
                    "psp_link_recovered",
                    "checkout",
                    resource_id=checkout_id,
                    response_status=200,
                )
                return checkout
            raise RazorpayError(
                "psp.duplicate_unrecoverable",
                f"Duplicate reference_id but cannot fetch existing link: {err_str}",
                500,
            )
        raise RazorpayError("psp.create_failed", f"Payment link create failed: {err_str}", None)

    _finalize_payment_link_create(
        session, trace_id, client_id, checkout, link, idem_key, request_hash
    )
    audit_log(
        session,
        trace_id,
        client_id,
        "psp_link_created",
        "checkout",
        resource_id=checkout_id,
        response_status=200,
        metadata={"payment_link_id": link.get("id"), "short_url": link.get("short_url")},
    )
    return checkout


def _finalize_payment_link_create(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout: Checkout,
    link: dict[str, Any],
    idem_key: str,
    request_hash: str,
) -> None:
    """INV-4 step 3: finalize (PspIntent→SUCCEEDED, Order, ledger, idempotency)."""
    checkout.psp_order_id = link.get("reference_id") or checkout.id
    checkout.psp_payment_link_id = link.get("id")
    checkout.psp_provider = "razorpay"
    checkout.short_url = link.get("short_url")
    # Mint the cancel token here: the payment link now exists, so the holder of
    # the link must be able to cancel the underlying hold. One high-entropy,
    # unguessable bearer token per payment link (R0.10: the token is not derived
    # from caller-known ids).
    if not checkout.cancel_token:
        checkout.cancel_token = generate_cancel_token()
    checkout.updated_at = datetime.now(UTC)
    session.add(checkout)

    response_body = {
        "psp_order_id": checkout.psp_order_id,
        "psp_payment_link_id": checkout.psp_payment_link_id,
        "short_url": checkout.short_url,
        "cancel_token": checkout.cancel_token,
    }

    idem = session.exec(select(IdempotencyKey).where(IdempotencyKey.key == idem_key)).first()
    if idem:
        idem.response_status = 200
        idem.response_body = response_body
        idem.expires_at = datetime.now(UTC)
        session.add(idem)

    session.flush()


def _fetch_existing_payment_link_by_reference_id(
    config: Settings,
    session: Session,
    reference_id: str,
    mock_razorpay: Any | None = None,
) -> dict[str, Any] | None:
    """Recover a payment link by reference_id (S5.2 recovery path)."""
    try:
        if mock_razorpay is not None:
            client = mock_razorpay
        else:
            client = _get_client(config)
        resp = client.payment_link.all({"reference_id": reference_id})
        items = resp.get("items", [])
        if items:
            return cast("dict[str, Any]", items[0])
    except Exception:
        return None
    return None


def fetch_payment_link(
    config: Settings,
    payment_link_id: str,
    mock_razorpay: Any | None = None,
) -> dict[str, Any]:
    """Fetch an existing payment link by id."""
    assert_test_mode_key(config.razorpay.key_id)
    if mock_razorpay is not None:
        return cast("dict[str, Any]", mock_razorpay.payment_link.fetch(payment_link_id))
    return cast("dict[str, Any]", _get_client(config).payment_link.fetch(payment_link_id))


def cancel_payment_link(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    mock_razorpay: Any | None = None,
) -> dict[str, Any]:
    """
    Cancel a payment link per DECISIONS §11.1.2.

    On the pinned already-paid HTTP 400, issue an idempotent refund and write
    a REFUND ledger entry instead of a RELEASE.

    Returns {"action": "cancelled" | "refunded", ...}.
    """
    assert_test_mode_key(config.razorpay.key_id)

    checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

    if not checkout:
        raise RazorpayError("psp.checkout_not_found", f"Checkout {checkout_id} not found", 404)

    payment_link_id = checkout.psp_payment_link_id
    if not payment_link_id:
        raise RazorpayError("psp.no_payment_link", "Checkout has no payment_link_id", 400)

    try:
        if mock_razorpay is not None:
            resp = mock_razorpay.payment_link.cancel(payment_link_id)
        else:
            resp = _get_client(config).payment_link.cancel(payment_link_id)
    except Exception as e:
        err_str = str(e)
        if RAZORPAY_CANCEL_ALREADY_PAID_HTTP_STATUS == 400 and (
            "400" in err_str or "Bad Request" in err_str
        ):
            refund_checkout(
                config,
                session,
                trace_id,
                client_id,
                checkout_id,
                reason="Cancel-already-paid → refund",
                mock_razorpay=mock_razorpay,
            )
            audit_log(
                session,
                trace_id,
                client_id,
                "psp_cancel_refunded",
                "checkout",
                resource_id=checkout_id,
                response_status=200,
            )
            return {
                "action": "refunded",
                "checkout_id": checkout_id,
                "reason": "cancel_already_paid",
            }
        raise RazorpayError("psp.cancel_failed", f"Cancel failed: {err_str}", None)

    from openstore.core.holdcancel import cancel_hold

    cancel_hold(session, checkout_id, trace_id, client_id, reason="Payment link cancelled")
    audit_log(
        session,
        trace_id,
        client_id,
        "psp_link_cancelled",
        "checkout",
        resource_id=checkout_id,
        response_status=200,
    )
    return {
        "action": "cancelled",
        "checkout_id": checkout_id,
        "razorpay_response": dict(resp) if hasattr(resp, "items") else resp,
    }


def cancel_checkout_by_id(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    checkout: Checkout,
    mock_razorpay: Any | None = None,
) -> dict[str, Any]:
    """Cancel an already-located checkout (S11 Phase 3 / plan item #16).

    Shared by POST /hold/{cancel_token}/cancel (psp/router.py, which looks
    the checkout up by cancel_token) and the bot `cancel <checkout_id>`
    command (buyer_agent.py, which looks it up by id and checks ownership).
    Refactored out of psp/router.py's hold_cancel so the already-paid
    fallback try/except lives in exactly one place. Caller commits.
    """
    if checkout.state not in (OrderState.HELD,):
        raise RazorpayError(
            "psp.invalid_state",
            f"invalid_state: {checkout.state}",
            400,
        )

    if mock_razorpay is None:
        try:
            mock_razorpay = _get_client(config)
        except Exception:
            mock_razorpay = None

    try:
        resp = cancel_payment_link(
            config=config,
            session=session,
            trace_id=trace_id,
            client_id=client_id,
            checkout_id=checkout.id,
            mock_razorpay=mock_razorpay,
        )
    except Exception as e:
        err_str = str(e)
        if (
            "already_paid" in err_str.lower()
            or "already cancelled" in err_str.lower()
            or "400" in err_str
        ):
            create_release_entry(
                session=session,
                trace_id=trace_id,
                client_id=client_id,
                checkout_id=checkout.id,
                amount_minor=checkout.amount_minor,
                currency=checkout.currency,
                description="hold_cancel (already-paid/cancelled)",
            )
            # Stage 26: inventory follows the money RELEASE (guarded no-op
            # when nothing is outstanding).
            from openstore.core.inventory import release_checkout_stock

            release_checkout_stock(
                session,
                checkout.merchant_id,
                checkout.id,
                (checkout.cart_snapshot or {}).get("items", []),
                trace_id,
                client_id,
            )
            checkout.state = OrderState.CANCELLED
            checkout.cancelled_at = datetime.now(UTC)
            checkout.updated_at = datetime.now(UTC)
            session.add(checkout)
            return {"status": "RELEASE", "checkout_id": checkout.id}
        raise

    return {
        "status": "RELEASE" if resp.get("action") == "cancelled" else "REFUND",
        "checkout_id": checkout.id,
        "psp_action": resp.get("action"),
    }


def refund_checkout(
    config: Settings,
    session: Session,
    trace_id: str,
    client_id: str,
    checkout_id: str,
    amount_minor: int | None = None,
    reason: str = "Refund",
    mock_razorpay: Any | None = None,
) -> dict[str, Any]:
    """Issue an idempotent refund against the PSP (refunds are idempotent requests).

    Q-027: Razorpay's refund API requires a payment_id (pay_...), not a
    payment_link_id (plink_...). Prefer checkout.psp_payment_id (stored at
    webhook time); fall back to fetching the payment_link and reading its
    payments array.
    """
    assert_test_mode_key(config.razorpay.key_id)

    checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

    if not checkout:
        raise RazorpayError("psp.checkout_not_found", f"Checkout {checkout_id} not found", 404)

    # Get payment_id: prefer stored value, else fetch from payment_link
    payment_id = checkout.psp_payment_id
    if not payment_id:
        link_id = checkout.psp_payment_link_id
        if not link_id:
            raise RazorpayError("psp.no_payment_link", "Checkout has no payment_link_id", 400)
        # Fetch payment_link to get the payment_id (Q-027 fallback)
        if mock_razorpay is not None:
            payment_link = mock_razorpay.payment_link.fetch(link_id)
        else:
            payment_link = _get_client(config).payment_link.fetch(link_id)
        payments = payment_link.get("payments", [])
        if not payments:
            raise RazorpayError("psp.no_payment", "No payment found for payment_link", 400)
        payment_id = payments[-1].get("id")
        if not payment_id:
            raise RazorpayError("psp.no_payment_id", "Payment link has no payment ID", 400)
        # Cache for future refunds
        checkout.psp_payment_id = payment_id
        session.add(checkout)
        session.flush()

    refund_amount = amount_minor or checkout.amount_minor

    refund_request = {
        "amount": refund_amount,
        "speed": "optimum",
        "notes": {"trace_id": trace_id, "checkout_id": checkout_id, "reason": reason},
    }

    try:
        if mock_razorpay is not None:
            refund = mock_razorpay.payment.refund(payment_id, refund_request)
        else:
            refund = _get_client(config).payment.refund(payment_id, refund_request)
    except Exception as e:
        raise RazorpayError("psp.refund_failed", f"Refund failed: {e}", None)

    create_refund_entry_local(session, trace_id, client_id, checkout, refund_amount, reason)

    audit_log(
        session,
        trace_id,
        client_id,
        "psp_refund_issued",
        "checkout",
        resource_id=checkout_id,
        response_status=200,
        metadata={"refund_id": refund.get("id"), "amount_minor": refund_amount},
    )
    return (
        dict(refund)
        if hasattr(refund, "items")
        else {"refund_id": refund.get("id") if hasattr(refund, "get") else None}
    )


def create_refund_entry_local(
    session: Session,
    trace_id: str,
    client_id: str,
    checkout: Checkout,
    amount_minor: int,
    description: str,
) -> None:
    """Write the REFUND ledger entry pair."""
    from openstore.models import LedgerEntry, LedgerEntryType

    idem_key = f"refund:{trace_id}:{client_id}:{checkout.id}"

    existing = session.exec(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idem_key)
    ).first()
    if existing:
        return

    now = datetime.now(UTC)

    revenue_reversal = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.REFUND,
        amount_minor=amount_minor,
        currency=checkout.currency,
        reference_id=checkout.id,
        account="merchant_revenue",
        counterparty_account="customer_refund",
        idempotency_key=idem_key,
        description=description,
        created_at=now,
    )
    customer_refund = LedgerEntry(
        trace_id=trace_id,
        client_id=client_id,
        entry_type=LedgerEntryType.REFUND,
        amount_minor=amount_minor,
        currency=checkout.currency,
        reference_id=checkout.id,
        account="customer_refund",
        counterparty_account="merchant_revenue",
        idempotency_key=f"{idem_key}:counterparty",
        description=description,
        created_at=now,
    )
    session.add_all([revenue_reversal, customer_refund])

    # Stage 26: RESTOCK inventory beside the money REFUND. Guarded on
    # committed-but-unrestocked units, so a repeated refund cannot restock
    # twice. (Core refunds via holdcancel.refund_checkout carry their own
    # hook; this is the PSP-issued path.)
    from openstore.core.inventory import restock_checkout_stock

    restock_checkout_stock(
        session,
        checkout.merchant_id,
        checkout.id,
        (checkout.cart_snapshot or {}).get("items", []),
        trace_id,
        client_id,
    )

    checkout.state = OrderState.REFUNDED
    checkout.cancelled_at = now
    checkout.updated_at = now
    session.add(checkout)
    session.flush()


# ---------------------------------------------------------------------------
# S5.3: Webhook endpoint + worker (INV-6)
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    raw_body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """INV-6: signature verify on the RAW body, HMAC-SHA256, hmac.compare_digest."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def process_incoming_webhook(
    session: Session,
    raw_body: bytes,
    signature: str,
    secret: str,
    x_event_id: str | None = None,
    trace_id: str = "",
    client_id: str = "webhook",
) -> dict[str, Any]:
    """
    INV-6: Webhook processing entry.
    Signature verify → persist raw event → return 200 → process.
    Dedupe on X-Razorpay-Event-Id (fallback: sha256(raw_body)).
    Allowed-transition table guards state; terminal states absorbing.
    """
    if not verify_webhook_signature(raw_body, signature, secret):
        return {"status": "rejected", "reason": "signature_mismatch"}

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return {"status": "rejected", "reason": f"invalid_json: {e}"}

    event_name = payload.get("event", "")
    if event_name not in HANDLED_WEBHOOK_EVENTS:
        return {"status": "ignored", "reason": f"unhandled_event: {event_name}"}

    event_id = x_event_id or hashlib.sha256(raw_body).hexdigest()
    return _process_persisted_webhook(session, event_id, event_name, payload, trace_id, client_id)


def _process_persisted_webhook(
    session: Session,
    event_id: str,
    event_name: str,
    payload: dict[str, Any],
    trace_id: str,
    client_id: str,
) -> dict[str, Any]:
    existing = session.exec(
        select(WebhookEvent).where(
            WebhookEvent.psp_provider == "razorpay",
            WebhookEvent.psp_event_id == event_id,
        )
    ).first()
    if existing and existing.status == WebhookStatus.COMPLETED:
        return {"status": "duplicate", "event_id": event_id, "previous_status": "COMPLETED"}

    if not existing:
        event = WebhookEvent(
            trace_id=trace_id,
            client_id=client_id,
            psp_provider="razorpay",
            psp_event_id=event_id,
            event_type=event_name,
            payload=payload,
            status=WebhookStatus.PROCESSING,
            retry_count=0,
            created_at=datetime.now(UTC),
        )
        session.add(event)
        session.flush()
    else:
        event = existing
        event.status = WebhookStatus.PROCESSING
        event.retry_count += 1
        session.add(event)
        session.flush()

    try:
        _dispatch_webhook_event(session, event_name, payload)
        event.status = WebhookStatus.COMPLETED
        event.processed_at = datetime.now(UTC)
        event.last_error = None
        session.add(event)
        session.flush()
        return {"status": "processed", "event_id": event_id, "event_name": event_name}
    except Exception as e:
        event.status = WebhookStatus.FAILED
        event.last_error = str(e)[:1024]
        session.add(event)
        session.flush()
        return {"status": "failed", "event_id": event_id, "error": str(e)}


def _dispatch_webhook_event(
    session: Session,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    if event_name in ("payment_link.paid", "payment_link.partially_paid"):
        _apply_payment_link_paid(session, event_name, payload)
    elif event_name == "payment_link.cancelled":
        _apply_payment_link_cancelled(session, payload)
    elif event_name == "payment.failed":
        _apply_payment_failed(session, payload)
    else:
        raise RazorpayError("webhook.unknown_event", f"Unhandled event {event_name}", 400)


def _apply_payment_link_paid(
    session: Session,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    link = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    reference_id = link.get("reference_id")
    if not reference_id:
        raise RazorpayError("webhook.missing_reference_id", "No reference_id in payment_link", 400)

    checkout = session.exec(select(Checkout).where(Checkout.id == reference_id)).first()
    if not checkout:
        return

    # Q-027 primary path: persist the PSP payment id (pay_...) at webhook time
    # so refund_checkout prefers the stored value. Live payment_link.paid
    # webhooks carry payload.payment.entity.id; fixtures pre-dating the live
    # capture may not — extraction is best-effort, never a hard error.
    if not checkout.psp_payment_id:
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        candidate = payment_entity.get("id")
        if not candidate:
            for p in link.get("payments", []) or []:
                if isinstance(p, dict) and p.get("id"):
                    candidate = p["id"]
                    break
        if candidate and isinstance(candidate, str):
            checkout.psp_payment_id = candidate

    current = checkout.state
    if current in (OrderState.PAID, OrderState.RELEASED, OrderState.REFUNDED):
        return  # terminal absorbing

    target = OrderState.PAID
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise RazorpayError(
            "webhook.invalid_transition",
            f"{current} -> {target} not allowed",
            400,
        )

    amount = link.get("amount", checkout.amount_minor)
    create_capture_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount,
        currency=checkout.currency,
        description=f"Razorpay {event_name}",
    )
    # Stage 26: COMMIT inventory beside the money CAPTURE. Guarded on the
    # outstanding reservation, so the webhook path and the hold-loop
    # reconcile path (both funnel here) cannot double-commit.
    from openstore.core.inventory import commit_checkout_stock

    commit_checkout_stock(
        session,
        checkout.merchant_id,
        checkout.id,
        (checkout.cart_snapshot or {}).get("items", []),
        checkout.trace_id,
        checkout.client_id,
    )
    # payment_link.paid moves the checkout to RELEASED (terminal fulfilment state).
    # PAID is the in-band payment confirmation; RELEASED is the order-final state.
    checkout.state = OrderState.RELEASED
    checkout.paid_at = datetime.now(UTC)
    checkout.released_at = datetime.now(UTC)
    checkout.updated_at = datetime.now(UTC)
    session.add(checkout)
    session.flush()


def _apply_payment_link_cancelled(session: Session, payload: dict[str, Any]) -> None:
    link = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    reference_id = link.get("reference_id")
    if not reference_id:
        return
    checkout = session.exec(select(Checkout).where(Checkout.id == reference_id)).first()
    if not checkout or checkout.state in (
        OrderState.PAID,
        OrderState.RELEASED,
        OrderState.REFUNDED,
    ):
        return

    if checkout.state == OrderState.HELD:
        create_release_entry(
            session=session,
            trace_id=checkout.trace_id,
            client_id=checkout.client_id,
            checkout_id=checkout.id,
            amount_minor=checkout.amount_minor,
            currency=checkout.currency,
            description="payment_link.cancelled",
        )
        # Stage 26: RELEASE inventory beside the money RELEASE (HELD only —
        # a CREATED checkout holds no reservation to release).
        from openstore.core.inventory import release_checkout_stock

        release_checkout_stock(
            session,
            checkout.merchant_id,
            checkout.id,
            (checkout.cart_snapshot or {}).get("items", []),
            checkout.trace_id,
            checkout.client_id,
        )

    checkout.state = OrderState.CANCELLED
    checkout.cancelled_at = datetime.now(UTC)
    checkout.updated_at = datetime.now(UTC)
    session.add(checkout)
    session.flush()


def _apply_payment_failed(session: Session, payload: dict[str, Any]) -> None:
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment.get("order_id")
    notes = payment.get("notes") or {}
    checkout_id = notes.get("checkout_id") or order_id
    if not checkout_id:
        return

    # Try psp_order_id first, then psp_payment_link_id, then id (notes.checkout_id)
    checkout = session.exec(select(Checkout).where(Checkout.psp_order_id == checkout_id)).first()
    if not checkout:
        checkout = session.exec(
            select(Checkout).where(Checkout.psp_payment_link_id == checkout_id)
        ).first()
    if not checkout:
        checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
    if not checkout or checkout.state in (
        OrderState.CANCELLED,
        OrderState.PAID,
        OrderState.RELEASED,
        OrderState.REFUNDED,
        OrderState.FAILED,
    ):
        return

    # Only a HELD checkout has an outstanding RESERVE to release. A CREATED
    # checkout has not reserved funds, so releasing it would mint an unbalanced
    # ledger entry (INV-5). Payment failed on CREATED just supersedes to CANCELLED.
    if checkout.state == OrderState.HELD:
        create_release_entry(
            session=session,
            trace_id=checkout.trace_id,
            client_id=checkout.client_id,
            checkout_id=checkout.id,
            amount_minor=checkout.amount_minor or payment.get("amount", 0),
            currency=checkout.currency,
            description="payment.failed",
        )
        # Stage 26: RELEASE inventory beside the money RELEASE (HELD only —
        # mirrors the CREATED guard above: no reservation, no release leg).
        from openstore.core.inventory import release_checkout_stock

        release_checkout_stock(
            session,
            checkout.merchant_id,
            checkout.id,
            (checkout.cart_snapshot or {}).get("items", []),
            checkout.trace_id,
            checkout.client_id,
        )
    checkout.state = OrderState.CANCELLED
    checkout.cancelled_at = datetime.now(UTC)
    checkout.updated_at = datetime.now(UTC)
    session.add(checkout)
    session.flush()


# ---------------------------------------------------------------------------
# S5.4: Reconciliation sweeper (INV-7)
# ---------------------------------------------------------------------------

# Per PRD Part 4: aged 10m–7d in non-terminal states.
SWEEPER_MIN_AGE_SECONDS = 600
SWEEPER_MAX_AGE_SECONDS = 7 * 24 * 3600


def reconciliation_sweep(
    config: Settings,
    session: Session,
    mock_razorpay: Any | None = None,
) -> dict[str, int]:
    """
    INV-7: Poll Razorpay for orders in non-terminal states aged 10m–7d.
    The PSP is the source of truth for money.
    Emits reconciliation_drift_total (sum of amount_minor that changed state).
    Non-zero → #alerts.
    """
    assert_test_mode_key(config.razorpay.key_id)

    now = datetime.now(UTC)
    cutoff_min = now.timestamp() - SWEEPER_MAX_AGE_SECONDS
    cutoff_max = now.timestamp() - SWEEPER_MIN_AGE_SECONDS

    candidates = session.exec(
        select(Checkout).where(
            Checkout.state.in_([OrderState.CREATED, OrderState.HELD]),  # type: ignore[attr-defined]
            Checkout.psp_payment_link_id.is_not(None),  # type: ignore[union-attr]
        )
    ).all()

    drift_total = 0
    processed = 0

    for checkout in candidates:
        if not checkout.created_at:
            continue
        age = (now - checkout.created_at).total_seconds()
        if not (SWEEPER_MIN_AGE_SECONDS <= age <= SWEEPER_MAX_AGE_SECONDS):
            continue
        if cutoff_min > checkout.created_at.timestamp():
            continue
        if cutoff_max < checkout.created_at.timestamp():
            continue

        try:
            link_id = checkout.psp_payment_link_id
            assert link_id is not None, "sweep requires a persisted payment_link_id"
            link = fetch_payment_link(config, link_id, mock_razorpay=mock_razorpay)
        except Exception:
            continue

        psp_status = link.get("status", "").lower()
        processed += 1

        if psp_status == "paid" and checkout.state != OrderState.PAID:
            drift_total += checkout.amount_minor
            _apply_payment_link_paid(
                session,
                "payment_link.paid",
                {"payload": {"payment_link": {"entity": link}}},
            )
        elif psp_status == "cancelled" and checkout.state not in (
            OrderState.CANCELLED,
            OrderState.REFUNDED,
        ):
            drift_total += checkout.amount_minor
            _apply_payment_link_cancelled(
                session,
                {"payload": {"payment_link": {"entity": link}}},
            )

    if drift_total > 0:
        from openstore.core.audit import audit_log

        audit_log(
            session,
            "reconciliation_sweep",
            "system",
            "reconciliation_drift",
            "checkout",
            response_status=200,
            metadata={"drift_minor": drift_total, "processed": processed},
        )

    return {
        "processed": processed,
        "drift_minor": drift_total,
    }


# ---------------------------------------------------------------------------
# S5.5: Hold/cancel live path
# ---------------------------------------------------------------------------


# Cancel token generation (PRD §3.7): 32-byte secrets.token_urlsafe, single-use,
# expires with the hold. The token IS the capability; do not "improve" with login.
def _get_client(config: Settings) -> Any:
    """The ONE place a PSP client is constructed (used by tests to mock).

    Demo mode swaps in an in-process client with the same five methods this
    driver calls (payment_link.create/fetch/all/cancel, payment.refund), so
    `openstore demo` runs the real INV-4 dual write, the real ledger entries
    and the real signed-webhook path against no external account. Nothing
    below this function knows which client it got.
    """
    if config.demo_mode:
        from openstore.psp.demo_driver import DemoClient

        return DemoClient(config)
    from razorpay import Client

    return Client(auth=(config.razorpay.key_id, config.razorpay.key_secret))


def generate_cancel_token() -> str:
    return secrets.token_urlsafe(32)


# Alias for backward compat.
PSPError = RazorpayError


# ---------------------------------------------------------------------------
# Webhook endpoint helpers
# ---------------------------------------------------------------------------


def persist_raw_webhook_event(
    session: Session,
    raw_body: bytes,
    signature: str,
    x_event_id: str | None,
    trace_id: str = "",
    client_id: str = "webhook",
) -> WebhookEvent:
    """Parse and persist a raw webhook event before processing (INV-6)."""
    payload = json.loads(raw_body.decode("utf-8"))
    event_type = payload.get("event", "")
    event_id = x_event_id or hashlib.sha256(raw_body).hexdigest()
    return _persist_event(session, event_id, event_type, payload, trace_id, client_id)


def _persist_event(
    session: Session,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str,
    client_id: str,
) -> WebhookEvent:
    existing = session.exec(
        select(WebhookEvent).where(
            WebhookEvent.psp_provider == "razorpay",
            WebhookEvent.psp_event_id == event_id,
        )
    ).first()

    if existing:
        if existing.status == WebhookStatus.COMPLETED:
            return existing
        existing.status = WebhookStatus.PROCESSING
        existing.retry_count += 1
        session.add(existing)
        session.flush()
        return existing

    event = WebhookEvent(
        trace_id=trace_id,
        client_id=client_id,
        psp_provider="razorpay",
        psp_event_id=event_id,
        event_type=event_type,
        payload=payload,
        status=WebhookStatus.PROCESSING,
        retry_count=0,
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    return event


def process_webhook_in_worker(session: Session, event: WebhookEvent) -> None:
    """Process a single persisted webhook event (INV-6 worker)."""
    if event.status == WebhookStatus.COMPLETED:
        return

    try:
        _dispatch_webhook_event(session, event.event_type, event.payload)
        event.status = WebhookStatus.COMPLETED
        event.processed_at = datetime.now(UTC)
        event.last_error = None
        session.add(event)
        session.flush()
    except Exception as e:
        event.status = WebhookStatus.FAILED
        event.last_error = str(e)[:1024]
        session.add(event)
        session.flush()


def hold_release_worker(config: Settings, session: Session) -> int:
    """INV-8 / S5.5: Call every 30s. Releases expired HELD checkouts."""
    return hold_release_worker_tick(config, session)


def run_reconciliation_sweeper(
    config: Settings,
    session: Session,
) -> dict[str, int]:
    """INV-7: reconciliation_sweep alias for psp/__init__.py."""
    return reconciliation_sweep(config, session)


# Q-032b: bounds for the pre-release PSP reconcile inside the 30s loop.
# Small by design: this is a safety net for lost webhooks, not the INV-7
# sweeper (which covers 10m–7d old rows). Failures never block the release
# path — a PSP outage must not freeze hold expiry.
PRE_RELEASE_RECONCILE_MAX_PER_TICK = 5


def reconcile_held_before_release(
    config: Settings,
    session: Session,
    *,
    warn_window_seconds: int = 120,
    mock_razorpay: Any | None = None,
) -> dict[str, int]:
    """Q-032b: poll the PSP for HELD checkouts nearing expiry and apply the
    PSP truth before the hold-release tick runs, so a paid-but-webhook-lost
    order is captured instead of released. Returns {checked, reconciled}.
    Never raises: per-checkout PSP errors are swallowed (the release path
    below still runs and the next tick retries)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    warn_cutoff = datetime.now(UTC).replace(tzinfo=None)
    try:
        from datetime import timedelta as _td

        warn_cutoff = now + _td(seconds=warn_window_seconds)
    except Exception:
        warn_cutoff = now

    candidates = session.exec(
        select(Checkout).where(
            Checkout.state == OrderState.HELD,
            Checkout.expires_at <= warn_cutoff,
            Checkout.expires_at > now,
            Checkout.psp_payment_link_id.is_not(None),  # type: ignore[union-attr]
        )
    ).all()[:PRE_RELEASE_RECONCILE_MAX_PER_TICK]

    checked = 0
    reconciled = 0
    for checkout in candidates:
        checked += 1
        try:
            link_id = checkout.psp_payment_link_id
            assert link_id is not None
            link = fetch_payment_link(config, link_id, mock_razorpay=mock_razorpay)
        except Exception:
            continue
        try:
            status = str(link.get("status", "")).lower()
            if status == "paid":
                _apply_payment_link_paid(
                    session,
                    "payment_link.paid",
                    {"payload": {"payment_link": {"entity": link}}},
                )
                reconciled += 1
            elif status in ("cancelled", "expired"):
                _apply_payment_link_cancelled(
                    session,
                    {"payload": {"payment_link": {"entity": link}}},
                )
                reconciled += 1
        except Exception:
            continue
    return {"checked": checked, "reconciled": reconciled}


def hold_release_worker_tick(config: Settings, session: Session) -> int:
    """INV-8 / S5.5: Hold release worker tick (call every 30s).
    Auto-releases expired HELD checkouts.
    """
    from openstore.core.holdcancel import check_and_expire_checkouts

    return check_and_expire_checkouts(session)


def hold_release_worker_tick_with_notifications(
    config: Settings, session: Session
) -> list[dict[str, Any]]:
    """S11 Phase 3: wraps hold_release_worker_tick to also report which
    chat-originated checkouts it just released/cancelled, so the lifespan's
    30s loop (server.py) knows who to DM. Does not change
    hold_release_worker_tick's own contract — snapshots the chat-originated
    candidates before the tick, then diffs against their post-tick state.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = session.exec(
        select(Checkout).where(
            Checkout.expires_at < now,
            Checkout.state.in_([OrderState.CREATED, OrderState.HELD]),  # type: ignore[attr-defined]
            Checkout.chat_user_id.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    candidate_ids = [c.id for c in candidates]

    hold_release_worker_tick(config, session)

    notified = []
    for checkout_id in candidate_ids:
        checkout = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()
        if checkout is not None and checkout.state in (OrderState.CANCELLED, OrderState.RELEASED):
            notified.append(
                {
                    "checkout_id": checkout.id,
                    "chat_user_id": checkout.chat_user_id,
                    "state": checkout.state.value,
                    "discord_message_id": checkout.discord_message_id,
                }
            )
    return notified
