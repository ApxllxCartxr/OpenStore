"""Structured error envelope with namespaces (PRODUCTION_READINESS §1.6).

Error codes: auth.*, policy.*, checkout.*, psp.*, ratelimit.*
Envelope: {"error": {"code", "message", "retriable", "trace_id", "details"}}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True, slots=True)
class OpenStoreError(Exception):
    """Base exception with structured error envelope."""

    code: str
    message: str
    retriable: bool = False
    trace_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.trace_id is None:
            object.__setattr__(self, "trace_id", uuid.uuid4().hex[:16])

    def to_envelope(self) -> Dict[str, Any]:
        """Convert to structured error envelope."""
        envelope = {
            "error": {
                "code": self.code,
                "message": self.message,
                "retriable": self.retriable,
                "trace_id": self.trace_id,
            }
        }
        if self.details:
            envelope["error"]["details"] = self.details
        return envelope


# ---------- Error code constants ----------

# auth.* namespace
AUTH_UNAUTHENTICATED = "auth.unauthenticated"
AUTH_FORBIDDEN = "auth.forbidden"
AUTH_CSRF = "auth.csrf"
AUTH_SESSION_EXPIRED = "auth.session_expired"
AUTH_INVALID_CREDENTIALS = "auth.invalid_credentials"

# policy.* namespace (compiler verdicts)
POLICY_SPEND_CAP_EXCEEDED = "policy.spend_cap_exceeded"
POLICY_TAG_VIOLATION = "policy.tag_violation"
POLICY_BLOCKED_SKU = "policy.blocked_sku"
POLICY_MERCHANT_MISMATCH = "policy.merchant_mismatch"
POLICY_EXPIRED = "policy.expired"
POLICY_CURRENCY_MISMATCH = "policy.currency_mismatch"
POLICY_DUPLICATE_SKU = "policy.duplicate_sku"
POLICY_EMPTY_CART = "policy.empty_cart"
POLICY_SPEND_ENVELOPE_EXCEEDED = "policy.spend_envelope_exceeded"
POLICY_LEGACY_VERSION = "policy.legacy_version"

# checkout.* namespace
CHECKOUT_INVALID = "checkout.invalid"
CHECKOUT_EXPIRED = "checkout.expired"
CHECKOUT_SNAPSHOT_CORRUPT = "checkout.snapshot_corrupt"
CHECKOUT_MANDATE_EXPIRED = "checkout.mandate_expired"
CHECKOUT_MANDATE_EXCEEDED = "checkout.mandate_exceeded"
CHECKOUT_IDEMPOTENCY_KEY_REUSE = "checkout.idempotency_key_reuse"
CHECKOUT_IDEMPOTENCY_IN_PROGRESS = "checkout.idempotency_in_progress"
CHECKOUT_NOT_FOUND = "checkout.not_found"

# psp.* namespace
PSP_TIMEOUT = "psp.timeout"
PSP_DECLINED = "psp.declined"
PSP_UNAVAILABLE = "psp.unavailable"
PSP_DUPLICATE_REFERENCE = "psp.duplicate_reference"
PSP_WEBHOOK_SIGNATURE_INVALID = "psp.webhook_signature_invalid"
PSP_WEBHOOK_STALE = "psp.webhook_stale"

# ratelimit.* namespace
RATELIMIT_EXCEEDED = "ratelimit.exceeded"
RATELIMIT_QUOTA_EXHAUSTED = "ratelimit.quota_exhausted"

# hold.* namespace
HOLD_INVALID = "hold.invalid"
HOLD_EXPIRED = "hold.expired"
HOLD_ALREADY_CANCELLED = "hold.already_cancelled"

# evidence.* namespace
EVIDENCE_NOT_FOUND = "evidence.not_found"

# orchestration.* namespace
ORCHESTRATION_MERCHANT_UNAVAILABLE = "orchestration.merchant_unavailable"
ORCHESTRATION_DEADLINE_TOO_SHORT = "orchestration.deadline_too_short"
ORCHESTRATION_MULTIPLE_NONCOMPENSABLE = "orchestration.multiple_noncompensable_legs"
ORCHESTRATION_COMPENSATION_FAILED = "orchestration.compensation_failed"
ORCHESTRATION_FULFILMENT_MODE_VIOLATED = "orchestration.fulfilment_mode_violated"

# delegation.* namespace
DELEGATION_ROOT_NOT_HUMAN_SIGNED = "delegation.root_not_human_signed"
DELEGATION_LINK_SIGNATURE_INVALID = "delegation.link_signature_invalid"
DELEGATION_LINK_ORDER_INVALID = "delegation.link_order_invalid"
DELEGATION_DEPTH_EXCEEDED = "delegation.depth_exceeded"
DELEGATION_BUDGET_NOT_ATTENUATING = "delegation.budget_not_attenuating"
DELEGATION_MERCHANT_NOT_ATTENUATING = "delegation.merchant_not_attenuating"
DELEGATION_TAG_NOT_ATTENUATING = "delegation.tag_not_attenuating"
DELEGATION_EXPIRY_NOT_ATTENUATING = "delegation.expiry_not_attenuating"
DELEGATION_TX_COUNT_NOT_ATTENUATING = "delegation.tx_count_not_attenuating"
DELEGATION_ENVELOPE_OVERLAPS_SIBLING = "delegation.envelope_overlaps_sibling"

# spendchain.* namespace
SPENDCHAIN_GENESIS_MISMATCH = "spendchain.genesis_mismatch"
SPENDCHAIN_SEQUENCE_GAP = "spendchain.sequence_gap"
SPENDCHAIN_PREV_LINK_MISMATCH = "spendchain.prev_link_mismatch"
SPENDCHAIN_FORK_DETECTED = "spendchain.fork_detected"
SPENDCHAIN_ENVELOPE_OVERSPENT = "spendchain.envelope_overspent"

# verifier.* namespace
VERIFIER_SCHEMA_INVALID = "verifier.schema_invalid"
VERIFIER_CHAIN_BROKEN = "verifier.chain_broken"
VERIFIER_MERCHANT_SIGNATURE_INVALID = "verifier.merchant_signature_invalid"
VERIFIER_TIME_ANCHOR_INVALID = "verifier.time_anchor_invalid"
VERIFIER_WEBAUTHN_INVALID = "verifier.webauthn_invalid"
VERIFIER_CHALLENGE_BINDING_MISMATCH = "verifier.challenge_binding_mismatch"
VERIFIER_UV_FLAG_MISMATCH = "verifier.uv_flag_mismatch"
VERIFIER_ATTESTATION_INVALID = "verifier.attestation_invalid"
VERIFIER_COMPILER_DIGEST_UNSUPPORTED = "verifier.unsupported_compiler_digest"
VERIFIER_TRANSCRIPT_MISMATCH = "verifier.transcript_mismatch"
VERIFIER_VERDICT_MISMATCH = "verifier.verdict_mismatch"
VERIFIER_AMOUNT_MISMATCH = "verifier.amount_mismatch"
VERIFIER_AAL_MISMATCH = "verifier.aal_mismatch"


# ---------- Helper functions ----------


def error_envelope(code: str, message: str, retriable: bool = False, trace_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a structured error envelope."""
    if trace_id is None:
        trace_id = uuid.uuid4().hex[:16]
    envelope = {
        "error": {
            "code": code,
            "message": message,
            "retriable": retriable,
            "trace_id": trace_id,
        }
    }
    if details:
        envelope["error"]["details"] = details
    return envelope


