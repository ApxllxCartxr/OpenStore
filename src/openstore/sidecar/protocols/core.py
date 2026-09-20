"""What every envelope shares: one core decision, translated four ways.

The single most valuable property in this phase is that the **core Transcript
bytes are identical across all four protocols**. Envelopes differ; core decision
bytes do not. This module is where that is made true rather than hoped for —
every translator calls `run_core` and none of them may reach past it.

No translator carries pricing logic. UCP's totals breakdown maps 1:1 onto the
Quote, ACP's totals come from the same place, and a translator that computed
anything would be a second money path (SPEC §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openstore.sidecar.core.codes import Protocol, ReasonCode
from openstore.sidecar.gate.decide import Decision, DecisionInput, Gate, GateRefused


@dataclass
class CoreResult:
    """One decision, plus the bytes every envelope must agree on."""

    decision: Decision | None
    transcript_bytes: bytes
    reason_code: ReasonCode | None = None
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.reason_code is not None


async def run_core(gate: Gate, request: DecisionInput, *, dry_run: bool = False) -> CoreResult:
    """The one path. Every translator goes through here.

    A refusal is a result rather than an exception at this boundary, because an
    envelope has to render it — but the Transcript is produced either way, which
    is what makes the byte-identity assertion meaningful on refusals too.
    """
    try:
        decision = await gate.decide(request, dry_run=dry_run)
    except GateRefused as refusal:
        return CoreResult(
            decision=None,
            transcript_bytes=refusal.transcript.to_bytes(),
            reason_code=refusal.reason_code,
            detail=refusal.detail,
        )
    return CoreResult(decision=decision, transcript_bytes=decision.transcript.to_bytes())


def approve_url(merchant_domain: str, tap_token: str) -> str:
    """The same-domain handoff every protocol completes through.

    Same domain because passkeys and tap tokens are bound to the origin the
    Consumer is looking at, and a handoff to anywhere else would be asking them
    to approve a spend on a site they did not choose.
    """
    return f"https://{merchant_domain}/agentic/approve?t={tap_token}"


def refusal_envelope(protocol: Protocol, code: ReasonCode, detail: str) -> dict[str, Any]:
    """A refusal, shaped for whoever is reading.

    The reason code is the same across every envelope — it is core, not
    translation — and only the wrapper differs.
    """
    return {
        "protocol": protocol.value,
        "error": {"code": code.value, "detail": detail},
    }
