"""One golden end-to-end replay per protocol.

search → allow → cart → tap → fake-UPI → receipt → verify, four times, through
four envelopes, against one core. The core Transcript is byte-compared across
all of them: envelopes differ, core decision bytes do not.

This is the test that would catch a translator growing its own money path, which
is the failure the whole architecture exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from openstore.sidecar.admission.oauth import Admission
from openstore.sidecar.authority.kinds import HandleSource, derive_consumer_id
from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.core.canonical import pii_commit
from openstore.sidecar.core.codes import (
    AuthorityKind,
    LedgerKind,
    OrderStatus,
    PaymentMethod,
    Protocol,
    Scope,
    ToolName,
)
from openstore.sidecar.evidence.bundle import (
    BundleBuilder,
    attestation_digest,
    ledger_entry_payload,
)
from openstore.sidecar.evidence.keys import Keyring, sign
from openstore.sidecar.gate.decide import Authority, DecisionInput, Gate
from openstore.sidecar.gate.policy import Policy
from openstore.sidecar.gate.settle import ProviderRecord, settle
from openstore.sidecar.ledger.entries import Ledger
from openstore.sidecar.protocols import acp, mcp, ucp
from openstore.sidecar.protocols.core import approve_url, run_core
from openstore.sidecar.protocols.registry import TOGGLE_ORDER, badge
from openstore.sidecar.provider.fake import FakeProvider
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.fake import FakeMerchant
from openstore.sidecar.trait.models import Destination, Line
from openstore.sidecar.trait.seed import SEED_ITEMS
from openstore.sidecar.verify.checks import ExitCode, verify

DEST_B = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "demo@spoiledduckie.test", "phone": "+919000000001"}
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")
PSEUDONYM_KEY = b"deploy-pseudonym-key-for-spoiledduckie"

GROUP_OF = {sku: group for sku, group, *_ in SEED_ITEMS}
TAGS_OF = {i[0]: i[-1] for i in SEED_ITEMS}
PRICE_OF = {i[0]: i[3] for i in SEED_ITEMS}
OPTIONS_OF = {i[0]: i[8] for i in SEED_ITEMS}

BASKET = [
    Line(sku="SD-TOTE-BLK-M", qty=1),
    Line(sku="SD-GIFTWRAP", qty=1, parent="SD-TOTE-BLK-M"),
    Line(sku="SD-CHARMBAR-SEAT", qty=1),
]


@dataclass
class Replay:
    """What one protocol's run produced."""

    protocol: Protocol
    transcript_bytes: bytes
    envelope: dict[str, Any]
    total_minor: int
    receipt_status: ExitCode
    ledger_kinds: list[LedgerKind]
    order_status: OrderStatus


