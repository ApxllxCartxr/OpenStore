"""The sealed receipt and its offline verifier."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from openstore.sidecar.core.canonical import pii_commit
from openstore.sidecar.core.codes import AuthorityKind, LedgerKind, PaymentMethod
from openstore.sidecar.evidence.bundle import (
    Bundle,
    BundleBuilder,
    append_moved_entry,
    attestation_digest,
    format_rupees,
    ledger_entry_payload,
    refund_summary,
)
from openstore.sidecar.evidence.keys import Keyring, sign
from openstore.sidecar.verify.checks import (
    ExitCode,
    bundle_from_dict,
    bundle_to_bytes,
    verify,
)

SALT = bytes.fromhex("00112233445566778899aabbccddeeff")
DESTINATION = {"line1": "Dadar West", "city": "Mumbai", "state": "MH", "postal_code": "400028"}
CONTACT = {"email": "demo@spoiledduckie.test", "phone": "+919000000001"}

QUOTE = {
    "currency": "INR",
    "subtotal_minor": 249800,
    "total_minor": 259700,
    "tax_lines": [{"kind": "IGST", "amount_minor": 15827}],
}


@pytest.fixture
def keyring() -> Keyring:
    ring = Keyring("spoiledduckie.localhost")
    ring.enroll("k1")
    return ring


def _seal(
    keyring: Keyring,
    *,
    method: PaymentMethod = PaymentMethod.UPI,
    kind: AuthorityKind = AuthorityKind.UPI_PIN,
    mechanism: str | None = None,
    entries: list[dict[str, Any]] | None = None,
    kid: str = "k1",
) -> Bundle:
    builder = BundleBuilder("rcpt_0011223344556677", "spoiledduckie.localhost")
    builder.bought(
        quote=QUOTE,
        lines=[{"sku": "SD-TOTE-BLK-M", "qty": 1, "price_minor": 89900}],
        attestation_digests={
            "SD-TOTE-BLK-M": attestation_digest(
                "SD-TOTE-BLK-M", 89900, [], {"colour": "black", "size": "M"}
            )
        },
        destination_hash=pii_commit(SALT, DESTINATION),
        contact_hash=pii_commit(SALT, CONTACT),
    )
    builder.tapped(
        authority_kind=kind,
        mechanism=mechanism,
        binding_what="amount" if kind is AuthorityKind.UPI_PIN else "cart",
        binding_by="payer-bank" if kind is AuthorityKind.UPI_PIN else "payer-device",
        ceremony=None,
        cart_hash="cart-hash-abc",
    )
    builder.decided(transcript={"reason_code": None, "checks": []})
    builder.told(notifications=[{"kind": "order-confirmation", "at": "2026-09-20T12:00:00Z"}])
    builder.moved(
        entries=entries
        if entries is not None
        else [ledger_entry_payload(LedgerKind.CAPTURE, 259700, "INR", "2026-09-20T12:01:00Z")],
        method=method,
    )
    bundle = builder.seal(jwks_snapshot=keyring.jwks())
    bundle.signing_kid = kid
    bundle.signature = sign(keyring.keys[kid], bundle.signing_payload())
    return bundle


# ── Structure ────────────────────────────────────────────────────────────────


def test_five_sections_in_order(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    assert [s.name for s in bundle.sections] == [
        "bought",
        "tapped",
        "decided",
        "told",
        "moved",
    ]


def test_sections_cannot_be_sealed_out_of_order() -> None:
    builder = BundleBuilder("r", "d")
    with pytest.raises(ValueError, match="expected 'bought'"):
        builder.decided(transcript={})


def test_a_bundle_cannot_be_sealed_incomplete() -> None:
    builder = BundleBuilder("r", "d")
    builder.bought(
        quote={}, lines=[], attestation_digests={}, destination_hash="x", contact_hash="y"
    )
    with pytest.raises(ValueError, match="cannot seal without"):
        builder.seal(jwks_snapshot={})


# ── Verification ─────────────────────────────────────────────────────────────


def test_a_clean_bundle_verifies(keyring: Keyring) -> None:
    result = verify(_seal(keyring))
    assert result.exit_code is ExitCode.VALID
    assert result.ok


def test_a_one_digit_tamper_fails_naming_the_link(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    bought = bundle.section("bought")
    bought.payload["quote"]["total_minor"] = 259701

    result = verify(bundle)
    assert result.exit_code is ExitCode.TAMPERED
    failing = next(f for f in result.findings if not f.ok)
    assert "bought" in failing.label, "the verifier names the exact link that broke"


def test_a_tampered_moved_section_is_caught(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    bundle.section("moved").payload["entries"][0]["amount_minor"] = 1
    result = verify(bundle)
    assert result.exit_code is ExitCode.TAMPERED
    assert "moved" in next(f for f in result.findings if not f.ok).label


def test_a_swapped_signature_is_caught(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    bundle.signature = sign(keyring.keys["k1"], b"some other document")
    result = verify(bundle)
    assert result.exit_code is ExitCode.TAMPERED


def test_the_jwks_snapshot_is_inside_the_signature(keyring: Keyring) -> None:
    """A bundle whose key list could be swapped after signing would verify
    against whatever key an attacker supplied."""
    bundle = _seal(keyring)
    other = Keyring("evil")
    other.enroll("k1")
    bundle.jwks_snapshot = other.jwks()
    assert verify(bundle).exit_code is ExitCode.TAMPERED


def test_verification_is_offline(keyring: Keyring) -> None:
    """The bundle carries its own JWKS, so nothing is fetched. Round-tripping
    through JSON proves nothing else is reached for."""
    document = json.loads(bundle_to_bytes(_seal(keyring)))
    assert verify(bundle_from_dict(document)).exit_code is ExitCode.VALID


# ── Key revocation ───────────────────────────────────────────────────────────


def test_a_bundle_signed_before_revocation_stays_valid(keyring: Keyring) -> None:
    """Revocation invalidates the future, not the past. Otherwise rotating after
    an incident would destroy the Merchant's own evidence."""
    bundle = _seal(keyring)
    keyring.revoke("k1", at=datetime.now(UTC) + timedelta(days=1))
    bundle.jwks_snapshot = keyring.jwks()
    bundle.signature = sign(keyring.keys["k1"], bundle.signing_payload())

    result = verify(bundle)
    assert result.exit_code is ExitCode.VALID
    assert any("revoked later" in f.detail for f in result.findings)


