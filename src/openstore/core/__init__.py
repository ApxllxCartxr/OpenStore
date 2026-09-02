# OpenStore core — deterministic spine (NO LLM imports permitted)

from __future__ import annotations

from .api import (
    CommerceError,
    cancel_hold_flow,
    confirm_checkout,
    create_checkout,
    get_checkout,
    run_sweepers,
    verify_checkout_evidence,
)
from .audit import AuditContext, audit_log, get_audit_trail
from .compiler import (
    CompilerContext,
    CompilerResult,
    compile_decision,
    get_compiler_digest,
    get_compiler_version,
)
from .database import (
    check_spend_cap,
    compute_policy_exposure,
    compute_policy_spend,
    get_engine,
    get_or_create_checkout,
    get_session,
    immediate_session,
    init_database,
    session_scope,
    update_checkout_state,
)
from .holdcancel import (
    AALLevel,
    calculate_expires_at,
    cancel_hold,
    check_and_expire_checkouts,
    compute_aal_level,
    get_aal_liability_sentence,
    get_hold_duration,
    initiate_hold,
    refund_checkout,
    release_hold,
)
from .idempotency import (
    IdempotencyError,
    check_idempotency,
    cleanup_expired_idempotency_keys,
    compute_request_hash,
    generate_idempotency_key,
    store_idempotency_result,
)
from .ledger import (
    LedgerError,
    create_capture_entry,
    create_refund_entry,
    create_release_entry,
    create_reserve_entry,
    get_ledger_balance,
    verify_ledger_balances,
)
from .oauth import (
    OAuthError,
    create_authorization_code,
    create_token_pair,
    get_jwks,
    register_client,
    revoke_token,
    validate_access_token,
    validate_authorization_code,
    validate_client,
)
from .poai import (
    SECTION_ORDER,
    build_aal_section,
    build_hash_chain,
    build_time_anchor,
    canonical_json_bytes,
    compute_aal_level_from_bundle,
    create_poai_bundle,
    evaluate_aal_predicates,
    hash_section,
    sign_merchant_jws_compact,
    verify_poai_bundle,
)
from .webauthn_rp import (
    WebAuthnError,
    begin_assertion,
    begin_registration,
    complete_assertion,
    complete_registration,
    create_policy_signing_challenge,
    get_user_credentials,
)
from .webhooks import (
    WebhookError,
    handle_razorpay_payment_captured,
    handle_razorpay_payment_failed,
    process_webhook_event,
    process_webhook_retry_queue,
    verify_razorpay_signature,
)

__all__ = [
    # api
    "CommerceError",
    "create_checkout",
    "get_checkout",
    "confirm_checkout",
    "cancel_hold_flow",
    "verify_checkout_evidence",
    "run_sweepers",
    # compiler
    "CompilerContext",
    "CompilerResult",
    "compile_decision",
    "get_compiler_version",
    "get_compiler_digest",
    # database
    "get_engine",
    "init_database",
    "get_session",
    "session_scope",
    "immediate_session",
    "check_spend_cap",
    "compute_policy_spend",
    "compute_policy_exposure",
    "get_or_create_checkout",
    "update_checkout_state",
    # holdcancel
    "AALLevel",
    "compute_aal_level",
    "get_hold_duration",
    "get_aal_liability_sentence",
    "calculate_expires_at",
    "initiate_hold",
    "release_hold",
    "cancel_hold",
    "refund_checkout",
    "check_and_expire_checkouts",
    # idempotency
    "IdempotencyError",
    "compute_request_hash",
    "check_idempotency",
    "store_idempotency_result",
    "generate_idempotency_key",
    "cleanup_expired_idempotency_keys",
    # ledger
    "LedgerError",
    "create_reserve_entry",
    "create_capture_entry",
    "create_release_entry",
    "create_refund_entry",
    "get_ledger_balance",
    "verify_ledger_balances",
    # oauth
    "OAuthError",
    "register_client",
    "validate_client",
    "create_authorization_code",
    "validate_authorization_code",
    "create_token_pair",
    "validate_access_token",
    "revoke_token",
    "get_jwks",
    # poai
    "canonical_json_bytes",
    "hash_section",
    "build_hash_chain",
    "create_poai_bundle",
    "verify_poai_bundle",
    "SECTION_ORDER",
    "build_aal_section",
    "build_time_anchor",
    "compute_aal_level_from_bundle",
    "evaluate_aal_predicates",
    "sign_merchant_jws_compact",
    # webauthn_rp
    "WebAuthnError",
    "begin_registration",
    "complete_registration",
    "begin_assertion",
    "complete_assertion",
    "get_user_credentials",
    "create_policy_signing_challenge",
    # webhooks
    "WebhookError",
    "verify_razorpay_signature",
    "process_webhook_event",
    "handle_razorpay_payment_captured",
    "handle_razorpay_payment_failed",
    "process_webhook_retry_queue",
    # audit
    "audit_log",
    "AuditContext",
    "get_audit_trail",
]
