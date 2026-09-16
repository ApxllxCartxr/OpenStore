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


class InventoryEntryType(str, enum.Enum):
    """Stage 26: inventory quantity movements. Mirrors LedgerEntryType's
    lifecycle vocabulary for stock: RESERVE (checkout hold) -> COMMIT (paid)
    or RELEASE (cancel/fail/expiry); RESTOCK reverses a COMMIT on refund.
    Not tracked in REGISTRY.json/test_enum_exhaustiveness (that sentinel pins
    order/campaign/ledger enums only) — enforced here via the SQLEnum column,
    mirroring HandoffKind."""

    RESERVE = "RESERVE"
    COMMIT = "COMMIT"
    RELEASE = "RELEASE"
    RESTOCK = "RESTOCK"


class InventoryWritebackStatus(str, enum.Enum):
    """Stage 26: platform stock write-back intent lifecycle. pending intents
    are delivered by the inventory sync loop; failed rows are the DLQ (alert
    raised, retried each tick); skipped rows target adapters with no
    STOCK_WRITE capability (flat files have no write API — never a failure);
    delivered rows are terminal."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


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


class MerchandisingKind(str, enum.Enum):
    """Stage 27: what a merchandising rule suggests. CROSS_SELL names a
    companion SKU, UPGRADE names a better (usually pricier) SKU with an
    honest delta, BUNDLE names a SKU whose discount comes from a Campaign
    row (DECISION-048: no second discount path). Not in REGISTRY's pinned
    enum set (order/campaign/ledger only) — SQLEnum-enforced here,
    HandoffKind precedent."""

    CROSS_SELL = "CROSS_SELL"
    UPGRADE = "UPGRADE"
    BUNDLE = "BUNDLE"


class WebhookStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class HandoffKind(str, enum.Enum):
    """Closed set (Q-014, Q-033): which out-of-band ceremony a handoff is
    parking a chat conversation for. Not tracked in
    REGISTRY.json/test_enum_exhaustiveness (that sentinel only pins
    order/campaign/ledger enums) — enforced here via the SQLEnum column,
    which rejects any other value at the DB layer."""

    POLICY = "policy"
    AMENDMENT = "amendment"
    CART = "cart"


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
    psp_payment_id: str | None = Field(default=None, max_length=64)
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
    # Q-032a: the Discord message id carrying the "Pay here" embed, so the
    # webhook worker / hold loop can edit it in place ("Pay here" ->
    # "Payment received") instead of leaving a stale pay link. Nullable:
    # non-chat and pre-migration checkouts never set it; editing is always
    # best-effort (the DM remains the source of truth).
    discord_message_id: str | None = Field(default=None, max_length=64)

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

    # Stage 24 (Q-045): arbitrator share-link bearer token. SHA-256 hex of the
    # raw token (never the token itself — handoff discipline); null means no
    # share link is live for this checkout. Revoked by nulling both columns
    # from /merchant/orders.
    evidence_token_hash: str | None = Field(default=None, max_length=64, index=True)
    evidence_token_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )

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

    id: int | None = Field(default=None, primary_key=True)
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

    id: int | None = Field(default=None, primary_key=True)
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

    # S12 step 8: where to POST a best-effort "some buyer may have finished
    # signing" ping once this handoff is consumed. Nullable — absent means
    # either the legacy single-process path (studio.py resumes in-process
    # via resume_after_signing) or a federated buyer that hasn't opted in.
    # Carries no authority: see surfaces/buyer_internal.py for why a forged
    # or replayed call here must gain an attacker nothing.
    resume_url: str | None = Field(default=None, max_length=1024)

    # S16 (Q-033): for kind=CART handoffs, the pending cart awaiting a
    # per-cart passkey tap: {"cart_id", "cart", "cart_hash", "policy_id"}.
    # Null for kind=POLICY/AMENDMENT handoffs (which use result_policy_id /
    # amendment_draft respectively).
    cart_payload: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    __table_args__ = (Index("ix_handoff_chat_user", "chat_platform", "chat_user_id"),)


class ShoppingSessionState(str, enum.Enum):
    """Closed set: a conversational-shopping session's lifecycle. Unlike
    Handoff (resumed by a web link click), a ShoppingSession is resumed by
    the buyer's next chat message — see core/shopping_session.py."""

    AWAITING_REPLY = "AWAITING_REPLY"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ShoppingSession(SQLModel, table=True):
    """S13: parks a buyer/LLM shopping conversation between Discord messages
    when BuyerGraph's agent_step asks the buyer something instead of
    answering (e.g. "no vanilla, but we have chocolate — want that?"). The
    buyer's next message (no command prefix needed) resumes it via
    BuyerGraph.converse() with the accumulated `messages` transcript. Short
    TTL (see expires_at) — this is an active chat exchange, not an async
    out-of-band ceremony like Handoff."""

    __tablename__ = "shopping_sessions"

    id: str = Field(primary_key=True, max_length=64)  # "sess_" + secrets.token_hex
    chat_platform: str = Field(max_length=32)
    chat_user_id: str = Field(max_length=64, index=True)
    chat_channel_id: str = Field(max_length=64)
    policy_id: str = Field(max_length=64)
    trace_id: str = Field(max_length=64)
    goal: str = Field(max_length=4096)  # original request, for reference
    messages: list[dict[str, Any]] = Field(sa_column=Column(JSON, nullable=False))
    turns_used: int = Field(default=0)
    state: ShoppingSessionState = Field(
        sa_column=Column(SQLEnum(ShoppingSessionState), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))

    __table_args__ = (
        Index("ix_shopping_session_chat_user", "chat_platform", "chat_user_id", "chat_channel_id"),
    )