def test_a_bundle_signed_after_revocation_fails_untrusted_key(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    keyring.revoke("k1", at=datetime.now(UTC) - timedelta(days=1))
    bundle.jwks_snapshot = keyring.jwks()
    bundle.signature = sign(keyring.keys["k1"], bundle.signing_payload())

    result = verify(bundle)
    assert result.exit_code is ExitCode.UNTRUSTED_KEY


def test_a_kid_missing_from_the_snapshot_is_untrusted(keyring: Keyring) -> None:
    bundle = _seal(keyring)
    bundle.signing_kid = "k-nope"
    assert verify(bundle).exit_code is ExitCode.UNTRUSTED_KEY


def test_rotation_is_additive_and_history_stays_verifiable(keyring: Keyring) -> None:
    old = _seal(keyring)
    keyring.rotate("k2")
    new = _seal(keyring, kid="k2")

    assert verify(old).exit_code is ExitCode.VALID
    assert verify(new).exit_code is ExitCode.VALID
    assert {k["kid"] for k in keyring.jwks()["keys"]} == {"k1", "k2"}  # type: ignore[index,union-attr]


def test_enrolling_the_same_kid_twice_is_refused(keyring: Keyring) -> None:
    with pytest.raises(ValueError, match="additive"):
        keyring.enroll("k1")


# ── PII: unopened, erased, tampered ──────────────────────────────────────────


def test_pii_sections_report_unopened_without_the_salt(keyring: Keyring) -> None:
    """Offline verify covers the chain, the signatures and every non-PII fact,
    and reports these two as `unopened` rather than failing (ADR-0011)."""
    result = verify(_seal(keyring))
    assert result.exit_code is ExitCode.VALID
    assert sorted(result.unopened) == ["contact", "destination"]


def test_an_erased_order_still_verifies(keyring: Keyring) -> None:
    """Erasure takes the salt with the row, so the commitments become
    permanently unopenable — and the receipt still verifies, because erasure
    must never break verification."""
    bundle = _seal(keyring)
    result = verify(bundle, order_salt=None, destination=None, contact=None)
    assert result.exit_code is ExitCode.VALID
    assert "destination" in result.unopened


def test_an_intact_row_opens_its_commitment(keyring: Keyring) -> None:
    result = verify(_seal(keyring), order_salt=SALT, destination=DESTINATION, contact=CONTACT)
    assert result.exit_code is ExitCode.VALID
    assert result.unopened == []
    assert any("destination matches" in f.label for f in result.findings)


def test_an_altered_row_renders_tampered(keyring: Keyring) -> None:
    """The distinction that matters: erased verifies, altered does not."""
    altered = dict(DESTINATION, line1="Somewhere else entirely")
    result = verify(_seal(keyring), order_salt=SALT, destination=altered, contact=CONTACT)
    assert result.exit_code is ExitCode.TAMPERED
    assert any("altered after the receipt was sealed" in f.detail for f in result.findings)


# ── The claims, printed rather than summarised ───────────────────────────────


def test_a_upi_pin_receipt_does_not_claim_the_buyer_signed_the_basket(
    keyring: Keyring,
) -> None:
    result = verify(_seal(keyring, kind=AuthorityKind.UPI_PIN))
    assert result.claims["authority"] == "upi-pin"
    assert result.claims["binding"] == "amount bound by payer-bank"
    assert "payer's own bank" in result.claims["strength"]
    assert "not signed by the buyer" in result.claims["strength"]


def test_a_passkey_receipt_claims_the_basket(keyring: Keyring) -> None:
    result = verify(_seal(keyring, kind=AuthorityKind.PASSKEY))
    assert result.claims["binding"] == "cart bound by payer-device"
    assert "buyer-attested over the exact basket" in result.claims["strength"]


def test_a_cod_receipt_says_the_merchant_asserts_the_capture(keyring: Keyring) -> None:
    """The truthful thing to print rather than the flattering one."""
    result = verify(
        _seal(
            keyring,
            method=PaymentMethod.CASH_ON_DELIVERY,
            kind=AuthorityKind.CONFIRMED_INTENT,
            mechanism="upi-verify",
        )
    )
    assert result.claims["mechanism"] == "upi-verify"
    assert "asserted by the Merchant" in result.claims["money"]
    assert "No payment rail attested it" in result.claims["money"]


def test_the_rendered_report_never_says_verified_unqualified(keyring: Keyring) -> None:
    rendered = verify(_seal(keyring)).render()
    assert "VALID" in rendered
    assert "upi-pin" in rendered
    assert "amount bound by payer-bank" in rendered


# ── COD sealing and versioning ───────────────────────────────────────────────


def test_a_cod_bundle_seals_at_confirmed_with_an_empty_moved_section(
    keyring: Keyring,
) -> None:
    """Money moves days after `confirmed`, so there is no payment moment to seal
    at. Every other section is already final, and the Consumer has a verifiable
    receipt from the moment they commit."""
    bundle = _seal(
        keyring,
        method=PaymentMethod.CASH_ON_DELIVERY,
        kind=AuthorityKind.CONFIRMED_INTENT,
        mechanism="upi-verify",
        entries=[],
    )
    result = verify(bundle)
    assert result.exit_code is ExitCode.VALID
    assert bundle.section("moved").payload["entries"] == []
    assert "nothing has moved yet" in result.claims["money"]


def test_collection_appends_a_moved_entry_as_v2(keyring: Keyring) -> None:
    """Through exactly the path a refund already uses. No new machinery."""
    v1 = _seal(
        keyring,
        method=PaymentMethod.CASH_ON_DELIVERY,
        kind=AuthorityKind.CONFIRMED_INTENT,
        mechanism="upi-verify",
        entries=[],
    )
    v2 = append_moved_entry(
        v1,
        ledger_entry_payload(LedgerKind.CAPTURE, 259700, "INR", "2026-09-27T09:00:00Z"),
    )
    v2.signing_kid = "k1"
    v2.signature = sign(keyring.keys["k1"], v2.signing_payload())

    assert v2.version == 2
    assert verify(v2).exit_code is ExitCode.VALID
    assert verify(v1).exit_code is ExitCode.VALID, "v1 stays verifiable as-is"


def test_a_partial_refund_produces_v2_and_v1_stays_valid(keyring: Keyring) -> None:
    v1 = _seal(keyring)
    v2 = append_moved_entry(
        v1, ledger_entry_payload(LedgerKind.REFUND, 30000, "INR", "2026-09-22T10:00:00Z")
    )
    v2.signing_kid = "k1"
    v2.signature = sign(keyring.keys["k1"], v2.signing_payload())

    assert verify(v1).exit_code is ExitCode.VALID
    assert verify(v2).exit_code is ExitCode.VALID
    assert len(v2.section("moved").payload["entries"]) == 2
    assert refund_summary(259700, 30000) == "Refunded ₹300.00 of ₹2,597.00"


def test_versioning_does_not_disturb_the_earlier_links(keyring: Keyring) -> None:
    v1 = _seal(keyring)
    v2 = append_moved_entry(v1, ledger_entry_payload(LedgerKind.REFUND, 1, "INR", "t"))
    assert [s.link for s in v2.sections[:-1]] == [s.link for s in v1.sections[:-1]]
    assert v2.sections[-1].link != v1.sections[-1].link


# ── Attestation digests ──────────────────────────────────────────────────────


def test_a_renamed_option_axis_cannot_rewrite_what_was_bought() -> None:
    """The digest covers the item's *resolved* values."""
    original = attestation_digest("SD-TOTE-BLK-M", 89900, [], {"colour": "black", "size": "M"})
    renamed = attestation_digest("SD-TOTE-BLK-M", 89900, [], {"color": "black", "size": "M"})
    assert original != renamed


def test_tag_order_does_not_change_the_digest() -> None:
    assert attestation_digest("X", 1, ["b", "a"], {}) == attestation_digest("X", 1, ["a", "b"], {})


def test_a_price_change_changes_the_digest() -> None:
    assert attestation_digest("X", 100, [], {}) != attestation_digest("X", 101, [], {})


# ── Money formatting ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("minor", "expected"),
    [(259700, "₹2,597.00"), (5, "₹0.05"), (0, "₹0.00"), (100, "₹1.00"), (-2500, "-₹25.00")],
)
def test_rupees_format_by_integer_arithmetic(minor: int, expected: str) -> None:
    """Float division would give `2597.0000000000005` on the wrong input, and
    this is the number a Consumer reads off a receipt."""
    assert format_rupees(minor) == expected


