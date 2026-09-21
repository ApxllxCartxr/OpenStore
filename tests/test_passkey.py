"""The `passkey` Authority, against a software authenticator that really signs.

`AuthorityKind.PASSKEY` was in the closed set from hour 0 with no module behind
it, so the strongest claim the system can make — cart bound by the payer's own
device — was enum-level only.

What these assert is the binding, not the plumbing: the challenge *is* the cart
commitment, so a signature over a different basket is a signature over a
different challenge and does not verify. Nothing here checks a server-side
association, because there is none to check.
"""

from __future__ import annotations

import pytest
from openstore.sidecar.authority.passkey import (
    PasskeyRefused,
    PasskeyRP,
    challenge_for,
)
from openstore.sidecar.core.codes import Ceremony, ReasonCode
from webauthn.helpers import base64url_to_bytes

from tests.webauthn_authenticator import SoftwareAuthenticator

RP_ID = "spoiledduckie.localhost"
ORIGIN = "http://spoiledduckie.localhost"
CART = "a" * 64
OTHER_CART = "b" * 64
TOTAL = 259700
EXPIRY = "2026-09-22T12:00:00Z"


@pytest.fixture
def rp(sessionmaker) -> PasskeyRP:  # type: ignore[no-untyped-def]
    return PasskeyRP(rp_id=RP_ID, origin=ORIGIN, rp_name="SpoiledDuckie", sessionmaker=sessionmaker)


@pytest.fixture
def device() -> SoftwareAuthenticator:
    return SoftwareAuthenticator(rp_id=RP_ID, origin=ORIGIN)


async def _begin(rp: PasskeyRP, token: str = "tok", cart: str = CART, **over: object):
    stage, options = await rp.begin(
        token=token,
        cart_hash=cart,
        total_minor=int(over.get("total_minor", TOTAL)),
        currency="INR",
        expiry_utc=str(over.get("expiry_utc", EXPIRY)),
        credential_ids=over.get("credential_ids"),  # type: ignore[arg-type]
    )
    return stage, options, base64url_to_bytes(options["challenge"])


# ── The challenge is the binding ─────────────────────────────────────────────


def test_the_challenge_changes_with_every_thing_it_binds() -> None:
    """Each of the five, moved on its own. A challenge that ignored one would
    let that one change after the human agreed to it."""
    base = dict(
        cart_hash=CART,
        total_minor=TOTAL,
        currency="INR",
        merchant_domain=RP_ID,
        expiry_utc=EXPIRY,
    )
    baseline = challenge_for(**base)  # type: ignore[arg-type]

    for field, moved in (
        ("cart_hash", OTHER_CART),
        ("total_minor", TOTAL + 1),
        ("currency", "USD"),
        ("merchant_domain", "someone-else.example"),
        ("expiry_utc", "2026-09-22T12:00:01Z"),
    ):
        assert challenge_for(**{**base, field: moved}) != baseline, field  # type: ignore[arg-type]


def test_the_same_binding_is_the_same_challenge() -> None:
    args = dict(
        cart_hash=CART,
        total_minor=TOTAL,
        currency="INR",
        merchant_domain=RP_ID,
        expiry_utc=EXPIRY,
    )
    assert challenge_for(**args) == challenge_for(**args)  # type: ignore[arg-type]


# ── One prompt, when the authenticator attests ───────────────────────────────