# Stage 24 (Q-044/Q-045): passkey-bound merchant browsing sessions + non-secret config overlay
class MerchantSession(SQLModel, table=True):
    """Stage 24 (Q-044): a passkey-authenticated merchant-operator browsing
    session. Proves WHICH operator is browsing — it is NOT money authority;
    every money-moving action still requires a fresh WebAuthn assertion
    exactly as today. The raw cookie token is never persisted (SHA-256 only,
    mirroring how Handoff treats its token)."""

    __tablename__ = "merchant_sessions"

    id: str = Field(primary_key=True, max_length=64)  # uuid4 hex
    token_hash: str = Field(max_length=64, unique=True, index=True)  # sha256 hex
    operator_id: str = Field(max_length=64, index=True)
    credential_id: str = Field(max_length=256)  # which WebAuthn credential authenticated it
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    last_seen_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    user_agent_hash: str | None = Field(default=None, max_length=64)

    __table_args__ = (Index("ix_merchant_session_operator", "operator_id"),)


class MerchantSetting(SQLModel, table=True):
    """Stage 24 (DECISION-045): non-secret config overlay edited from the
    browser. Overlays the YAML at load time so a browser edit never rewrites
    a file that may be templated or read-only in a container. Secrets never
    live here (presence/absence only in the UI)."""

    __tablename__ = "merchant_settings"

    key: str = Field(primary_key=True, max_length=128)
    value: str = Field(max_length=4096)
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )


class InventoryItem(SQLModel, table=True):
    """Stage 26: per-SKU stock management state. This row is the sync loop's
    cache of platform truth plus merchant intent — it is NEVER decremented
    by a sale. Quantity movements are inventory_ledger_entries rows only
    (the money ledger's discipline, for the same reason: an auditable,
    idempotent movement history instead of a racy counter).

    A SKU with NO row here is unmanaged (the `stock: None` world) and passes
    every gate. A row with tracked=false opts out explicitly (made-to-order,
    services). Only tracked=true rows gate sales."""

    __tablename__ = "inventory_items"

    id: int | None = Field(default=None, primary_key=True)
    merchant_id: str = Field(max_length=64, index=True)
    sku: str = Field(max_length=128, index=True)
    tracked: bool = Field(default=True)
    low_stock_threshold: int = Field(default=5, ge=0)
    last_platform_qty: int | None = Field(default=None, ge=0)
    drifted: bool = Field(default=False)
    low_stock_notified: bool = Field(default=False)
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_inventory_merchant_sku", "merchant_id", "sku", unique=True),
    )