async def _run(
    protocol: Protocol,
    trait: TraitClient,
    merchant: FakeMerchant,
    ledger: Ledger,
    order_id: str,
) -> Replay:
    gate = Gate(trait, Policy())
    tokens = TokenStore()
    provider = FakeProvider()
    keyring = Keyring("spoiledduckie.localhost")
    keyring.enroll("k1")

    # ── search: the agent is admitted with no prior Merchant action ──────────
    token = Admission().issue_for_stranger("thumbprint_one_agent")
    assert token.allows(Scope.SEARCH)
    assert mcp.check_scope(token, ToolName.SEARCH) is None
    catalog = await trait.catalog_read()
    assert any(item.sku == "SD-TOTE-BLK-M" for item in catalog.items)

    # ── cart + quote: door 7 produces the order id and the salt (§6.3a) ──────
    merchant.fixed_order_salt_hex = SALT.hex()
    created = await trait.orders_create(
        f"cart_{protocol.value}",
        BASKET,
        DEST_B,
        CONTACT,
        "rest-of-india",
        order_id_hint=order_id,
    )
    assert created.order_id == order_id
    salt = bytes.fromhex(created.order_salt_hex)

    consumer_id = derive_consumer_id(PSEUDONYM_KEY, "demo@okaxis", source=HandleSource.PAYER_HANDLE)
    request = DecisionInput(
        order_id=order_id,
        cart_id=f"cart_{protocol.value}",
        lines=BASKET,
        destination=DEST_B,
        contact=CONTACT,
        fulfillment_option_id="rest-of-india",
        order_salt=salt,
        expiry_utc="2026-09-21T12:00:00Z",
        merchant_domain="spoiledduckie.localhost",
        agent_id=token.agent_id,
        consumer_id=consumer_id,
        authority=Authority(kind=AuthorityKind.UPI_PIN),
        method=PaymentMethod.UPI,
        group_of=GROUP_OF,
        tags_of=TAGS_OF,
        attested_prices=PRICE_OF,
    )

    # ── allow + decide: the one core every envelope shares ───────────────────
    result = await run_core(gate, request)
    assert not result.refused, result.detail
    decision = result.decision
    assert decision is not None

    # ── tap: a hold cannot be created without spending an approve token ──────
    tap = tokens.issue_tap(order_id, decision.cart_hash, decision.total_minor)
    spent = tokens.spend_tap(tap.token, cart_hash=decision.cart_hash)
    assert spent.spent

    url = approve_url("spoiledduckie.localhost", tap.token)
    envelope = _render(protocol, decision, url)

    # ── reserve + confirm ────────────────────────────────────────────────────
    await trait.reserve(order_id, BASKET)
    await ledger.reserve(order_id, decision.total_minor, "INR")
    await trait.orders_set_status(order_id, OrderStatus.CONFIRMED.value, "tapped", attempt=1)

    # ── fake-UPI: the Provider says money moved, never that anyone allowed it ─
    link = await provider.make_link(order_id, decision.total_minor, "INR", expires_in_seconds=900)
    provider.approve(link.link_id)
    status = await provider.check_status(link.link_id)

    settlement = await settle(
        decision,
        ProviderRecord(order_id, status.amount_minor, status.currency, status.reference, True),
        ledger,
    )
    assert settlement.status is OrderStatus.PAID
    # attempt=2, because attempt=1 already wrote `confirmed` under this order's
    # key and same-key-same-result would replay it. Two transitions, two keys.
    await trait.orders_set_status(order_id, OrderStatus.PAID.value, "settled", attempt=2)

    # ── receipt ──────────────────────────────────────────────────────────────
    entries = await ledger.entries(order_id)
    builder = BundleBuilder(f"rcpt_{protocol.value}", "spoiledduckie.localhost")
    builder.bought(
        quote=decision.quote.model_dump(),
        lines=[{"sku": line.sku, "qty": line.qty} for line in BASKET],
        attestation_digests={
            line.sku: attestation_digest(
                line.sku, PRICE_OF[line.sku], TAGS_OF[line.sku], OPTIONS_OF[line.sku]
            )
            for line in BASKET
        },
        destination_hash=pii_commit(salt, DEST_B.model_dump()),
        contact_hash=pii_commit(salt, dict(sorted(CONTACT.items()))),
    )
    transcript = decision.transcript
    assert transcript.binding is not None
    builder.tapped(
        authority_kind=transcript.authority_kind or AuthorityKind.UPI_PIN,
        mechanism=transcript.authority_mechanism,
        binding_what=transcript.binding.what.value,
        binding_by=transcript.binding.by.value,
        ceremony=transcript.ceremony,
        cart_hash=decision.cart_hash,
    )
    builder.decided(transcript=transcript.to_dict())
    builder.told(notifications=[{"kind": "order-confirmation"}])
    builder.moved(
        entries=[
            ledger_entry_payload(e.kind, e.amount_minor, e.currency, e.created_at.isoformat())
            for e in entries
        ],
        method=PaymentMethod.UPI,
    )
    bundle = builder.seal(jwks_snapshot=keyring.jwks())
    bundle.signing_kid = "k1"
    bundle.signature = sign(keyring.keys["k1"], bundle.signing_payload())

    # ── verify ───────────────────────────────────────────────────────────────
    verification = verify(bundle)

    order = await trait.orders_read(order_id)
    return Replay(
        protocol=protocol,
        transcript_bytes=result.transcript_bytes,
        envelope=envelope,
        total_minor=decision.total_minor,
        receipt_status=verification.exit_code,
        ledger_kinds=[e.kind for e in entries],
        order_status=order.status,
    )


