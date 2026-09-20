"""Offline verification. Exit 0 valid, 1 tampered, 2 untrusted key.

**Offline means offline.** The bundle carries its own JWKS snapshot, so the
verifier needs no network, no registry, and no cooperation from the Merchant
whose behaviour it is checking. A verifier that has to phone home is a verifier
the Merchant can switch off.

It reports rather than summarises. A bundle never comes back "verified": it
comes back with the Authority kind, its mechanism, and its Binding printed, so
the reader knows which claim they are being offered instead of assuming the
strongest one. The PII sections come back `unopened` — the commitments cannot be
opened without the Merchant's salt, and that is a correct state rather than a
failure (ADR-0011).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any

from openstore.sidecar.core.canonical import canonical_bytes, pii_commit
from openstore.sidecar.evidence.bundle import SECTION_ORDER, Bundle, Section, _chain
from openstore.sidecar.evidence.keys import verify_signature


class ExitCode(IntEnum):
    VALID = 0
    TAMPERED = 1
    UNTRUSTED_KEY = 2


@dataclass
class Finding:
    ok: bool
    label: str
    detail: str = ""


@dataclass
class VerifyResult:
    exit_code: ExitCode
    findings: list[Finding] = field(default_factory=list)
    claims: dict[str, str] = field(default_factory=dict)
    unopened: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code is ExitCode.VALID

    def render(self) -> str:
        lines = [f"receipt {self.claims.get('receipt_id', '?')} — {self.exit_code.name}"]
        for finding in self.findings:
            lines.append(f"  [{'ok' if finding.ok else 'FAIL'}] {finding.label}{
                f': {finding.detail}' if finding.detail else ''}")
        if self.claims:
            lines.append("  claims:")
            lines.extend(f"    {k}: {v}" for k, v in self.claims.items() if k != "receipt_id")
        if self.unopened:
            lines.append(f"  unopened (no salt, as designed): {', '.join(self.unopened)}")
        return "\n".join(lines)


def verify(
    bundle: Bundle,
    *,
    order_salt: bytes | None = None,
    destination: dict[str, Any] | None = None,
    contact: dict[str, Any] | None = None,
) -> VerifyResult:
    """Check the chain, the signature, the key's validity window, and — only if
    the caller supplied the salt — the PII commitments."""
    findings: list[Finding] = []
    claims: dict[str, str] = {"receipt_id": bundle.receipt_id}
    unopened: list[str] = []

    # 1. Sections present and in order.
    names = [s.name for s in bundle.sections]
    if names != list(SECTION_ORDER):
        findings.append(
            Finding(False, "sections", f"expected {list(SECTION_ORDER)}, found {names}")
        )
        return VerifyResult(ExitCode.TAMPERED, findings, claims, unopened)
    findings.append(Finding(True, "five sections, in order"))

    # 2. The hash chain, link by link, naming the exact one that breaks.
    previous = ""
    for section in bundle.sections:
        expected = _chain(previous, section.name, section.payload)
        if expected != section.link:
            findings.append(
                Finding(
                    False,
                    f"chain link '{section.name}'",
                    "this section's contents do not match the link recorded for it",
                )
            )
            return VerifyResult(ExitCode.TAMPERED, findings, claims, unopened)
        previous = section.link
    findings.append(Finding(True, "hash chain intact"))

    # 3. The signature, against the bundle's own snapshot.
    jwk = _find_key(bundle, bundle.signing_kid)
    if jwk is None:
        findings.append(
            Finding(False, "signing key", f"kid {bundle.signing_kid!r} is not in the snapshot")
        )
        return VerifyResult(ExitCode.UNTRUSTED_KEY, findings, claims, unopened)

    if not verify_signature(jwk, bundle.signing_payload(), bundle.signature):
        findings.append(Finding(False, "signature", "does not verify against the pinned key"))
        return VerifyResult(ExitCode.TAMPERED, findings, claims, unopened)
    findings.append(Finding(True, f"signed by {bundle.signing_kid}"))

    # 4. Was the key revoked *before* this bundle was sealed?
    revoked = jwk.get("revoked_at")
    if revoked and bundle.sealed_at:
        if _parse(str(revoked)) <= _parse(bundle.sealed_at):
            findings.append(
                Finding(
                    False,
                    "key validity",
                    f"{bundle.signing_kid} was revoked at {revoked}, before this receipt "
                    f"was sealed at {bundle.sealed_at}",
                )
            )
            return VerifyResult(ExitCode.UNTRUSTED_KEY, findings, claims, unopened)
        findings.append(
            Finding(
                True,
                "key validity",
                f"{bundle.signing_kid} was revoked later, at {revoked}; a receipt signed "
                f"before revocation stays valid",
            )
        )

    # 5. The claims, printed rather than summarised.
    claims.update(_claims(bundle))

    # 6. PII: an online check by design.
    bought = bundle.section("bought").payload
    for label, commitment, value in (
        ("destination", bought["destination_commitment"], destination),
        ("contact", bought["contact_commitment"], contact),
    ):
        if order_salt is None or value is None:
            unopened.append(label)
            continue
        if pii_commit(order_salt, value) == commitment:
            findings.append(Finding(True, f"{label} matches its commitment"))
        else:
            findings.append(
                Finding(
                    False,
                    f"{label} commitment",
                    "the stored row does not match what was committed to — the row was "
                    "altered after the receipt was sealed",
                )
            )
            return VerifyResult(ExitCode.TAMPERED, findings, claims, unopened)

    return VerifyResult(ExitCode.VALID, findings, claims, unopened)


def _claims(bundle: Bundle) -> dict[str, str]:
    """The honest claim, in the words the plan chose.

    Never 'verified' unqualified: the reader is told which kind, which
    mechanism, what it bound and who attested it, and a COD `moved` says plainly
    that the Merchant asserts it and no rail attested it.
    """
    tapped = bundle.section("tapped").payload
    moved = bundle.section("moved").payload
    kind = tapped["authority_kind"]
    binding = tapped["binding"]

    claims = {
        "authority": kind,
        "binding": f"{binding['what']} bound by {binding['by']}",
    }
    if tapped.get("mechanism"):
        claims["mechanism"] = tapped["mechanism"]
    if tapped.get("ceremony"):
        claims["ceremony"] = tapped["ceremony"]

    if kind == "upi-pin":
        claims["strength"] = (
            "tamper-evident throughout; third-party attested at the money step by the "
            "payer's own bank. The basket is bound by reference through the Quote, not "
            "signed by the buyer."
        )
    elif kind == "passkey":
        claims["strength"] = (
            "tamper-evident throughout; buyer-attested over the exact basket by the "
            "payer's own device."
        )
    else:
        claims["strength"] = (
            "tamper-evident throughout; see the mechanism for what was actually bound."
        )

    # Order matters: a COD bundle sealed at `confirmed` has no CAPTURE yet, and
    # saying "the Merchant asserts this CAPTURE" about a capture that has not
    # happened is exactly the kind of flattering claim this section exists to
    # avoid. Emptiness is checked first.
    if not moved.get("entries"):
        claims["money"] = "nothing has moved yet; this receipt was sealed at confirmation."
    elif moved.get("merchant_asserted"):
        claims["money"] = (
            "cash on delivery: this CAPTURE is asserted by the Merchant. No payment rail "
            "attested it."
        )

    return claims


def _find_key(bundle: Bundle, kid: str) -> dict[str, Any] | None:
    keys = bundle.jwks_snapshot.get("keys", [])
    if not isinstance(keys, list):
        return None
    for key in keys:
        if isinstance(key, dict) and key.get("kid") == kid:
            return key
    return None


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def bundle_from_dict(document: dict[str, Any]) -> Bundle:
    """Rebuild a bundle from its JSON form, for the CLI and the viewer."""
    return Bundle(
        receipt_id=document["receipt_id"],
        version=document["version"],
        merchant_domain=document["merchant_domain"],
        sections=[Section(s["name"], s["payload"], s["link"]) for s in document["sections"]],
        jwks_snapshot=document["jwks"],
        signature=document.get("signature", ""),
        signing_kid=document.get("kid", ""),
        sealed_at=document.get("sealed_at", ""),
        demo=document.get("demo", True),
    )


def bundle_to_bytes(bundle: Bundle) -> bytes:
    return canonical_bytes(bundle.to_dict())
