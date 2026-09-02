# tests/sentinel/test_schema_snapshots.py
# S10.3 — Schema snapshots: SQLModel table definitions are pinned. Any drift in a
# pinned table's columns, nullability, or indexes fails the build. Update this file
# deliberately ONLY when a PRD change legally alters the schema (never to mask drift).

from __future__ import annotations

import openstore.models  # noqa: F401  (registers table=True models on metadata)
from sqlmodel import SQLModel

# Pinned column sets per table (name -> required). A column present in the model
# but not listed here, or non-nullability flipping, is a schema drift.
EXPECTED_COLUMNS = {
    "checkouts": {
        "id", "trace_id", "client_id", "merchant_id", "cart_hash", "cart_version",
        "amount_minor", "currency", "state", "policy_id", "policy_hash", "aal_level",
        "expires_at", "idempotency_key", "psp_provider", "psp_order_id",
        "psp_payment_link_id", "short_url", "cancel_token", "created_at", "updated_at", "paid_at",
        "released_at", "cancelled_at", "cart_snapshot", "agent_plan",
    },
    "intent_policies": {
        "id", "merchant_id", "policy_version", "policy_hash", "currency",
        "max_spend_per_tx_minor", "max_spend_total_minor", "max_transactions",
        "allowed_tags", "tag_mode", "blocked_skus", "not_before", "expires_at",
        "assertion_max_age_seconds", "fulfilment_mode", "required_skus",
        "webauthn_credential_id", "webauthn_sign_count", "signed_at", "is_active", "created_at",
        "no_human_authority",
    },
    "ledger_entries": {
        "id", "trace_id", "client_id", "entry_type", "amount_minor", "currency",
        "reference_id", "account", "counterparty_account", "idempotency_key",
        "description", "created_at",
    },
    "campaigns": {
        "id", "merchant_id", "campaign_version", "title", "rationale", "discount_bps",
        "applies_to_skus", "starts_at", "ends_at", "source_signals", "draft_digest",
        "state", "merchant_signature", "approver_credential_id", "approved_at",
        "webauthn_assertion", "created_at", "updated_at",
    },
    "webhook_events": {
        "id", "trace_id", "client_id", "psp_provider", "psp_event_id", "event_type",
        "payload", "status", "retry_count", "last_error", "processed_at", "created_at",
    },
    "audit_logs": {
        "id", "trace_id", "client_id", "action", "resource_type", "resource_id",
        "request_ip", "user_agent", "request_method", "request_path", "response_status",
        "audit_metadata", "created_at",
    },
    "oauth_tokens": {
        "jti", "client_id", "token_type", "scopes", "subject", "issued_at", "expires_at",
        "revoked_at", "access_token_hash",
    },
    "oauth_clients": {
        "client_id", "client_name", "client_secret_hash", "redirect_uris", "grant_types",
        "scopes", "jwks_uri", "jwks", "is_active", "created_at", "updated_at",
    },
    "oauth_authorization_codes": {
        "code", "client_id", "redirect_uri", "scopes", "code_challenge",
        "code_challenge_method", "auth_time", "expires_at", "used_at", "code_metadata",
    },
    "webauthn_credentials": {
        "id", "credential_id", "user_handle", "public_key", "sign_count", "aaguid",
        "attestation_format", "attestation_data", "is_active", "created_at", "last_used_at",
    },
    "idempotency_keys": {
        "key", "trace_id", "client_id", "request_hash", "response_status",
        "response_body", "created_at", "expires_at",
    },
}


def test_registry_schema_snapshot():
    """Column sets of pinned tables must not drift from the snapshot."""
    meta = SQLModel.metadata
    errors = []
    for table_name, expected in EXPECTED_COLUMNS.items():
        if table_name not in meta.tables:
            errors.append(f"missing table: {table_name}")
            continue
        actual = set(meta.tables[table_name].columns.keys())
        if actual != expected:
            errors.append(
                f"{table_name}: added={sorted(actual - expected)} "
                f"removed={sorted(expected - actual)}"
            )

    # Tables that must NOT silently become tracked SQLModel tables.
    undeclared = set(meta.tables.keys()) - set(EXPECTED_COLUMNS)
    if undeclared:
        errors.append(f"undeclared tables present: {sorted(undeclared)}")

    assert not errors, "\n".join(errors)


def test_money_columns_are_integer_minor_units():
    """Money is integer paise only — a float money column is a build-breaker."""
    meta = SQLModel.metadata
    money_cols = {
        "amount_minor", "max_spend_per_tx_minor", "max_spend_total_minor",
        "discount_bps", "unit_minor",
    }
    violations = []
    for table in meta.tables.values():
        for name, col in table.columns.items():
            if name in money_cols and str(col.type) not in {"INTEGER", "INT"}:
                violations.append(f"{table.name}.{name}: {col.type}")
    assert not violations, f"non-integer money columns: {violations}"
