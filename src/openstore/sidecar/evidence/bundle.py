"""The sealed receipt: five sections, hash-chained, Merchant-ES256-signed.

**bought / tapped / decided / told / moved.** No Merkle in v1 (ADR-0009) — a
hash chain over five sections is enough for a document nobody batches, and a
Merkle tree would be machinery serving a scale that does not exist.

It carries its own JWKS snapshot so offline verify is really offline, and it
opens by an unguessable 128-bit `receipt_id` with **no login**. The ID is the
only credential and 128 bits is the protection, which is why the viewer lives at
`/receipt/<id>` outside the console's auth boundary: putting it under `/agentic`
would mean no Consumer could ever open their own receipt.

The honest claim, which the verifier prints rather than summarising as
"verified": tamper-evident throughout, third-party attested at the money step by
the payer's own bank under `upi-pin`, and buyer-attested over the basket **only**
under `passkey`. On a COD receipt the `moved` section shows a `CAPTURE` the
**Merchant asserts** — no rail attested it, and saying so is the truthful thing
to print rather than the flattering one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from openstore.sidecar.core.canonical import canonical_bytes
from openstore.sidecar.core.codes import AuthorityKind, LedgerKind, PaymentMethod

SECTION_ORDER = ("bought", "tapped", "decided", "told", "moved")


def attestation_digest(sku: str, price_minor: int, tags: list[str], options: dict[str, str]) -> str:
    """`sha256(sku | price_minor | sorted tags | canonical resolved-options JSON)`.

    The options are the Catalogue Item's **resolved** values (`{colour: black,
    size: M}`), so a later rename of an option axis cannot rewrite what was
    bought. Frozen before S3/S6 (SPEC §4).
    """
    canonical = b"|".join(
        [
            sku.encode("utf-8"),
            str(price_minor).encode("ascii"),
            ",".join(sorted(tags)).encode("utf-8"),
            canonical_bytes(options),
        ]
    )
    return hashlib.sha256(canonical).hexdigest()


def _chain(previous: str, section_name: str, payload: dict[str, Any]) -> str:
    """Each link covers the previous link **and** the section's name.

    Without the name, two sections with identical bytes would be
    interchangeable, and a `told` could be presented as a `moved`.
    """
    material = b"|".join(
        [previous.encode("ascii"), section_name.encode("ascii"), canonical_bytes(payload)]
    )
    return hashlib.sha256(material).hexdigest()


@dataclass
class Section:
    name: str
    payload: dict[str, Any]
    link: str = ""


@dataclass
class Bundle:
    """One order's receipt, at one version."""

    receipt_id: str
    version: int
    merchant_domain: str
    sections: list[Section]
    jwks_snapshot: dict[str, Any]
    signature: str = ""
    signing_kid: str = ""
    sealed_at: str = ""
    demo: bool = True
    """Demo receipts are marked, and the sidecar refuses live keys in demo mode.
    A receipt that cannot say which it is has no evidentiary value at all."""

    def section(self, name: str) -> Section:
        return next(s for s in self.sections if s.name == name)

    def head(self) -> str:
        return self.sections[-1].link if self.sections else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "version": self.version,
            "merchant_domain": self.merchant_domain,
            "demo": self.demo,
            "sealed_at": self.sealed_at,
            "sections": [
                {"name": s.name, "payload": s.payload, "link": s.link} for s in self.sections
            ],
            "jwks": self.jwks_snapshot,
            "signature": self.signature,
            "kid": self.signing_kid,
        }

    def signing_payload(self) -> bytes:
        """What the signature covers: everything except the signature itself.

        The JWKS snapshot is inside the signed payload deliberately — a bundle
        whose key list could be swapped after signing would verify against
        whatever key an attacker supplied.
        """
        body = self.to_dict()
        body.pop("signature")
        return canonical_bytes(body)


@dataclass
class BundleBuilder:
    """Assembles the five sections in order, chaining as it goes."""

    receipt_id: str
    merchant_domain: str
    demo: bool = True
    _sections: list[Section] = field(default_factory=list)

    def _add(self, name: str, payload: dict[str, Any]) -> BundleBuilder:
        expected = SECTION_ORDER[len(self._sections)]
        if name != expected:
            raise ValueError(f"sections are sealed in order; expected {expected!r}, got {name!r}")
        previous = self._sections[-1].link if self._sections else ""
        self._sections.append(Section(name, payload, _chain(previous, name, payload)))
        return self

    def bought(
        self,
        *,
        quote: dict[str, Any],
        lines: list[dict[str, Any]],
        attestation_digests: dict[str, str],
        destination_hash: str,
        contact_hash: str,
    ) -> BundleBuilder:
        """The Quote breakdown **verbatim** — shipping and GST exactly as the
        Consumer saw them — plus salted commitments to the two PII fields.

        The commitments make the PII binding an *online* check: offline verify
        covers the chain, the signatures and every non-PII fact, and reports
        these two as `unopened` rather than failing (ADR-0011).
        """
        return self._add(
            "bought",
            {
                "quote": quote,
                "lines": lines,
                "attestations": dict(sorted(attestation_digests.items())),
                "destination_commitment": destination_hash,
                "contact_commitment": contact_hash,
            },
        )

    def tapped(
        self,
        *,
        authority_kind: AuthorityKind,
        mechanism: str | None,
        binding_what: str,
        binding_by: str,
        ceremony: str | None,
        cart_hash: str,
    ) -> BundleBuilder:
        """Kind, mechanism and Binding **verbatim**. The verifier prints all
        three rather than reporting an unqualified 'verified'."""
        payload: dict[str, Any] = {
            "authority_kind": authority_kind.value,
            "binding": {"what": binding_what, "by": binding_by},
            "cart_hash": cart_hash,
        }
        if mechanism:
            payload["mechanism"] = mechanism
        if ceremony:
            payload["ceremony"] = ceremony
        return self._add("tapped", payload)

    def decided(self, *, transcript: dict[str, Any]) -> BundleBuilder:
        return self._add("decided", {"transcript": transcript})

    def told(self, *, notifications: list[dict[str, Any]]) -> BundleBuilder:
        return self._add("told", {"notifications": notifications})

    def moved(self, *, entries: list[dict[str, Any]], method: PaymentMethod) -> BundleBuilder:
        """Money events.

        **Empty at seal time on a COD order**, and that is deliberate: money
        moves days after `confirmed`, so there is no payment moment to seal at.
        Every other section is already final — the basket, the Quote, the
        Authority and the decision all exist — so the bundle is sealed at
        `confirmed` with nothing here, and collection appends a `moved` entry as
        v2 through exactly the path a refund already uses. The Consumer has a
        verifiable receipt from the moment they commit rather than only after
        they pay.
        """
        return self._add(
            "moved",
            {
                "entries": entries,
                "method": method.value,
                # No rail attested a cash collection. Saying so is the truthful
                # thing to print rather than the flattering one.
                "merchant_asserted": method is PaymentMethod.CASH_ON_DELIVERY,
            },
        )

    def seal(self, *, jwks_snapshot: dict[str, Any], version: int = 1) -> Bundle:
        if len(self._sections) != len(SECTION_ORDER):
            missing = SECTION_ORDER[len(self._sections) :]
            raise ValueError(f"cannot seal without {', '.join(missing)}")
        return Bundle(
            receipt_id=self.receipt_id,
            version=version,
            merchant_domain=self.merchant_domain,
            sections=list(self._sections),
            jwks_snapshot=jwks_snapshot,
            sealed_at=datetime.now(UTC).isoformat(),
            demo=self.demo,
        )


def append_moved_entry(
    bundle: Bundle, entry: dict[str, Any], *, jwks_snapshot: dict[str, Any] | None = None
) -> Bundle:
    """Version the bundle: v1 stays verifiable as-is, this is v(n+1).

    Used by both a refund and a COD collection — the same path, because they are
    the same act from the bundle's point of view: a new money event on an order
    whose other four sections were already final.
    """
    moved = bundle.section("moved")
    new_payload = dict(moved.payload)
    new_payload["entries"] = [*moved.payload["entries"], entry]

    sections = [Section(s.name, s.payload, s.link) for s in bundle.sections[:-1]]
    previous = sections[-1].link
    sections.append(Section("moved", new_payload, _chain(previous, "moved", new_payload)))

    return Bundle(
        receipt_id=bundle.receipt_id,
        version=bundle.version + 1,
        merchant_domain=bundle.merchant_domain,
        sections=sections,
        jwks_snapshot=jwks_snapshot or bundle.jwks_snapshot,
        sealed_at=datetime.now(UTC).isoformat(),
        demo=bundle.demo,
    )


def format_rupees(minor: int) -> str:
    """Paise to a rupee string, by integer arithmetic.

    Float division would be the obvious way and it is wrong here: this is the
    number a Consumer reads off a receipt, and `2597.0000000000005` is not a
    price. `divmod` has no rounding mode to get wrong.
    """
    rupees, paise = divmod(abs(minor), 100)
    sign = "-" if minor < 0 else ""
    return f"{sign}₹{rupees:,}.{paise:02d}"


def refund_summary(captured_minor: int, refunded_minor: int) -> str:
    """`Refunded ₹X of ₹Y` — so partial is legible without a ninth status."""
    return f"Refunded {format_rupees(refunded_minor)} of {format_rupees(captured_minor)}"


def ledger_entry_payload(
    kind: LedgerKind, amount_minor: int, currency: str, at: str, reference: str = ""
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind.value,
        "amount_minor": amount_minor,
        "currency": currency,
        "at": at,
    }
    if reference:
        payload["reference"] = reference
    return payload
