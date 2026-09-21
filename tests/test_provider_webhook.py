"""The Provider's callback, end to end and adversarially.

`provider/webhooks.py` held the HMAC check, the dedupe and the reconcile since
A3 and nothing was mounted in front of them: the only way money ever finished
moving was the demo's own button calling `complete()` directly, which is the one
shape a real Provider never uses.

What these assert is the posture, not the plumbing: an unverified body moves
nothing, a body that *claims* an outcome decides nothing, and a retry or a late
event never captures twice or resurrects a dead order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.sidecar import checkout as flow
from openstore.sidecar.checkout import CheckoutContext
from openstore.sidecar.core.codes import OrderStatus, PaymentMethod, ReasonCode
from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
from openstore.sidecar.evidence.keys import Keyring
from openstore.sidecar.evidence.store import ReceiptStore
from openstore.sidecar.provider.fake import FakeProvider
from openstore.sidecar.provider.routes import WebhookContext, configure, deliver, router
from openstore.sidecar.provider.webhooks import WebhookRejected, WebhookVerifier
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.models import Destination, Line

DEST = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "webhook@spoiledduckie.test"}
SECRET = "webhook-test-secret"  # noqa: S105 - a test secret, never a deployment one


@pytest.fixture
async def ctx(trait: TraitClient) -> AsyncIterator[CheckoutContext]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    ring = Keyring(merchant_domain="spoiledduckie.localhost")
    ring.enroll("k1")
    provider = FakeProvider(webhook_secret=SECRET)
    context = CheckoutContext(
        trait=trait,
        provider=provider,
        keyring=ring,
        sessionmaker=make_sessionmaker(engine),
        receipts=ReceiptStore(),
        merchant_domain="spoiledduckie.localhost",
        deploy_pseudonym_key=b"webhook-test-key",
    )
    flow.configure(context)
    configure(
        WebhookContext(
            verifier=WebhookVerifier(secret=SECRET),
            provider=provider,
            signature_header="x-openstore-signature",
        )
    )
    yield context
    configure(WebhookContext())
    flow.configure(CheckoutContext())
    await engine.dispose()


async def _confirmed(ctx: CheckoutContext, cart: str = "cart_webhook") -> flow.Pending:
    """A checkout that has been tapped and is waiting on the money."""
    checkout = await flow.start(
        ctx,
        cart_id=cart,
        lines=[Line(sku="SD-TOTE-BLK-M", qty=1)],
        destination=DEST,
        contact=CONTACT,
        fulfillment_option_id="rest-of-india",
        method=PaymentMethod.UPI,
        agent_id="agent_webhook",
    )
    result = await flow.tap(ctx, checkout.tap_token)
    assert not result.refused
    return checkout


def _signed(payload: dict[str, object], *, secret: str = SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return body, hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ── Verification ─────────────────────────────────────────────────────────────


async def test_a_body_nobody_signed_moves_nothing(ctx: CheckoutContext) -> None:
    checkout = await _confirmed(ctx)
    body, _ = _signed({"event_id": "evt_1", "link_id": checkout.link_id})

    with pytest.raises(WebhookRejected, match="signature"):
        await deliver(body, "not-a-signature")
    assert checkout.status is OrderStatus.CONFIRMED


async def test_a_body_signed_with_the_wrong_secret_moves_nothing(ctx: CheckoutContext) -> None:
    checkout = await _confirmed(ctx)
    body, signature = _signed({"event_id": "evt_1", "link_id": checkout.link_id}, secret="wrong")

    with pytest.raises(WebhookRejected, match="signature"):
        await deliver(body, signature)
    assert checkout.status is OrderStatus.CONFIRMED


async def test_one_byte_of_the_body_invalidates_the_signature(ctx: CheckoutContext) -> None:
    """The HMAC is over the raw bytes, not over a re-serialization — so an
    edit the JSON parser would forgive still fails."""
    checkout = await _confirmed(ctx)
    body, signature = _signed({"event_id": "evt_1", "link_id": checkout.link_id})

    with pytest.raises(WebhookRejected):
        await deliver(body + b" ", signature)


async def test_a_webhook_with_no_secret_configured_is_refused(ctx: CheckoutContext) -> None:
    """There is no unauthenticated callback path, demo mode included."""
    configure(WebhookContext(provider=ctx.provider))
    body, signature = _signed({"event_id": "evt_1", "link_id": "link_x"})

    with pytest.raises(WebhookRejected, match="no webhook secret"):
        await deliver(body, signature)


async def test_a_verified_body_that_is_not_json_is_refused(ctx: CheckoutContext) -> None:
    raw = b"<html>this is not a webhook</html>"
    signature = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    with pytest.raises(WebhookRejected, match="not JSON"):
        await deliver(raw, signature)


async def test_a_webhook_carrying_no_event_id_is_refused(ctx: CheckoutContext) -> None:
    """Dedupe is not optional: without an id, a retry captures twice."""
    checkout = await _confirmed(ctx)
    body, signature = _signed({"link_id": checkout.link_id})

    with pytest.raises(WebhookRejected, match="event_id"):
        await deliver(body, signature)


# ── The event is a trigger, never an instruction ─────────────────────────────


async def test_a_signed_body_claiming_paid_settles_nothing_on_its_own(
    ctx: CheckoutContext,
) -> None:
    """The whole posture in one test. The body says `paid`; the Provider has
    settled nothing; the order fails rather than capturing on a claim."""
    checkout = await _confirmed(ctx)
    body, signature = _signed(
        {"event_id": "evt_lie", "link_id": checkout.link_id, "outcome": "paid"}
    )

    outcome = await deliver(body, signature)

    assert outcome.status == "not-settled"
    assert outcome.reason_code is ReasonCode.AMOUNT_MISMATCH
    assert checkout.status is OrderStatus.FAILED


async def test_the_real_thing_settles_and_seals_a_receipt(ctx: CheckoutContext) -> None:
    checkout = await _confirmed(ctx)
    ctx.provider.approve(checkout.link_id)
    body, signature = ctx.provider.webhook_for(checkout.link_id)

    outcome = await deliver(body, signature)

    assert outcome.status == OrderStatus.PAID.value
    assert outcome.acted is True
    assert outcome.receipt_id
    assert checkout.status is OrderStatus.PAID
    assert ctx.receipts.get(outcome.receipt_id) is not None


# ── Retries and ordering ─────────────────────────────────────────────────────


async def test_a_redelivered_event_captures_once(ctx: CheckoutContext) -> None:
    """Providers retry, and that is correct behaviour. The dedupe is what makes
    it free."""
    checkout = await _confirmed(ctx)
    ctx.provider.approve(checkout.link_id)
    body, signature = ctx.provider.webhook_for(checkout.link_id)

    first = await deliver(body, signature)
    second = await deliver(body, signature)

    assert first.acted is True
    assert second.status == "duplicate"
    assert second.acted is False


async def test_a_late_paid_never_resurrects_a_failed_order(ctx: CheckoutContext) -> None:
    """Providers do not promise ordering. A `paid` arriving after a `failed`
    for the same order must not bring it back."""
    checkout = await _confirmed(ctx)
    lie, lie_signature = _signed({"event_id": "evt_first", "link_id": checkout.link_id})
    await deliver(lie, lie_signature)
    assert checkout.status is OrderStatus.FAILED

    ctx.provider.approve(checkout.link_id)
    body, signature = ctx.provider.webhook_for(checkout.link_id)
    outcome = await deliver(body, signature)

    assert outcome.status == "already-failed"
    assert outcome.acted is False
    assert checkout.status is OrderStatus.FAILED


async def test_an_event_for_a_link_this_sidecar_never_issued_is_a_no_op(
    ctx: CheckoutContext,
) -> None:
    body, signature = _signed({"event_id": "evt_1", "link_id": "link_somebody_elses"})

    outcome = await deliver(body, signature)

    assert outcome.status == "unknown-link"
    assert outcome.acted is False
    assert outcome.reason_code is ReasonCode.NOT_FOUND


# ── The route itself ─────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def test_the_route_refuses_an_unsigned_post(ctx: CheckoutContext, client: TestClient) -> None:
    response = client.post("/provider/webhook", json={"event_id": "e", "link_id": "l"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == ReasonCode.SIGNATURE_INVALID.value


async def test_the_route_answers_200_on_a_verified_delivery(
    ctx: CheckoutContext, client: TestClient
) -> None:
    """200 even when the money did not settle: the Provider is being told
    'received and processed', and a non-2xx buys a retry of an event whose
    outcome cannot change."""
    checkout = await _confirmed(ctx)
    ctx.provider.approve(checkout.link_id)
    body, signature = ctx.provider.webhook_for(checkout.link_id)

    response = client.post(
        "/provider/webhook", content=body, headers={"x-openstore-signature": signature}
    )

    assert response.status_code == 200
    assert response.json()["status"] == OrderStatus.PAID.value
    assert response.json()["receipt_id"] == checkout.receipt_id
