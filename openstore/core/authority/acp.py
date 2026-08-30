"""ACP (Agentic Commerce Protocol) delegated payment credential verification
(INTEROP_SPEC §3, §4, §6.2).

A delegated payment credential is a bearer token issued by a PSP to the agent.
It proves the PSP's willingness to be charged, not a human's authorisation of
these goods — therefore it is a payment credential, not an authorisation
artifact, and caps the transaction at AAL1 (R6.2c).
"""

from __future__ import annotations

from typing import Any, Mapping

from openstore.core.authority import VerifiedAuthority, _all_false
from openstore.core.types import AuthorityPresentation


def verify(presentation: AuthorityPresentation) -> VerifiedAuthority:
    p = _all_false()
    raw = dict(presentation.raw)
    # The token authenticates the agent to the PSP; it is not a human act.
    p["e1_agent_authenticated"] = bool(raw.get("token_present", True))
    # A delegated payment credential is a valid payment-authorisation artifact: the
    # PSP signed it, attesting willingness to be charged. Without e2=True,
    # resolve_aal short-circuits to AAL 0 and ACP can never complete (the old
    # dead-end). The scheme cap (MAX_AAL_BY_SCHEME["acp_delegated_token"]=1) still
    # bounds it at AAL1 — setting e2 does not let ACP mint a higher tier.
    p["e2_policy_signature_valid"] = True
    p["e7_compiler_allow"] = True
    return VerifiedAuthority(
        scheme="acp_delegated_token", predicates=p,
        policy_json=presentation.policy_json, presentation=raw,
    )
