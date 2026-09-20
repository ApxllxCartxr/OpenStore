"""Authority kinds, consumer pseudonyms, and the tokens that gate a tap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from openstore.sidecar.authority.kinds import (
    SPECS,
    HandleSource,
    derive_consumer_id,
    is_live,
    spec_for,
)
from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.core.codes import (
    AuthorityKind,
    BindingBy,
    BindingWhat,
    IntentMechanism,
    ReasonCode,
)
from openstore.sidecar.trait.errors import TraitError

KEY_A = b"deploy-key-for-shop-a"
KEY_B = b"deploy-key-for-shop-b"


# ── The closed set ───────────────────────────────────────────────────────────


def test_every_live_kind_declares_what_it_binds_and_when_it_lands() -> None:
    """§6.5's table, as data. A bundle never reads 'verified' unqualified."""
    assert spec_for(AuthorityKind.UPI_PIN).binds is BindingWhat.AMOUNT
    assert spec_for(AuthorityKind.UPI_PIN).by is BindingBy.PAYER_BANK
    assert spec_for(AuthorityKind.UPI_PIN).lands_before_gate is False

    assert spec_for(AuthorityKind.PASSKEY).binds is BindingWhat.CART
    assert spec_for(AuthorityKind.PASSKEY).by is BindingBy.PAYER_DEVICE
    assert spec_for(AuthorityKind.PASSKEY).lands_before_gate is True


def test_confirmed_intent_binds_differently_per_mechanism() -> None:
    """A ₹1 verification binds no cart; a passkey binds the whole one. Calling
    both 'confirmed' without naming the mechanism is not evidence."""
    verify = spec_for(AuthorityKind.CONFIRMED_INTENT, IntentMechanism.UPI_VERIFY)
    passkey = spec_for(AuthorityKind.CONFIRMED_INTENT, IntentMechanism.PASSKEY)
    assert verify.binds is BindingWhat.NONE
    assert verify.by is BindingBy.PAYER_BANK
    assert passkey.binds is BindingWhat.CART
    assert passkey.by is BindingBy.PAYER_DEVICE


def test_mandate_is_defined_registered_and_refused() -> None:
    """Different from absent: a reader of the registry can see it was
    considered and turned down for v1."""
    assert spec_for(AuthorityKind.MANDATE).live is False
    assert is_live(AuthorityKind.MANDATE) is False


def test_an_otp_cannot_be_configured_as_a_mechanism() -> None:
    """Refused at the enum, not at a validation step. It proves control of a
    phone number, which is exactly what RTO fraud already defeats, and it binds
    no funding instrument."""
    assert [m.value for m in IntentMechanism] == ["upi-verify", "passkey"]
    with pytest.raises(ValueError):
        IntentMechanism("otp")


def test_confirmed_intent_without_a_mechanism_has_no_spec() -> None:
    with pytest.raises(ValueError, match="needs a mechanism"):
        spec_for(AuthorityKind.CONFIRMED_INTENT)


def test_the_spec_table_covers_every_kind_in_the_enum() -> None:
    assert {s.kind for s in SPECS} == set(AuthorityKind)


# ── consumer_id ──────────────────────────────────────────────────────────────


def test_consumer_id_is_stable_across_two_orders_at_one_shop() -> None:
    a = derive_consumer_id(KEY_A, "demo@upi", source=HandleSource.PAYER_HANDLE)
    b = derive_consumer_id(KEY_A, "demo@upi", source=HandleSource.PAYER_HANDLE)
    assert a == b


def test_consumer_id_differs_under_a_second_deploy_key() -> None:
    """Per Merchant domain, never a cross-merchant identifier. A stable one
    would make every sidecar a node in a tracking network nobody asked for."""
    assert derive_consumer_id(KEY_A, "demo@upi", source=HandleSource.PAYER_HANDLE) != (
        derive_consumer_id(KEY_B, "demo@upi", source=HandleSource.PAYER_HANDLE)
    )


def test_a_cod_passkey_order_derives_from_the_credential_id() -> None:
    """The one path with no payer handle: COD authorized by passkey never
    touches a payment rail, so there is no VPA. Leaving this unstated would
    produce a null consumer_id on exactly the path where attribution matters."""
    consumer_id = derive_consumer_id(KEY_A, "cred_abc123", source=HandleSource.CREDENTIAL_ID)
    assert consumer_id.startswith("csm_")
    assert consumer_id != derive_consumer_id(
        KEY_A, "cred_abc123", source=HandleSource.PAYER_HANDLE
    ), "the source is part of the derivation, so a verifier can check which was used"