# ── The CLI and the public viewer ────────────────────────────────────────────


def test_the_cli_exits_zero_one_and_two(keyring: Keyring, tmp_path: Any) -> None:
    """Exit codes are the interface: a script piping receipts through this needs
    to tell the three apart without parsing prose."""
    from openstore.sidecar.verify.cli import main

    clean = tmp_path / "clean.json"
    clean.write_bytes(bundle_to_bytes(_seal(keyring)))
    assert main([str(clean)]) == 0

    tampered_bundle = _seal(keyring)
    tampered_bundle.section("bought").payload["quote"]["total_minor"] = 1
    tampered = tmp_path / "tampered.json"
    tampered.write_bytes(bundle_to_bytes(tampered_bundle))
    assert main([str(tampered)]) == 1

    untrusted_bundle = _seal(keyring)
    untrusted_bundle.signing_kid = "k-gone"
    untrusted = tmp_path / "untrusted.json"
    untrusted.write_bytes(bundle_to_bytes(untrusted_bundle))
    assert main([str(untrusted)]) == 2


def test_the_cli_opens_commitments_when_given_the_salt(keyring: Keyring, tmp_path: Any) -> None:
    from openstore.sidecar.verify.cli import main

    bundle_path = tmp_path / "b.json"
    bundle_path.write_bytes(bundle_to_bytes(_seal(keyring)))
    dest = tmp_path / "d.json"
    dest.write_text(json.dumps(DESTINATION))
    contact = tmp_path / "c.json"
    contact.write_text(json.dumps(CONTACT))

    assert (
        main(
            [
                str(bundle_path),
                "--salt",
                SALT.hex(),
                "--destination",
                str(dest),
                "--contact",
                str(contact),
            ]
        )
        == 0
    )


def test_the_receipt_viewer_is_outside_the_console_auth_boundary(keyring: Keyring) -> None:
    """`/agentic` is session-authenticated for the Merchant. A receipt that
    opens by unguessable id with no login cannot live there, or no Consumer
    could ever open their own."""
    from fastapi.testclient import TestClient
    from openstore.sidecar.app import app
    from openstore.sidecar.evidence.store import get_receipt_store

    bundle = _seal(keyring)
    get_receipt_store().put(bundle)
    try:
        with TestClient(app) as client:
            response = client.get(f"/receipt/{bundle.receipt_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["verification"]["status"] == "VALID"
            assert body["verification"]["claims"]["authority"] == "upi-pin"
            assert sorted(body["verification"]["unopened"]) == ["contact", "destination"]
            assert not response.request.headers.get("cookie"), "no session was needed"
    finally:
        get_receipt_store().clear()


def test_an_unknown_receipt_id_is_not_found() -> None:
    from fastapi.testclient import TestClient
    from openstore.sidecar.app import app

    with TestClient(app) as client:
        response = client.get("/receipt/rcpt_doesnotexist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not-found"