def is_retriable(code: str) -> bool:
    """Check if an error code is retriable."""
    retriable_prefixes = ("psp.timeout", "psp.unavailable", "ratelimit.")
    return any(code.startswith(p) for p in retriable_prefixes)


# ---------- Exception classes for common errors ----------


class AuthenticationError(OpenStoreError):
    def __init__(self, message: str = "Authentication required", trace_id: Optional[str] = None):
        super().__init__(AUTH_UNAUTHENTICATED, message, retriable=False, trace_id=trace_id)


class ForbiddenError(OpenStoreError):
    def __init__(self, message: str = "Access forbidden", trace_id: Optional[str] = None):
        super().__init__(AUTH_FORBIDDEN, message, retriable=False, trace_id=trace_id)


class CSRFError(OpenStoreError):
    def __init__(self, message: str = "CSRF token mismatch", trace_id: Optional[str] = None):
        super().__init__(AUTH_CSRF, message, retriable=False, trace_id=trace_id)


class PolicyError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class CheckoutError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class PSPError(OpenStoreError):
    def __init__(self, code: str, message: str, retriable: bool = True, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=retriable, trace_id=trace_id, details=details)


class RateLimitError(OpenStoreError):
    def __init__(self, message: str = "Rate limit exceeded", details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(RATELIMIT_EXCEEDED, message, retriable=True, trace_id=trace_id, details=details)


class HoldError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class OrchestrationError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class DelegationError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class SpendChainError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)


class VerifierError(OpenStoreError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        super().__init__(code, message, retriable=False, trace_id=trace_id, details=details)