def test_a_missing_handle_fails_loud_rather_than_producing_a_null_id() -> None:
    with pytest.raises(ValueError, match="credential id"):
        derive_consumer_id(KEY_A, "", source=HandleSource.PAYER_HANDLE)


def test_an_empty_pseudonym_key_is_refused() -> None:
    with pytest.raises(ValueError, match="plaintext identifier"):
        derive_consumer_id(b"", "demo@upi", source=HandleSource.PAYER_HANDLE)


def test_the_payer_handle_does_not_appear_in_the_pseudonym() -> None:
    handle = "9000000001@okaxis"
    assert handle not in derive_consumer_id(KEY_A, handle, source=HandleSource.PAYER_HANDLE)


# ── Tap tokens ───────────────────────────────────────────────────────────────


def test_a_hold_cannot_be_created_without_spending_a_tap_token() -> None:
    store = TokenStore()
    with pytest.raises(TraitError) as exc:
        store.spend_tap("never-issued", cart_hash="abc")
    assert exc.value.code is ReasonCode.NOT_FOUND


def test_a_tap_token_is_single_use() -> None:
    store = TokenStore()
    token = store.issue_tap("ord_1", "cart-hash", 259700)
    store.spend_tap(token.token, cart_hash="cart-hash")
    with pytest.raises(TraitError, match="already been used"):
        store.spend_tap(token.token, cart_hash="cart-hash")


def test_a_tap_token_expires_in_five_minutes() -> None:
    store = TokenStore()
    now = datetime.now(UTC)
    token = store.issue_tap("ord_2", "cart-hash", 1000, now=now)
    assert token.expires_at - token.issued_at == timedelta(minutes=5)
    with pytest.raises(TraitError, match="expired"):
        store.spend_tap(
            token.token, cart_hash="cart-hash", now=now + timedelta(minutes=5, seconds=1)
        )


def test_a_tap_token_belongs_to_one_basket() -> None:
    store = TokenStore()
    token = store.issue_tap("ord_3", "cart-hash-a", 1000)
    with pytest.raises(TraitError, match="different basket"):
        store.spend_tap(token.token, cart_hash="cart-hash-b")


def test_a_destination_edit_after_render_invalidates_the_token() -> None:
    """Even though the total did not move. Anything shown on the approve page is
    covered on display, so an edit after render cannot redirect a parcel someone
    already paid for."""
    store = TokenStore()
    token = store.issue_tap(
        "ord_4", "cart-hash", 259700, rendered_digest="digest-of-what-was-shown"
    )
    with pytest.raises(TraitError, match="something changed"):
        store.spend_tap(token.token, cart_hash="cart-hash", rendered_digest="digest-after-the-edit")


# ── Resume tokens ────────────────────────────────────────────────────────────


def test_a_resume_token_is_session_bound() -> None:
    """Holding the link is not enough. Bare order_id + chat_thread_id in a URL
    would hand anyone with the link someone else's checkout — the same IDOR the
    storefront's order lookup was fixed for."""
    store = TokenStore()
    token = store.issue_resume("ord_5", "thread_1", session_id="session_a")
    with pytest.raises(TraitError) as exc:
        store.redeem_resume(token.token, session_id="session_b")
    assert exc.value.code is ReasonCode.NOT_FOUND
    assert "not valid" in exc.value.detail, "a stranger is not told the link is real"


def test_a_resume_token_resolves_both_ids_server_side() -> None:
    store = TokenStore()
    token = store.issue_resume("ord_6", "thread_2", session_id="s")
    record = store.redeem_resume(token.token, session_id="s")
    assert (record.order_id, record.chat_thread_id) == ("ord_6", "thread_2")
    assert "ord_6" not in token.token, "the token is opaque, not a container"


def test_a_resume_token_is_single_use() -> None:
    store = TokenStore()
    token = store.issue_resume("ord_7", "t", session_id="s")
    store.redeem_resume(token.token, session_id="s")
    with pytest.raises(TraitError):
        store.redeem_resume(token.token, session_id="s")


def test_tokens_are_unguessable() -> None:
    store = TokenStore()
    issued = {store.issue_tap(f"ord_{n}", "c", 1).token for n in range(200)}
    assert len(issued) == 200
    assert all(len(t) >= 20 for t in issued)