async def test_an_attested_enrollment_authorizes_in_one_prompt(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    stage, _, challenge = await _begin(rp)
    assert stage == "enrollment"

    verified = await rp.verify_enrollment(
        token="tok",
        credential=device.create(challenge, attestation="packed"),
        cart_hash=CART,
        total_minor=TOTAL,
    )

    assert verified is not None
    assert verified.ceremony is Ceremony.ENROLLMENT
    assert verified.prompts == 1
    assert verified.user_verified is True
    assert verified.attestation_fmt == "packed"


# ── Two prompts, named, when it does not ─────────────────────────────────────


async def test_an_unattested_enrollment_proves_nothing_on_its_own(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    """Under `fmt: none` the response signs nothing verifiable as agreement to a
    basket. Returning a verified Authority here is exactly the lie the fallback
    exists to avoid."""
    _, _, challenge = await _begin(rp)

    verified = await rp.verify_enrollment(
        token="tok",
        credential=device.create(challenge, attestation="none"),
        cart_hash=CART,
        total_minor=TOTAL,
    )

    assert verified is None
    # The credential is usable; the agreement is not yet made.
    assert len(await rp.credentials_held()) == 1


async def test_the_fallback_assertion_runs_over_the_same_challenge(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    """A second prompt over a *fresh* challenge would be two ceremonies over two
    bindings, which proves nothing about the basket the first one saw."""
    _, _, challenge = await _begin(rp)
    created = device.create(challenge, attestation="none")
    assert (
        await rp.verify_enrollment(
            token="tok", credential=created, cart_hash=CART, total_minor=TOTAL
        )
        is None
    )

    options = await rp.options_for("tok", CART)
    assert base64url_to_bytes(options["challenge"]) == challenge

    verified = await rp.verify_assertion(
        token="tok",
        credential=device.get(challenge, str(created["id"])),
        cart_hash=CART,
        total_minor=TOTAL,
        prompts=2,
    )

    assert verified.ceremony is Ceremony.ASSERTION
    assert verified.prompts == 2
    assert verified.cart_hash == CART


# ── What must not verify ─────────────────────────────────────────────────────


async def test_a_signature_over_a_different_basket_does_not_verify(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    """The whole point, in one test. The device signs a challenge built from
    another cart; there is no lookup to fool, and it simply fails."""
    _, _, challenge = await _begin(rp)
    created = device.create(challenge, attestation="none")
    await rp.verify_enrollment(token="tok", credential=created, cart_hash=CART, total_minor=TOTAL)

    other = challenge_for(
        cart_hash=OTHER_CART,
        total_minor=TOTAL,
        currency="INR",
        merchant_domain=RP_ID,
        expiry_utc=EXPIRY,
    )
    with pytest.raises(PasskeyRefused) as refusal:
        await rp.verify_assertion(
            token="tok",
            credential=device.get(other, str(created["id"])),
            cart_hash=CART,
            total_minor=TOTAL,
        )
    assert refusal.value.code is ReasonCode.AUTHORITY_STALE


async def test_an_amount_changed_after_the_page_rendered_does_not_verify(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    _, _, challenge = await _begin(rp)
    created = device.create(challenge, attestation="none")
    await rp.verify_enrollment(token="tok", credential=created, cart_hash=CART, total_minor=TOTAL)

    moved = challenge_for(
        cart_hash=CART,
        total_minor=TOTAL + 100,
        currency="INR",
        merchant_domain=RP_ID,
        expiry_utc=EXPIRY,
    )
    with pytest.raises(PasskeyRefused):
        await rp.verify_assertion(
            token="tok",
            credential=device.get(moved, str(created["id"])),
            cart_hash=CART,
            total_minor=TOTAL,
        )


async def test_presence_without_user_verification_is_refused(rp: PasskeyRP) -> None:
    """`preferred` degrades silently on authenticators that feel like it, and
    'somebody touched a key' is not the permission a spend rests on."""
    device = SoftwareAuthenticator(rp_id=RP_ID, origin=ORIGIN, user_verified=False)
    _, _, challenge = await _begin(rp)

    with pytest.raises(PasskeyRefused) as refusal:
        await rp.verify_enrollment(
            token="tok",
            credential=device.create(challenge, attestation="packed"),
            cart_hash=CART,
            total_minor=TOTAL,
        )
    assert refusal.value.code is ReasonCode.AUTHORITY_MISSING


async def test_a_ceremony_from_another_origin_is_refused(rp: PasskeyRP) -> None:
    """The RP ID decides which passkeys exist. A response minted against another
    origin is another shop's ceremony."""
    elsewhere = SoftwareAuthenticator(rp_id=RP_ID, origin="http://evil.example")
    _, _, challenge = await _begin(rp)

    with pytest.raises(PasskeyRefused):
        await rp.verify_enrollment(
            token="tok",
            credential=elsewhere.create(challenge, attestation="packed"),
            cart_hash=CART,
            total_minor=TOTAL,
        )


async def test_a_challenge_is_spent_once(rp: PasskeyRP, device: SoftwareAuthenticator) -> None:
    """A challenge that could be answered twice is a replay of a human's
    agreement."""
    _, _, challenge = await _begin(rp)
    created = device.create(challenge, attestation="none")
    await rp.verify_enrollment(token="tok", credential=created, cart_hash=CART, total_minor=TOTAL)
    assertion = device.get(challenge, str(created["id"]))
    await rp.verify_assertion(token="tok", credential=assertion, cart_hash=CART, total_minor=TOTAL)

    with pytest.raises(PasskeyRefused) as refusal:
        await rp.verify_assertion(
            token="tok", credential=assertion, cart_hash=CART, total_minor=TOTAL
        )
    assert refusal.value.code is ReasonCode.AUTHORITY_STALE


async def test_the_stored_sign_count_advances_after_an_assertion(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    """The stored counter is what makes a cloned authenticator detectable —
    a clone replaying an old signature presents a counter that does not
    advance from what was last seen. Never checking it move is indistinguishable
    from it staying pinned at its zero default forever."""
    _, _, challenge = await _begin(rp, token="tok1")
    created = device.create(challenge, attestation="none")
    raw_id = base64url_to_bytes(str(created["id"]))
    await rp.verify_enrollment(
        token="tok1", credential=created, cart_hash=CART, total_minor=TOTAL
    )
    after_enrollment = (await rp.credential(raw_id)).sign_count

    _, _, challenge2 = await _begin(rp, token="tok2")
    await rp.verify_assertion(
        token="tok2",
        credential=device.get(challenge2, str(created["id"])),
        cart_hash=CART,
        total_minor=TOTAL,
    )
    assert (await rp.credential(raw_id)).sign_count > after_enrollment


async def test_an_assertion_without_user_verification_is_refused(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """`require_user_verification=True` is what turns "this key signed it"
    into "a human present and verified authorized it" — the whole reason
    this Authority kind outranks a bare signature."""
    device = SoftwareAuthenticator(rp_id=RP_ID, origin=ORIGIN, user_verified=True)
    rp = PasskeyRP(rp_id=RP_ID, origin=ORIGIN, rp_name="SpoiledDuckie", sessionmaker=sessionmaker)
    _, _, challenge = await _begin(rp, token="tok1")
    created = device.create(challenge, attestation="none")
    await rp.verify_enrollment(
        token="tok1", credential=created, cart_hash=CART, total_minor=TOTAL
    )

    device.user_verified = False
    _, _, challenge2 = await _begin(rp, token="tok2")
    with pytest.raises(PasskeyRefused) as refusal:
        await rp.verify_assertion(
            token="tok2",
            credential=device.get(challenge2, str(created["id"])),
            cart_hash=CART,
            total_minor=TOTAL,
        )
    assert refusal.value.code is ReasonCode.AUTHORITY_STALE


async def test_a_credential_this_shop_never_enrolled_is_refused(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    _, _, challenge = await _begin(rp)
    stranger = SoftwareAuthenticator(rp_id=RP_ID, origin=ORIGIN)
    created = stranger.create(challenge, attestation="none")  # never enrolled here

    with pytest.raises(PasskeyRefused) as refusal:
        await rp.verify_assertion(
            token="tok",
            credential=stranger.get(challenge, str(created["id"])),
            cart_hash=CART,
            total_minor=TOTAL,
        )
    assert refusal.value.code is ReasonCode.AUTHORITY_MISSING


# ── A second visit skips enrollment ──────────────────────────────────────────


async def test_a_returning_device_is_asked_to_assert_not_to_enroll(
    rp: PasskeyRP, device: SoftwareAuthenticator
) -> None:
    """Roaming five shops costs five taps, not five signups — and the sixth visit
    to one of them costs one."""
    _, _, first = await _begin(rp)
    created = device.create(first, attestation="none")
    await rp.verify_enrollment(token="tok", credential=created, cart_hash=CART, total_minor=TOTAL)

    stage, options, _ = await _begin(rp, token="tok2", credential_ids=[str(created["id"])])

    assert stage == "assertion"
    assert options["allowCredentials"][0]["id"] == created["id"]


# ── Through the money path ───────────────────────────────────────────────────


@pytest.fixture
async def ctx(trait):  # type: ignore[no-untyped-def]
    """A live checkout context with a passkey RP, as `lifespan` builds one."""
    from collections.abc import AsyncIterator  # noqa: F401

    from openstore.sidecar import checkout as flow
    from openstore.sidecar.authority.tokens import TokenStore
    from openstore.sidecar.checkout_store import CheckoutStore
    from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
    from openstore.sidecar.evidence.keys import Keyring
    from openstore.sidecar.evidence.store import ReceiptStore
    from openstore.sidecar.provider.fake import FakeProvider

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    maker = make_sessionmaker(engine)
    ring = Keyring(merchant_domain=RP_ID)
    ring.enroll("k1")
    context = flow.CheckoutContext(
        trait=trait,
        tokens=TokenStore(sessionmaker=maker),
        provider=FakeProvider(),
        keyring=ring,
        sessionmaker=maker,
        receipts=ReceiptStore(sessionmaker=maker),
        merchant_domain=RP_ID,
        deploy_pseudonym_key=b"passkey-test-key",
        store=CheckoutStore(sessionmaker=maker),
        passkey_rp=PasskeyRP(rp_id=RP_ID, origin=ORIGIN, sessionmaker=maker),
    )
    flow.configure(context)
    yield context
    flow.configure(flow.CheckoutContext())
    await engine.dispose()


async def _started(ctx, method):  # type: ignore[no-untyped-def]
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.trait.models import Destination, Line

    return await flow.start(
        ctx,
        cart_id=f"cart_passkey_{method.value}",
        lines=[Line(sku="SD-TOTE-BLK-M", qty=1)],
        destination=Destination(
            line1="Dadar West", city="Mumbai", state="MH", postal_code="400028"
        ),
        contact={"email": "passkey@spoiledduckie.test"},
        fulfillment_option_id="rest-of-india",
        method=method,
        agent_id="agent_passkey",
    )


async def _ceremony(ctx, checkout, device: SoftwareAuthenticator) -> None:  # type: ignore[no-untyped-def]
    """Run the ceremony the way the page does, ending with it held against the
    tap token."""
    _, options = await ctx.passkey_rp.begin(
        token=checkout.tap_token,
        cart_hash=checkout.cart_hash,
        total_minor=checkout.total_minor,
        currency=checkout.quote.currency,
        expiry_utc=checkout.expiry_utc,
    )
    challenge = base64url_to_bytes(options["challenge"])
    verified = await ctx.passkey_rp.verify_enrollment(
        token=checkout.tap_token,
        credential=device.create(challenge, attestation="packed"),
        cart_hash=checkout.cart_hash,
        total_minor=checkout.total_minor,
    )
    assert verified is not None
    await ctx.passkey_rp.remember(checkout.tap_token, verified)


async def _remembered(ctx, token: str):  # type: ignore[no-untyped-def]
    """What the RP is holding for this tap, without consuming it.

    `take` is single-use by design, so a test that used it to look would change
    the thing it is asserting about.
    """
    from openstore.sidecar.authority.passkey import passkey_ceremonies
    from openstore.sidecar.core.db import session_scope
    from sqlalchemy import select

    async with session_scope(ctx.passkey_rp.sessionmaker) as session:
        row = (
            await session.execute(
                select(passkey_ceremonies.c.verified).where(passkey_ceremonies.c.tap_token == token)
            )
        ).first()
    return None if row is None else row.verified


async def test_a_prepaid_tap_carrying_a_passkey_binds_the_cart(
    ctx, device: SoftwareAuthenticator
) -> None:  # type: ignore[no-untyped-def]
    """`upi-pin` would have bound the amount only, and deferred. A passkey binds
    the basket, to this device, before the Gate runs."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.core.codes import AuthorityKind, CheckResult, GateCheck, PaymentMethod

    checkout = await _started(ctx, PaymentMethod.UPI)
    await _ceremony(ctx, checkout, device)

    result = await flow.tap(ctx, checkout.tap_token)

    assert not result.refused
    transcript = result.decision.transcript
    assert transcript.authority_kind is AuthorityKind.PASSKEY
    assert transcript.ceremony == Ceremony.ENROLLMENT.value
    # Nothing is deferred: the Authority landed before the Gate.
    check = next(
        c for c in transcript.checks if c.check is GateCheck.AUTHORITY_PRESENT_AND_ACCEPTED
    )
    assert check.result is CheckResult.PASS


async def test_a_cod_passkey_order_derives_its_pseudonym_from_the_credential(
    ctx, device: SoftwareAuthenticator
) -> None:  # type: ignore[no-untyped-def]
    """A COD order authorized by passkey never touches a payment rail, so there
    is no VPA to derive from. Left unstated this produces a null `consumer_id`
    on exactly the path where attribution matters most."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.authority.kinds import HandleSource
    from openstore.sidecar.core.codes import AuthorityKind, IntentMechanism, PaymentMethod

    checkout = await _started(ctx, PaymentMethod.CASH_ON_DELIVERY)
    await _ceremony(ctx, checkout, device)

    result = await flow.tap(ctx, checkout.tap_token)

    assert not result.refused
    transcript = result.decision.transcript
    assert transcript.authority_kind is AuthorityKind.CONFIRMED_INTENT
    assert transcript.authority_mechanism == IntentMechanism.PASSKEY.value
    # `result.checkout` and not the object `start` returned: a checkout is a
    # row now, and the tap reads its own copy.
    assert result.checkout.handle_source is HandleSource.CREDENTIAL_ID
    assert transcript.consumer_id.startswith("csm_")


async def test_a_ceremony_is_consumed_by_the_tap_it_was_taken_for(
    ctx, device: SoftwareAuthenticator
) -> None:  # type: ignore[no-untyped-def]
    """Two taps of one order are two agreements, and one may not stand in for
    the other."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.core.codes import PaymentMethod

    checkout = await _started(ctx, PaymentMethod.UPI)
    await _ceremony(ctx, checkout, device)
    await flow.tap(ctx, checkout.tap_token)

    assert await ctx.passkey_rp.take(checkout.tap_token) is None


async def test_the_receipt_names_the_passkey_binding_verbatim(
    ctx, device: SoftwareAuthenticator
) -> None:  # type: ignore[no-untyped-def]
    """A receipt that named a stronger binding than the ceremony produced would
    be a false claim in signed evidence — so this checks the weaker fact too:
    it says `cart` / `payer-device`, which is what a passkey actually gives."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.core.codes import AuthorityKind, PaymentMethod

    checkout = await _started(ctx, PaymentMethod.UPI)
    await _ceremony(ctx, checkout, device)
    result = await flow.tap(ctx, checkout.tap_token)
    ctx.provider.approve(result.checkout.link_id)
    settled = await flow.complete(ctx, result.checkout.link_id, payer_handle="demo@upi")

    bundle = await ctx.receipts.get(settled.receipt_id)
    tapped = next(s for s in bundle.sections if s.name == "tapped")
    assert tapped.payload["authority_kind"] == AuthorityKind.PASSKEY.value
    assert tapped.payload["binding"] == {"what": "cart", "by": "payer-device"}
    assert tapped.payload["ceremony"] == Ceremony.ENROLLMENT.value


# ── Over HTTP, the way the page drives it ────────────────────────────────────


@pytest.fixture
def approve_client(ctx):  # type: ignore[no-untyped-def]
    """A client built after the context, then re-pointed at it: the app's
    lifespan configures a context of its own."""
    from fastapi.testclient import TestClient
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.app import app
    from openstore.sidecar.console.approve import ApproveContext
    from openstore.sidecar.console.approve import configure as configure_approve

    with TestClient(app) as client:
        flow.configure(ctx)
        configure_approve(
            ApproveContext(tokens=ctx.tokens, merchant_domain=RP_ID, passkey_enabled=True)
        )
        yield client
    configure_approve(ApproveContext())


async def test_the_two_prompt_fallback_runs_over_http(
    ctx, approve_client, device: SoftwareAuthenticator
) -> None:  # type: ignore[no-untyped-def]
    """The whole ceremony as the browser runs it, on the unattested path: begin,
    create, and a second prompt the *server* asks for over the same challenge."""
    from openstore.sidecar.core.codes import PaymentMethod

    checkout = await _started(ctx, PaymentMethod.UPI)

    started = approve_client.post(
        "/agentic/approve/passkey/begin", json={"t": checkout.tap_token}
    ).json()
    assert started["stage"] == "enrollment"
    challenge = base64url_to_bytes(started["options"]["challenge"])
    assert challenge == challenge_for(
        cart_hash=checkout.cart_hash,
        total_minor=checkout.total_minor,
        currency="INR",
        merchant_domain=RP_ID,
        expiry_utc=checkout.expiry_utc,
    )

    created = device.create(challenge, attestation="none")
    first = approve_client.post(
        "/agentic/approve/passkey/finish",
        json={"t": checkout.tap_token, "stage": "enrollment", "credential": created},
    ).json()
    # Nothing was recorded: the response proved possession of a key, not
    # agreement to a basket.
    assert first["next"] == "assertion-fallback"
    assert base64url_to_bytes(first["options"]["challenge"]) == challenge
    assert await _remembered(ctx, checkout.tap_token) is None

    done = approve_client.post(
        "/agentic/approve/passkey/finish",
        json={
            "t": checkout.tap_token,
            "stage": "assertion-fallback",
            "credential": device.get(challenge, str(created["id"])),
        },
    ).json()

    assert done["next"] is None
    assert done["ceremony"] == Ceremony.ASSERTION.value
    assert done["prompts"] == 2
    assert await _remembered(ctx, checkout.tap_token) is not None


async def test_a_ceremony_on_a_dead_token_is_refused(ctx, approve_client) -> None:  # type: ignore[no-untyped-def]
    """A ceremony against a spent token spends a human's attention on an
    agreement that can never be used."""
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.core.codes import PaymentMethod, ReasonCode

    checkout = await _started(ctx, PaymentMethod.UPI)
    await flow.tap(ctx, checkout.tap_token)  # spends it

    response = approve_client.post("/agentic/approve/passkey/begin", json={"t": checkout.tap_token})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ReasonCode.AUTHORITY_STALE.value


def test_the_ceremony_routes_are_public_inside_the_authenticated_prefix() -> None:
    """Behind the Merchant session the ceremony could never run: a Consumer
    approving a spend is not the Merchant."""
    from openstore.sidecar.console.routes import PUBLIC_AGENTIC_PATHS

    assert "/agentic/approve/passkey/begin" in PUBLIC_AGENTIC_PATHS
    assert "/agentic/approve/passkey/finish" in PUBLIC_AGENTIC_PATHS


def test_the_page_offers_no_passkey_button_when_the_shop_runs_no_ceremony() -> None:
    """Offering a ceremony that cannot complete teaches the Consumer that the
    page lies."""
    from openstore.sidecar.console.approve import render_approve
    from openstore.sidecar.core.codes import PaymentMethod

    args = dict(
        merchant_name="SpoiledDuckie",
        quote={"total_minor": 1000, "lines": [], "tax_lines": []},
        token="tok",
        expires_in_seconds=300,
        enabled_methods=frozenset({PaymentMethod.UPI}),
        demo=True,
    )
    assert "passkey-btn" not in render_approve(**args)  # type: ignore[arg-type]
    assert "passkey-btn" in render_approve(**args, passkey_enabled=True)  # type: ignore[arg-type]
