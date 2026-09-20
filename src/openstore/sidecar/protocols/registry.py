"""One place naming every protocol, what it is, and what it deviates on.

The header toggle and the conformance badge both read from here, so a protocol
cannot be live in one and stale in the other. That is the whole reason this file
exists rather than two lists that agree today.

**Envelope vs layer is a real distinction, not a label.** MCP, UCP and ACP are
envelopes: each wraps the same core decision in its own request and response
shapes. AP2 is a *security layer over UCP* — its own specification says so — and
building it as a fourth peer would be a nicer-looking lie. The toggle reads
`[MCP | UCP | UCP+AP2 | ACP]` for that reason.

The badge names its deviations **inline**. An unqualified "ACP conformant"
against a specification that revises quarterly ages badly, so the badge carries
the version it targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from openstore.sidecar.core.codes import PaymentMethod, Protocol


@unique
class Shape(StrEnum):
    ENVELOPE = "envelope"
    LAYER = "layer"


@dataclass(frozen=True)
class ProtocolSpec:
    protocol: Protocol
    shape: Shape
    version: str
    live: bool
    rides_on: Protocol | None = None
    refused_step: str = ""
    """The completion step this envelope wanted and we refuse, named."""
    refusal_reason: str = ""
    toggle_label: str = ""


SPECS: dict[Protocol, ProtocolSpec] = {
    Protocol.MCP: ProtocolSpec(
        protocol=Protocol.MCP,
        shape=Shape.ENVELOPE,
        version="as carried on main",
        live=True,
        toggle_label="MCP",
        refused_step="",
        refusal_reason="",
    ),
    Protocol.UCP: ProtocolSpec(
        protocol=Protocol.UCP,
        shape=Shape.ENVELOPE,
        version="as carried on main",
        live=True,
        toggle_label="UCP",
        refused_step="direct-checkout-inside-AI completion",
        refusal_reason=(
            "Completion is a same-domain approve handoff, which is UCP's own buyer "
            "escalation path rather than a deviation from it. Every spend terminates in a "
            "fresh Authority at /agentic/approve."
        ),
    ),
    Protocol.ACP: ProtocolSpec(
        protocol=Protocol.ACP,
        shape=Shape.ENVELOPE,
        # Named, because "ACP conformant" unqualified ages badly against a
        # specification with five dated releases in under a year.
        version="2026-04-17",
        live=True,
        toggle_label="ACP",
        refused_step="completeCheckoutSession",
        refusal_reason=(
            "POST /checkout_sessions/{id}/complete hands the merchant a delegated payment "
            "credential (a Shared Payment Token or a vault token). That is exactly the "
            "authority we removed from agents (ADR-0008, ADR-0013), so the step is refused "
            "with a named code and an approve URL. The other four operations work normally."
        ),
    ),
    Protocol.AP2: ProtocolSpec(
        protocol=Protocol.AP2,
        shape=Shape.LAYER,
        version="main @ 2026-09-20",
        live=True,
        rides_on=Protocol.UCP,
        toggle_label="UCP+AP2",
        refused_step="Human Not Present (Autonomous)",
        refusal_reason=(
            "Autonomous mode is the `mandate` Authority kind, which is defined, registered "
            "and refused in v1 (ADR-0017). Human Present (Direct) completes through the "
            "approve ceremony, which is the Trusted Surface AP2's own specification "
            "describes."
        ),
    ),
}

#: The toggle, in the order it renders. AP2 appears as a layer on UCP because
#: that is what it is.
TOGGLE_ORDER: tuple[Protocol, ...] = (Protocol.MCP, Protocol.UCP, Protocol.AP2, Protocol.ACP)


def spec_for(protocol: Protocol) -> ProtocolSpec:
    return SPECS[protocol]


def toggle_labels() -> list[str]:
    return [SPECS[p].toggle_label for p in TOGGLE_ORDER]


def badge(protocol: Protocol, enabled_methods: frozenset[PaymentMethod]) -> dict[str, object]:
    """The conformance badge, naming its deviations inline.

    This is what keeps "conformant" from becoming a lie. It says which spec
    version it targets, that completion is redirect-only, which instruments this
    Merchant has actually enabled, and **which completion step this envelope
    wanted that we refuse and why**.
    """
    spec = SPECS[protocol]
    deviations: list[str] = [
        "Completion is redirect-only: every spend terminates in a fresh human Authority "
        "at /agentic/approve on the Merchant's own domain.",
        "Agent-held delegated payment credentials are refused by design, not unimplemented "
        "(ADR-0008, ADR-0013).",
    ]
    if spec.refused_step:
        deviations.append(f"{spec.refused_step}: {spec.refusal_reason}")

    return {
        "protocol": spec.protocol.value,
        "shape": spec.shape.value,
        "version": spec.version,
        "rides_on": spec.rides_on.value if spec.rides_on else None,
        "capability": "supported",
        "completion": "redirect-only",
        "payment_instruments_enabled": sorted(m.value for m in enabled_methods),
        "deviations": deviations,
    }