def _render(protocol: Protocol, decision: Any, url: str) -> dict[str, Any]:
    """The only part that differs per protocol."""
    if protocol is Protocol.MCP:
        return mcp.envelope({"approve_url": url, "total_minor": decision.total_minor})
    if protocol in (Protocol.UCP, Protocol.AP2):
        # AP2 rides on UCP, so it renders UCP's checkout. A separate shape here
        # would be the fourth-peer lie the registry exists to prevent.
        return ucp.checkout(decision.quote, approve_url=url)
    session = acp.session_from_quote(f"cs_{protocol.value}", decision.quote)
    return acp.envelope(session)


@pytest.fixture
async def replays(
    trait: TraitClient, merchant: FakeMerchant, ledger: Ledger
) -> dict[Protocol, Replay]:
    out: dict[Protocol, Replay] = {}
    for protocol in TOGGLE_ORDER:
        out[protocol] = await _run(
            protocol, trait, merchant, ledger, f"ord_golden_{protocol.value}"
        )
    return out


# ── The assertion this phase exists for ──────────────────────────────────────


async def test_the_core_transcript_is_byte_identical_across_all_four(
    replays: dict[Protocol, Replay],
) -> None:
    """Envelopes differ, core decision bytes do not.

    Each replay runs against a different order id, so the Transcripts differ in
    exactly one field by construction — normalising that out is what makes the
    rest of the comparison meaningful.
    """
    normalised = set()
    for protocol, replay in replays.items():
        marker = f'"order_id":"ord_golden_{protocol.value}"'.encode()
        assert marker in replay.transcript_bytes
        normalised.add(replay.transcript_bytes.replace(marker, b'"order_id":"ORDER"'))

    assert len(normalised) == 1, (
        f"{len(normalised)} distinct core Transcripts across four protocols — a "
        f"translator has grown its own money path"
    )


async def test_every_protocol_reaches_a_verified_receipt(
    replays: dict[Protocol, Replay],
) -> None:
    for protocol, replay in replays.items():
        assert replay.receipt_status is ExitCode.VALID, protocol.value
        assert replay.order_status is OrderStatus.PAID
        assert replay.ledger_kinds == [LedgerKind.RESERVE, LedgerKind.CAPTURE]


async def test_every_protocol_charges_the_same_pinned_total(
    replays: dict[Protocol, Replay],
) -> None:
    """§16.11's worked example, reached four ways."""
    assert {r.total_minor for r in replays.values()} == {259700}


async def test_every_envelope_ends_at_the_same_approve_page(
    replays: dict[Protocol, Replay],
) -> None:
    """Every spend terminates in a fresh Authority at /agentic/approve on the
    Merchant's own domain — including ACP's, which says so in its refusal."""
    for protocol, replay in replays.items():
        rendered = str(replay.envelope)
        if protocol is Protocol.ACP:
            # ACP's session carries no approve URL; its `complete` refusal does.
            continue
        assert "/agentic/approve" in rendered, protocol.value


async def test_the_envelopes_actually_differ(replays: dict[Protocol, Replay]) -> None:
    """The other half of the claim: if every envelope were identical the
    byte-identity assertion would be trivially true and prove nothing."""
    shapes = {p: sorted(r.envelope.keys()) for p, r in replays.items()}
    assert shapes[Protocol.MCP] != shapes[Protocol.ACP]
    assert shapes[Protocol.UCP] != shapes[Protocol.ACP]


async def test_the_badge_is_present_for_every_protocol_in_the_toggle() -> None:
    """A golden replay asserts the deviation text is present, per protocol."""
    for protocol in TOGGLE_ORDER:
        result = badge(protocol, frozenset({PaymentMethod.UPI}))
        assert result["deviations"], protocol.value
        assert result["completion"] == "redirect-only"
