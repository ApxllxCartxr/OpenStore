# OpenStore database models — SQLModel schemas for money path

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, LargeBinary
from sqlalchemy import Enum as SQLEnum
from sqlmodel import JSON, Column, Field, Index, SQLModel


def _utcnow() -> datetime:
    """Naive UTC now, usable as a SQLModel default_factory.

    Replaces the deprecated datetime.utcnow() without changing semantics: the
    SQL DateTime columns are naive on SQLite, so a tz-aware value would break
    aware-vs-naive comparisons after a DB round-trip.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class LedgerEntryType(str, enum.Enum):
    RESERVE = "RESERVE"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    REFUND = "REFUND"


class OrderState(str, enum.Enum):
    CREATED = "CREATED"
    HELD = "HELD"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class CampaignState(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class WebhookStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class HandoffKind(str, enum.Enum):
    """Closed set (Q-014): which out-of-band ceremony a handoff is parking a
    chat conversation for. Not tracked in REGISTRY.json/test_enum_exhaustiveness
    (that sentinel only pins order/campaign/ledger enums) — enforced here via
    the SQLEnum column, which rejects any other value at the DB layer."""

    POLICY = "policy"
    AMENDMENT = "amendment"


class LedgerEntry(SQLModel, table=True):
    __tablename__ = "ledger_entries"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, max_length=64)
    client_id: str = Field(index=True, max_length=64)
    entry_type: LedgerEntryType = Field(sa_column=Column(SQLEnum(LedgerEntryType), nullable=False))
    amount_minor: int = Field(ge=0)  # paise, non-negative
    currency: str = Field(max_length=3, default="INR")
    reference_id: str = Field(max_length=64, index=True)  # checkout_id
    account: str = Field(max_length=64)  # e.g., "customer_hold", "merchant_revenue", "platform_fee"
    counterparty_account: str = Field(max_length=64)  # double-entry pair
    idempotency_key: str = Field(max_length=128, unique=True, index=True)
    description: str = Field(max_length=512)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_ledger_trace_client", "trace_id", "client_id"),
        Index("ix_ledger_reference_type", "reference_id", "entry_type"),
    )


class Checkout(SQLModel, table=True):
    __tablename__ = "checkouts"

    id: str = Field(primary_key=True, max_length=64)  # checkout_id, UUID
    trace_id: str = Field(index=True, max_length=64)
    client_id: str = Field(index=True, max_length=64)
    merchant_id: str = Field(max_length=64)
    cart_hash: str = Field(max_length=64)
    cart_version: int
    amount_minor: int = Field(ge=0)
    currency: str = Field(max_length=3, default="INR")
    state: OrderState = Field(
        default=OrderState.CREATED, sa_column=Column(SQLEnum(OrderState), nullable=False)
    )
    policy_id: str | None = Field(default=None, max_length=64)
    policy_hash: str | None = Field(default=None, max_length=64)
    aal_level: int = Field(default=0, ge=0, le=3)
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    idempotency_key: str = Field(max_length=128, unique=True, index=True)
    psp_provider: str | None = Field(default=None, max_length=32)
    psp_order_id: str | None = Field(default=None, max_length=64)
    psp_payment_link_id: str | None = Field(default=None, max_length=64)
    short_url: str | None = Field(default=None, max_length=512)
    cancel_token: str | None = Field(default=None, max_length=64, unique=True)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    paid_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    released_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    cancelled_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))

    # Chat identity (S11 Phase 3 / Q-018): nullable, since not every checkout
    # is chat-originated (API/test-created checkouts leave these unset). Set
    # once, at checkout_initiate time, so the webhook worker and the
    # hold-release loop know which Discord user to DM. Mirrors Handoff's
    # chat_platform/chat_user_id/chat_channel_id fields exactly.
    chat_platform: str | None = Field(default=None, max_length=32)
    chat_user_id: str | None = Field(default=None, max_length=64, index=True)
    chat_channel_id: str | None = Field(default=None, max_length=64)

    # Cart snapshot for INV-1
    cart_snapshot: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))

    # Agent plan (optional, for PoAI)
    agent_plan: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    # S11 Phase 4 (Q-020): the original chat goal text, so a completed
    # purchase's evidence bundle can populate human_intent (PoAI e8). Nullable
    # since not every checkout is chat-originated (mirrors chat_platform et
    # al. from Q-018). Set once, at checkout_initiate time, alongside the
    # chat-identity columns.
    request_text: str | None = Field(default=None, max_length=4096)

    # S11 Phase 4 (Q-020): the PoAI evidence bundle produced when this
    # checkout reaches RELEASED, persisted so /orders/{checkout_id}/evidence
    # can serve it back. Null until a bundle has been produced (e.g. a
    # non-chat checkout, or one not yet RELEASED).
    poai_bundle: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    __table_args__ = (
        Index("ix_checkout_trace_client", "trace_id", "client_id"),
        Index("ix_checkout_merchant_state", "merchant_id", "state"),
    )


class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_keys"

    key: str = Field(primary_key=True, max_length=128)
    trace_id: str = Field(max_length=64, index=True)
    client_id: str = Field(max_length=64, index=True)
    request_hash: str = Field(max_length=64)  # hash of request body
    response_status: int
    response_body: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))

    __table_args__ = (Index("ix_idempotency_trace_client", "trace_id", "client_id"),)


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_events"

    id: int = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, max_length=64)
    client_id: str = Field(max_length=64, index=True)
    psp_provider: str = Field(max_length=32)
    psp_event_id: str = Field(max_length=128, unique=True, index=True)
    event_type: str = Field(max_length=64)
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: WebhookStatus = Field(
        default=WebhookStatus.RECEIVED, sa_column=Column(SQLEnum(WebhookStatus), nullable=False)
    )
    retry_count: int = Field(default=0, ge=0)
    last_error: str | None = Field(default=None, max_length=1024)
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_webhook_trace_client", "trace_id", "client_id"),
        Index("ix_webhook_status_created", "status", "created_at"),
    )


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: int = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, max_length=64)
    client_id: str = Field(index=True, max_length=64)
    action: str = Field(max_length=64)
    resource_type: str = Field(max_length=64)
    resource_id: str | None = Field(default=None, max_length=64)
    request_ip: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=512)
    request_method: str | None = Field(default=None, max_length=10)
    request_path: str | None = Field(default=None, max_length=256)
    response_status: int | None = Field(default=None)
    audit_metadata: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_audit_trace_client", "trace_id", "client_id"),
        Index("ix_audit_action_created", "action", "created_at"),
    )


class OAuthClient(SQLModel, table=True):
    __tablename__ = "oauth_clients"

    client_id: str = Field(primary_key=True, max_length=64)
    client_name: str = Field(max_length=256)
    client_secret_hash: str | None = Field(default=None, max_length=128)  # for confidential clients
    redirect_uris: list[str] = Field(sa_column=Column(JSON, nullable=False))
    grant_types: list[str] = Field(sa_column=Column(JSON, nullable=False))
    scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    jwks_uri: str | None = Field(default=None, max_length=512)
    jwks: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )


class OAuthAuthorizationCode(SQLModel, table=True):
    __tablename__ = "oauth_authorization_codes"

    code: str = Field(primary_key=True, max_length=128)
    client_id: str = Field(index=True, max_length=64)
    redirect_uri: str = Field(max_length=512)
    scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    code_challenge: str | None = Field(default=None, max_length=128)
    code_challenge_method: str | None = Field(default=None, max_length=16)
    auth_time: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime, nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    used_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    code_metadata: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class OAuthToken(SQLModel, table=True):
    __tablename__ = "oauth_tokens"

    jti: str = Field(primary_key=True, max_length=64)
    client_id: str = Field(index=True, max_length=64)
    token_type: str = Field(max_length=32)  # "access_token" or "refresh_token"
    scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    subject: str | None = Field(default=None, max_length=256)
    issued_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime, nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    access_token_hash: str | None = Field(default=None, max_length=128)


class WebAuthnCredential(SQLModel, table=True):
    __tablename__ = "webauthn_credentials"

    id: int = Field(default=None, primary_key=True)
    credential_id: str = Field(max_length=256, unique=True, index=True)
    user_handle: str = Field(max_length=64, index=True)  # merchant_id or admin user
    public_key: bytes = Field(
        sa_column=Column(LargeBinary, nullable=False)
    )  # COSE-encoded public key
    sign_count: int = Field(default=0, ge=0)
    aaguid: str | None = Field(default=None, max_length=36)
    attestation_format: str | None = Field(default=None, max_length=64)
    attestation_data: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    last_used_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))


class IntentPolicy(SQLModel, table=True):
    __tablename__ = "intent_policies"

    id: str = Field(primary_key=True, max_length=64)
    merchant_id: str = Field(max_length=64, index=True)
    policy_version: int = Field(default=2)
    policy_hash: str = Field(max_length=64)
    currency: str = Field(max_length=3, default="INR")
    max_spend_per_tx_minor: int = Field(ge=0)
    max_spend_total_minor: int = Field(ge=0)
    max_transactions: int = Field(ge=0)
    allowed_tags: list[str] = Field(sa_column=Column(JSON, nullable=False))
    tag_mode: str = Field(max_length=4, default="all")
    blocked_skus: list[str] = Field(sa_column=Column(JSON, nullable=False))
    not_before: int  # Unix seconds
    expires_at: int  # Unix seconds
    assertion_max_age_seconds: int = Field(default=86400, ge=0)
    no_human_authority: bool = Field(default=False)
    fulfilment_mode: str = Field(max_length=32, default="all_or_nothing")
    required_skus: list[str] = Field(sa_column=Column(JSON, nullable=False))
    webauthn_credential_id: str = Field(max_length=256)
    webauthn_sign_count: int
    signed_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (Index("ix_policy_merchant_active", "merchant_id", "is_active"),)


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"

    id: str = Field(primary_key=True, max_length=64)  # campaign_id
    merchant_id: str = Field(max_length=64, index=True)
    campaign_version: int = Field(default=1)
    title: str = Field(max_length=256)
    rationale: str = Field(max_length=2048)
    discount_bps: int = Field(ge=0, le=10000)
    applies_to_skus: list[str] = Field(sa_column=Column(JSON, nullable=False))
    starts_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    source_signals: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    draft_digest: str = Field(max_length=64)
    state: CampaignState = Field(
        default=CampaignState.DRAFT, sa_column=Column(SQLEnum(CampaignState), nullable=False)
    )
    merchant_signature: str | None = Field(default=None, max_length=256)
    approver_credential_id: str | None = Field(default=None, max_length=256)
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    webauthn_assertion: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_campaign_merchant_state", "merchant_id", "state"),
        Index("ix_campaign_window", "starts_at", "ends_at"),
    )


class Handoff(SQLModel, table=True):
    """S11 Phase 2 (Q-014): a durable, single-use, token-addressed row that
    parks a chat conversation while a human completes an out-of-band ceremony
    (policy signing, amendment approval) in a browser, then resumes it.
    One table serves every ceremony kind (see HandoffKind) — no bespoke
    one-off callback tables."""

    __tablename__ = "handoffs"

    token: str = Field(primary_key=True, max_length=64)  # secrets.token_urlsafe(32)
    kind: HandoffKind = Field(sa_column=Column(SQLEnum(HandoffKind), nullable=False))
    merchant_id: str = Field(max_length=64, index=True)
    chat_platform: str = Field(max_length=32)
    chat_user_id: str = Field(max_length=64, index=True)
    chat_channel_id: str = Field(max_length=64)
    request_text: str = Field(max_length=4096)  # becomes human_intent (PoAI)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    result_policy_id: str | None = Field(default=None, max_length=64)

    # S11 Phase 4 (Q-020): for kind=AMENDMENT handoffs, the drafted amendment
    # (MerchantAgent.draft_amendment's output) plus the cart it was drafted
    # against — draft_amendment() itself returns no persistence hook, and the
    # cart must survive the round-trip from drafting to approval. Null for
    # kind=POLICY handoffs.
    amendment_draft: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    __table_args__ = (Index("ix_handoff_chat_user", "chat_platform", "chat_user_id"),)