class InventoryLedgerEntry(SQLModel, table=True):
    """Stage 26: inventory quantity movements, mirroring LedgerEntry's shape
    (trace/client, typed entry, idempotency_key unique, account pair,
    description, created_at). Units move between STATES, so every movement
    is a double-entry pair: RESERVE (available->reserved), COMMIT
    (reserved->sold), RELEASE (reserved->available), RESTOCK (sold->available).
    Quantities are always positive; the sign is implied by entry_type, exactly
    like amount_minor. merchant_id/sku ride on the row (unlike the money
    ledger's reference_id->Checkout join) because the oversell gate runs
    BEFORE any checkout row exists."""

    __tablename__ = "inventory_ledger_entries"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, max_length=64)
    client_id: str = Field(index=True, max_length=64)
    entry_type: InventoryEntryType = Field(
        sa_column=Column(SQLEnum(InventoryEntryType), nullable=False)
    )
    quantity: int = Field(gt=0)
    merchant_id: str = Field(max_length=64, index=True)
    sku: str = Field(max_length=128, index=True)
    reference_id: str = Field(max_length=64, index=True)  # checkout_id
    account: str = Field(max_length=32)  # "available" | "reserved" | "sold"
    counterparty_account: str = Field(max_length=32)
    idempotency_key: str = Field(max_length=160, unique=True, index=True)
    description: str = Field(max_length=512)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_inventory_ledger_trace_client", "trace_id", "client_id"),
        Index("ix_inventory_ledger_merchant_sku", "merchant_id", "sku"),
        Index("ix_inventory_ledger_reference_type", "reference_id", "entry_type"),
    )


class InventoryWriteback(SQLModel, table=True):
    """Stage 26: platform stock write-back intents + DLQ in one table. A COMMIT
    inserts a PENDING row (same transaction — exactly-once intent); the sync
    loop delivers it via the stock adapter's STOCK_WRITE. Failures flip to
    FAILED (the DLQ: alert raised, retried each tick, attempts counted);
    adapters with no STOCK_WRITE mark rows SKIPPED (flat files have no write
    API — never a failure, never an alert)."""

    __tablename__ = "inventory_writebacks"

    id: int | None = Field(default=None, primary_key=True)
    merchant_id: str = Field(max_length=64, index=True)
    sku: str = Field(max_length=128, index=True)
    quantity: int  # SIGNED delta to apply to platform stock (negative = sold)
    status: InventoryWritebackStatus = Field(
        default=InventoryWritebackStatus.PENDING,
        sa_column=Column(SQLEnum(InventoryWritebackStatus), nullable=False),
    )
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = Field(default=None, max_length=1024)
    idempotency_key: str = Field(max_length=160, unique=True, index=True)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime, nullable=False)
    )

    __table_args__ = (
        Index("ix_inventory_writeback_status", "status"),
        Index("ix_inventory_writeback_merchant_sku", "merchant_id", "sku"),
    )


class MerchandisingRule(SQLModel, table=True):
    """Stage 27: a merchant- or agent-authored cross-sell / up-sell / bundle
    rule. Mirrors Campaign's lifecycle (DRAFT -> PENDING_APPROVAL -> ACTIVE ->
    PAUSED / EXPIRED / REJECTED), approval assertion fields, and auditability;
    adds the rule itself (kind, trigger_skus, suggested_sku) and a nullable
    campaign_id through which BUNDLE discounts flow (DECISION-048).

    Selection is LLM-free and deterministic (core/merchandising.py); an LLM
    may word the surface copy and nothing else. A rule takes effect only via
    a passkey assertion bound to {"mode": "merchandising", "rule_id": ...}."""

    __tablename__ = "merchandising_rules"

    id: str = Field(primary_key=True, max_length=64)  # rule_id
    merchant_id: str = Field(max_length=64, index=True)
    kind: MerchandisingKind = Field(
        sa_column=Column(SQLEnum(MerchandisingKind), nullable=False)
    )
    title: str = Field(max_length=256)
    rationale: str = Field(max_length=2048)
    trigger_skus: list[str] = Field(sa_column=Column(JSON, nullable=False))
    suggested_sku: str = Field(max_length=128)
    campaign_id: str | None = Field(default=None, max_length=64)
    source_signals: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    draft_digest: str = Field(max_length=64)
    state: CampaignState = Field(
        default=CampaignState.DRAFT, sa_column=Column(SQLEnum(CampaignState), nullable=False)
    )
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
        Index("ix_merchandising_merchant_state", "merchant_id", "state"),
    )

