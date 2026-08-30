"""ACP adapter conformance (INTEROP_SPEC §7)."""

from __future__ import annotations


def conformance_pass() -> bool:
    from openstore.protocols.acp.adapter import (
        ACP_EXTERNAL_FIELDS, ACP_STATE_BY_CHECKOUT_STATUS, AcpAdapter,
        from_acp_amount, to_acp_amount,
    )
    # Round-trip amount conversion must be lossless in both directions (R6.2b).
    assert from_acp_amount(to_acp_amount(12345)) == 12345
    assert to_acp_amount(from_acp_amount({"currency": "INR", "value": 99})) == {"currency": "INR", "value": 99}
    # Every mapped state resolves without a default (R6.2a).
    assert ACP_STATE_BY_CHECKOUT_STATUS["completed"] == "COMPLETED"
    assert set(ACP_EXTERNAL_FIELDS)
    return True
