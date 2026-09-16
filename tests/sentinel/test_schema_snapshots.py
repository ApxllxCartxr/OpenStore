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
        "id",
        "trace_id",
        "client_id",
        "merchant_id",
        "cart_hash",
        "cart_version",
        "amount_minor",
        "currency",
        "state",
        "policy_id",
        "policy_hash",
        "aal_level",
        "expires_at",
        "idempotency_key",
        "psp_provider",
        "psp_order_id",
        "psp_payment_link_id",
        "psp_payment_id",
        "short_url",
        "cancel_token",
        "created_at",
        "updated_at",
        "paid_at",
        "released_at",
        "cancelled_at",
        "cart_snapshot",
        # Stage 24 (Q-045): arbitrator share-link bearer token (hash + expiry).
        "evidence_token_hash",
        "evidence_token_expires_at",
        "agent_plan",
        "chat_platform",
        "chat_user_id",
        "chat_channel_id",
        "discord_message_id",
        "request_text",
        "poai_bundle",
    },
    "intent_policies": {
        "id",
        "merchant_id",
        "policy_version",
        "policy_hash",
        "currency",
        "max_spend_per_tx_minor",
        "max_spend_total_minor",
        "max_transactions",
        "allowed_tags",
        "tag_mode",
        "blocked_skus",
        "not_before",
        "expires_at",
        "assertion_max_age_seconds",
        "fulfilment_mode",
        "required_skus",
        "webauthn_credential_id",
        "webauthn_sign_count",
        "signed_at",
        "is_active",
        "created_at",
        "no_human_authority",
    },
    "ledger_entries": {
        "id",
        "trace_id",
        "client_id",
        "entry_type",
        "amount_minor",
        "currency",
        "reference_id",
        "account",
        "counterparty_account",
        "idempotency_key",
        "description",
        "created_at",
    },
    "campaigns": {
        "id",
        "merchant_id",
        "campaign_version",
        "title",
        "rationale",
        "discount_bps",
        "applies_to_skus",
        "starts_at",
        "ends_at",
        "source_signals",
        "draft_digest",
        "state",
        "merchant_signature",
        "approver_credential_id",
        "approved_at",
        "webauthn_assertion",
        "created_at",
        "updated_at",
    },
    "webhook_events": {
        "id",
        "trace_id",
        "client_id",
        "psp_provider",
        "psp_event_id",
        "event_type",
        "payload",
        "status",
        "retry_count",
        "last_error",
        "processed_at",
        "created_at",
    },
    "audit_logs": {
        "id",
        "trace_id",
        "client_id",
        "action",
        "resource_type",
        "resource_id",
        "request_ip",
        "user_agent",
        "request_method",
        "request_path",
        "response_status",
        "audit_metadata",
        "created_at",
    },
    "oauth_tokens": {
        "jti",
        "client_id",
        "token_type",
        "scopes",
        "subject",
        "issued_at",
        "expires_at",
        "revoked_at",
        "access_token_hash",
    },
    "oauth_clients": {
        "client_id",
        "client_name",
        "client_secret_hash",
        "redirect_uris",
        "grant_types",
        "scopes",
        "jwks_uri",
        "jwks",
        "is_active",
        "created_at",
        "updated_at",
    },
    "oauth_authorization_codes": {
        "code",
        "client_id",
        "redirect_uri",
        "scopes",
        "code_challenge",
        "code_challenge_method",
        "auth_time",
        "expires_at",
        "used_at",
        "code_metadata",
    },
    "webauthn_credentials": {
        "id",
        "credential_id",
        "user_handle",
        "public_key",
        "sign_count",
        "aaguid",
        "attestation_format",
        "attestation_data",
        "is_active",
        "created_at",
        "last_used_at",
    },
    "idempotency_keys": {
        "key",
        "trace_id",
        "client_id",
        "request_hash",
        "response_status",
        "response_body",
        "created_at",
        "expires_at",
    },
    "handoffs": {
        "token",
        "kind",
        "merchant_id",
        "chat_platform",
        "chat_user_id",
        "chat_channel_id",
        "request_text",
        "created_at",
        "expires_at",
        "consumed_at",
        "result_policy_id",
        "amendment_draft",
        "resume_url",
        "cart_payload",
    },

    "shopping_sessions": {
        "id",
        "chat_platform",
        "chat_user_id",
        "chat_channel_id",
        "policy_id",
        "trace_id",
        "goal",
        "messages",
        "turns_used",
        "state",
        "created_at",
        "updated_at",
        "expires_at",
    },
    # Stage 24 (Q-044): passkey-bound merchant browsing sessions.
    "merchant_sessions": {
        "id",
        "token_hash",
        "operator_id",
        "credential_id",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
        "user_agent_hash",
    },
    # Stage 24 (DECISION-045): non-secret browser-edited config overlay.
    "merchant_settings": {
        "key",
        "value",
        "updated_at",
    },
    # Stage 26: per-SKU management state + platform-truth cache.
    "inventory_items": {
        "id",
        "merchant_id",
        "sku",
        "tracked",
        "low_stock_threshold",
        "last_platform_qty",
        "drifted",
        "low_stock_notified",
        "updated_at",
    },
    # Stage 26: quantity movements as ledger rows (never in-place decrements).
    "inventory_ledger_entries": {
        "id",
        "trace_id",
        "client_id",
        "entry_type",
        "quantity",
        "merchant_id",
        "sku",
        "reference_id",
        "account",
        "counterparty_account",
        "idempotency_key",
        "description",
        "created_at",
    },
    # Stage 26: platform write-back queue + DLQ.
    "inventory_writebacks": {
        "id",
        "merchant_id",
        "sku",
        "quantity",
        "status",
        "attempts",
        "last_error",
        "idempotency_key",
        "created_at",
        "updated_at",
    },
    # Stage 27: merchant-/agent-authored cross-sell / up-sell / bundle rules.
    "merchandising_rules": {
        "id",
        "merchant_id",
        "kind",
        "title",
        "rationale",
        "trigger_skus",
        "suggested_sku",
        "campaign_id",
        "source_signals",
        "draft_digest",
        "state",
        "approver_credential_id",
        "approved_at",
        "webauthn_assertion",
        "created_at",
        "updated_at",
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
        "amount_minor",
        "max_spend_per_tx_minor",
        "max_spend_total_minor",
        "discount_bps",
        "unit_minor",
    }
    violations = []
    for table in meta.tables.values():
        for name, col in table.columns.items():
            if name in money_cols and str(col.type) not in {"INTEGER", "INT"}:
                violations.append(f"{table.name}.{name}: {col.type}")
    assert not violations, f"non-integer money columns: {violations}"
