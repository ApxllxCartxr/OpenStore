"""The Transcript: byte-stable, stored with the decision, forensic.

Every check's result is in it — `pass`, `fail`, or `deferred` — in the fixed
order `decide()` runs them. The bytes are what translators are pinned against
(SPEC §9): the envelope differs per protocol, the core decision bytes do not.

`deferred` exists because `upi-pin`'s Authority arrives with the money.
Recording it as `pass` at `decide()` would put a false statement into signed
evidence. `settle()` must resolve every `deferred` to `pass` before it captures,
and a bundle carrying an unresolved one fails verification.

Transcripts are forensic; the structured log line is operational (SPEC §12).
They answer different questions and neither replaces the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openstore.sidecar.core.canonical import canonical_bytes
from openstore.sidecar.core.codes import (
    GATE_CHECK_ORDER,
    AuthorityKind,
    BindingBy,
    BindingWhat,
    CheckResult,
    GateCheck,
    ReasonCode,
)


@dataclass(frozen=True)
class Binding:
    """What an Authority actually covered, declared rather than inferred.

    Normative per §6.5's table. A bundle never reads "verified" unqualified:
    ranking the kinds would describe one rail, while making every implementer
    declare the strength of its own claim describes all of them.
    """

    what: BindingWhat
    by: BindingBy

    def to_dict(self) -> dict[str, str]:
        return {"what": self.what.value, "by": self.by.value}


#: Which Binding each Authority kind carries, and when it lands. Normative —
#: the kind declares this, never the caller.
BINDINGS: dict[tuple[AuthorityKind, str | None], tuple[Binding, bool]] = {
    # (kind, mechanism): (binding, lands_before_the_gate)
    (AuthorityKind.UPI_PIN, None): (Binding(BindingWhat.AMOUNT, BindingBy.PAYER_BANK), False),
    (AuthorityKind.PASSKEY, None): (Binding(BindingWhat.CART, BindingBy.PAYER_DEVICE), True),
    (AuthorityKind.CONFIRMED_INTENT, "upi-verify"): (
        Binding(BindingWhat.NONE, BindingBy.PAYER_BANK),
        True,
    ),
    (AuthorityKind.CONFIRMED_INTENT, "passkey"): (
        Binding(BindingWhat.CART, BindingBy.PAYER_DEVICE),
        True,
    ),
}


def binding_for(kind: AuthorityKind, mechanism: str | None = None) -> tuple[Binding, bool]:
    """The Binding and landing time for a kind. `mandate` is defined and refused
    in v1, so it has no entry and asking for one is the refusal."""
    try:
        return BINDINGS[(kind, mechanism)]
    except KeyError:
        raise ValueError(
            f"no Binding for {kind.value}"
            + (f"/{mechanism}" if mechanism else "")
            + " — `mandate` is registered and refused in v1 (ADR-0017)"
        ) from None


@dataclass
class CheckRecord:
    check: GateCheck
    result: CheckResult
    reason_code: ReasonCode | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"check": self.check.value, "result": self.result.value}
        if self.reason_code:
            body["reason_code"] = self.reason_code.value
        if self.detail:
            body["detail"] = self.detail
        return body


@dataclass
class Transcript:
    """The record of one Gate decision.

    `expiry_utc` and `cart_hash` are in here because they are what the Consumer
    authorized. Nothing clock-derived is added beyond them: a timestamp of when
    the decision ran would make two identical decisions produce different bytes,
    and the golden replays compare bytes.
    """

    order_id: str
    cart_hash: str
    quote_hash: str
    total_minor: int
    currency: str
    merchant_domain: str
    expiry_utc: str
    agent_id: str
    consumer_id: str
    authority_kind: AuthorityKind | None = None
    authority_mechanism: str | None = None
    binding: Binding | None = None
    ceremony: str | None = None
    checks: list[CheckRecord] = field(default_factory=list)
    reason_code: ReasonCode | None = None
    """The **first** failure, and only the first: the Gate stops there."""

    def record(
        self,
        check: GateCheck,
        result: CheckResult,
        reason_code: ReasonCode | None = None,
        detail: str = "",
    ) -> CheckRecord:
        entry = CheckRecord(check, result, reason_code, detail)
        self.checks.append(entry)
        if result is CheckResult.FAIL and self.reason_code is None:
            self.reason_code = reason_code
        return entry

    @property
    def passed(self) -> bool:
        return self.reason_code is None

    @property
    def deferred_checks(self) -> list[CheckRecord]:
        return [c for c in self.checks if c.result is CheckResult.DEFERRED]

    def resolve_deferred(self, check: GateCheck, result: CheckResult) -> None:
        """`settle()` turning a `deferred` into its real answer.

        Only ever called for a check that actually deferred — resolving one that
        passed would rewrite a decision after the fact, which is the one thing a
        forensic record may not do.
        """
        for record in self.checks:
            if record.check is check and record.result is CheckResult.DEFERRED:
                record.result = result
                if result is CheckResult.FAIL and self.reason_code is None:
                    self.reason_code = ReasonCode.AUTHORITY_MISSING
                return
        raise ValueError(f"{check.value} did not defer; there is nothing to resolve")

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "order_id": self.order_id,
            "cart_hash": self.cart_hash,
            "quote_hash": self.quote_hash,
            "total_minor": self.total_minor,
            "currency": self.currency,
            "merchant_domain": self.merchant_domain,
            "expiry_utc": self.expiry_utc,
            "agent_id": self.agent_id,
            "consumer_id": self.consumer_id,
            "checks": [c.to_dict() for c in self.checks],
        }
        if self.authority_kind:
            body["authority"] = {
                "kind": self.authority_kind.value,
                "binding": self.binding.to_dict() if self.binding else None,
            }
            if self.authority_mechanism:
                body["authority"]["mechanism"] = self.authority_mechanism
            if self.ceremony:
                body["authority"]["ceremony"] = self.ceremony
        if self.reason_code:
            body["reason_code"] = self.reason_code.value
        return body

    def to_bytes(self) -> bytes:
        """The byte-stable form. Pinned in CI across every protocol envelope."""
        return canonical_bytes(self.to_dict())

    def assert_complete(self) -> None:
        """Every check ran, in order, and nothing is still deferred.

        Called before capture. A bundle carrying an unresolved `deferred` is a
        bundle claiming a check happened that did not.
        """
        ran = [c.check for c in self.checks]
        expected = list(GATE_CHECK_ORDER[: len(ran)])
        if ran != expected:
            raise ValueError(f"checks ran out of order: {[c.value for c in ran]}")
        if self.deferred_checks:
            raise ValueError(
                "unresolved deferred checks: "
                + ", ".join(c.check.value for c in self.deferred_checks)
            )


def transcript_from_dict(body: dict[str, Any]) -> Transcript:
    """Rebuild a Transcript from `to_dict`.

    Exists because a decision has to survive the gap between the tap and the
    money arriving, which may span a restart. **The round trip must be exact**:
    these bytes are what the receipt carries and what the golden replays pin, so
    a field this forgets would not be a missing field — it would be a different
    signed document. `test_transcript_round_trip_is_byte_stable` is the check
    that keeps the two functions in step.
    """
    authority = body.get("authority") or {}
    binding = authority.get("binding") or None
    transcript = Transcript(
        order_id=body["order_id"],
        cart_hash=body["cart_hash"],
        quote_hash=body["quote_hash"],
        total_minor=body["total_minor"],
        currency=body["currency"],
        merchant_domain=body["merchant_domain"],
        expiry_utc=body["expiry_utc"],
        agent_id=body["agent_id"],
        consumer_id=body["consumer_id"],
        authority_kind=AuthorityKind(authority["kind"]) if authority.get("kind") else None,
        authority_mechanism=authority.get("mechanism"),
        binding=(
            Binding(BindingWhat(binding["what"]), BindingBy(binding["by"])) if binding else None
        ),
        ceremony=authority.get("ceremony"),
        checks=[
            CheckRecord(
                check=GateCheck(c["check"]),
                result=CheckResult(c["result"]),
                reason_code=ReasonCode(c["reason_code"]) if c.get("reason_code") else None,
                detail=c.get("detail", ""),
            )
            for c in body.get("checks", [])
        ],
    )
    # Set after the checks, because `record()` is what normally sets it and
    # these are being restored rather than recorded.
    transcript.reason_code = ReasonCode(body["reason_code"]) if body.get("reason_code") else None
    return transcript
