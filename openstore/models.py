from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field as SQLField, Session, select
from sqlmodel import SQLModel

from openstore.core import signing


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# ---------- Discovery ----------


class MerchantInfo(BaseModel):
    legal_name: str
    country: str
    website: Optional[str] = None
    support_contact: Optional[str] = None


class TrustAnchor(BaseModel):
    did: str
    daily_seed_public: bool = True
    bundle_endpoint: Optional[str] = None
    anchor_policy: Literal["per-day", "ad-hoc", "both"] = "per-day"
    explorer_url: Optional[str] = None


class Capabilities(BaseModel):
    currencies: List[str] = ["INR"]
    per_transaction_limit_paise: int
    daily_limit_paise: int
    supported_mandate_scopes: List[str] = ["global", "category", "sku"]
    agent_self_reports: bool = False
    languages: List[str] = ["en"]


class DiscoveryDoc(BaseModel):
    version: int = 1
    transport: Literal["mcp"] = "mcp"
    merchant: MerchantInfo
    trust_anchor: TrustAnchor
    supported_actions: List[str]
    capabilities: Capabilities


# ---------- Mandate ----------


class Mandate(BaseModel):
    mandate_id: str
    merchant_did: str
    payer: str
    scope: str
    max_amount_paise: int
    currency: str
    expires_at: int
    jws: str

    @classmethod
    def create(
        cls,
        *,
        private_key,
        mandate_id: str,
        merchant_did: str,
        payer: str,
        scope: str,
        max_amount_paise: int,
        currency: str,
        expires_at: int,
    ) -> "Mandate":
        payload = {
            "mandate_id": mandate_id,
            "merchant_did": merchant_did,
            "payer": payer,
            "scope": scope,
            "max_amount_paise": max_amount_paise,
            "currency": currency,
            "expires_at": expires_at,
        }
        digest = signing.sha256_of_json(payload)
        jws = signing.sign_digest(private_key, digest, kid=payer)
        return cls(
            mandate_id=mandate_id,
            merchant_did=merchant_did,
            payer=payer,
            scope=scope,
            max_amount_paise=max_amount_paise,
            currency=currency,
            expires_at=expires_at,
            jws=jws,
        )

    def verify(self, public_key) -> None:
        payload = {
            "mandate_id": self.mandate_id,
            "merchant_did": self.merchant_did,
            "payer": self.payer,
            "scope": self.scope,
            "max_amount_paise": self.max_amount_paise,
            "currency": self.currency,
            "expires_at": self.expires_at,
        }
        claimed = signing.verify_jws(self.jws, public_key)
        if claimed != signing.sha256_of_json(payload):
            raise ValueError("mandate signature mismatch")


# ---------- Quote ----------


class QuoteItem(BaseModel):
    sku: str
    title: str = ""
    quantity: int
    unit_price_paise: int = 0
    tax_paise: int = 0
    catalog_attestation: Optional[str] = None


class Quote(BaseModel):
    quote_id: str
    merchant_did: str
    items: List[QuoteItem]
    subtotal_paise: int
    tax_paise: int = 0
    total_paise: int
    currency: str
    valid_until: int
    mandate_required: bool = True
    available_actions: List[str] = []


# ---------- Policy (inherent constraints) ----------


class Policy(BaseModel):
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    blocked_skus: List[str] = Field(default_factory=list)
    spend_limit_paise: Optional[int] = None
    allowed_scopes: List[str] = Field(default_factory=lambda: ["global", "category", "sku"])
    allowed_countries: List[str] = Field(default_factory=list)
    requires_payer_consent: bool = False
    vendor_attestation_required: bool = False
    approval_required_above_paise: Optional[int] = None


# ---------- Trust Receipt / Ledger ----------


class ReceiptKind(str, Enum):
    ORDER = "order"
    QUOTE = "quote"
    CANCEL = "cancel"
    RETURN = "return"
    REFUND = "refund"
    MANDATE = "mandate"
    RELEASE = "release"
    SETTLEMENT = "settlement"
    DISPUTE = "dispute"


class TrustReceipt(BaseModel):
    receipt_id: str
    kind: ReceiptKind
    ts: int = Field(default_factory=_now)
    actor_did: str
    envelope: dict
    payload_ref: str
    merkle_proof: Optional[List] = None
    daily_anchor_ref: Optional[str] = None
    statements: List[str] = Field(default_factory=list)


# ---------- Dispute / Release / Return ----------


class Dispute(BaseModel):
    dispute_id: str
    order_id: str
    raised_by: str
    reason: str
    evidence_refs: List[str] = Field(default_factory=list)
    status: Literal["open", "accepted", "rejected"] = "open"


class ReleaseRequest(BaseModel):
    order_id: str
    amount_paise: int
    released_by: str


class ReturnRequest(BaseModel):
    order_id: str
    items: List[str]
    reason: str


# ---------- §2 persistence tables (created by the Ledger engine) ----------


class CatalogAttestationRow(SQLModel, table=True):
    """One ES256 catalog attestation per (sku, price_minor, tags)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    sku: str = SQLField(index=True)
    price_minor: int
    tags_json: str = "[]"
    catalog_digest: str
    iat: int
    jws: str


class EvidenceBundleRow(SQLModel, table=True):
    """Persisted PoAI evidence bundle for an order (§6)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    bundle_id: str = SQLField(index=True, unique=True)
    order_id: str = SQLField(index=True)
    merchant_did: str = SQLField(index=True)
    poai_version: str
    root: str
    time_anchor_json: str = "null"
    bundle_json: str


class AgentSessionRow(SQLModel, table=True):
    """Merchant operator / agent session for the Agent Console (§9.2)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    session_key: str = SQLField(index=True, unique=True)
    client_id: str
    scopes: str = "[]"
    created_at: int
    last_seen: int
    frozen: bool = False


class HoldRecordRow(SQLModel, table=True):
    """Hold & Cancel state machine record (§9.3)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    cancel_token: str = SQLField(index=True, unique=True)
    order_id: str = SQLField(index=True)
    aal_level: int
    hold_seconds: int
    created_at: int
    expires_at: int
    status: str = SQLField(default="HELD")  # HELD | RELEASED | CANCELLED


# ---------- DELEGATION_AND_ORCHESTRATION §5.4 ----------


class DelegationLinkRow(SQLModel, table=True):
    """One signed delegation link (§3.1)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    link_id: str = SQLField(index=True, unique=True)
    parent_link_id: Optional[str] = SQLField(default=None, index=True)
    envelope_id: str = SQLField(index=True)
    depth: int
    delegate_thumbprint: str = SQLField(index=True)
    grant_json: dict = SQLField(default_factory=dict, sa_column=Column(JSON))
    jws_compact: str
    revoked: bool = SQLField(default=False, index=True)
    issued_at: int


class SpendChainEntry(SQLModel, table=True):
    """One spend-chain entry (§3.3)."""

    __table_args__ = (UniqueConstraint("envelope_id", "sequence"),)

    id: Optional[int] = SQLField(default=None, primary_key=True)
    envelope_id: str = SQLField(index=True)
    sequence: int
    entry_type: str
    amount_minor: int
    ref: str
    prev_link: str
    link: str = SQLField(index=True, unique=True)
    jws_compact: str
    issued_at: int


# ---------- PRODUCTION_READINESS §1.1 Idempotency ----------


class IdempotencyRecord(SQLModel, table=True):
    """Idempotency record for create_order."""

    __table_args__ = (UniqueConstraint("client_id", "idempotency_key"),)

    id: Optional[int] = SQLField(default=None, primary_key=True)
    client_id: str = SQLField(index=True)
    idempotency_key: str = SQLField(index=True)
    request_fingerprint: str
    state: str = "IN_FLIGHT"
    response_json: Optional[str] = SQLField(default=None, sa_column=Column(JSON))
    status_code: Optional[int] = None
    created_at: int = SQLField(default_factory=_now)
    expires_at: int


# ---------- PRODUCTION_READINESS §1.2 Dual-write / Outbox ----------


class PspIntent(SQLModel, table=True):
    """PSP intent record for dual-write pattern."""

    __table_args__ = (UniqueConstraint("checkout_id"),)

    id: Optional[int] = SQLField(default=None, primary_key=True)
    checkout_id: str = SQLField(index=True, unique=True)
    order_id: Optional[str] = SQLField(default=None, index=True)
    amount_minor: int
    currency: str
    reference_id: str
    state: str = "PENDING"
    psp_payment_id: Optional[str] = None
    psp_response_json: Optional[str] = SQLField(default=None, sa_column=Column(JSON))
    created_at: int = SQLField(default_factory=_now)
    updated_at: int = SQLField(default_factory=_now)
    attempts: int = 0


# ---------- PRODUCTION_READINESS §1.3 Double-entry Ledger ----------


class LedgerEntryType(str, Enum):
    RESERVE = "RESERVE"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    REFUND = "REFUND"


class LedgerEntry(SQLModel, table=True):
    """Double-entry ledger entry (PRODUCTION_READINESS §1.3).

    Append-only journal with typed entries. Never UPDATE a ledger row.
    Balance derived from: RESERVE + CAPTURE - RELEASE - REFUND
    """

    __table_args__ = (UniqueConstraint("checkout_id", "entry_type", "sequence"),)

    id: Optional[int] = SQLField(default=None, primary_key=True)
    entry_type: str  # RESERVE | CAPTURE | RELEASE | REFUND
    checkout_id: str = SQLField(index=True)
    policy_hash: str = SQLField(index=True)
    client_id: str = SQLField(index=True)
    amount_minor: int  # always positive; entry_type carries the sign
    sequence: int = 0  # sequence per checkout_id for ordering
    created_at: int = SQLField(default_factory=_now)
    metadata_json: Optional[str] = SQLField(default=None, sa_column=Column(JSON))


def available_minor(session: Session, client_id: str, window_h: int = 24) -> int:
    """Calculate available budget for a client within a time window.

    RESERVE and CAPTURE consume budget; RELEASE and REFUND return it.
    """
    from sqlmodel import select
    cutoff = _now() - (window_h * 3600)
    entries = session.exec(
        select(LedgerEntry).where(
            LedgerEntry.client_id == client_id,
            LedgerEntry.created_at >= cutoff,
        )
    ).all()

    balance = 0
    for entry in entries:
        if entry.entry_type in (LedgerEntryType.RESERVE, LedgerEntryType.CAPTURE):
            balance += entry.amount_minor
        elif entry.entry_type in (LedgerEntryType.RELEASE, LedgerEntryType.REFUND):
            balance -= entry.amount_minor
    return max(0, -balance)  # negative balance means available


# ---------- PRODUCTION_READINESS §0.8 Rate Limiting ----------


class RateLimitBucket(SQLModel, table=True):
    """Token bucket for rate limiting (PRODUCTION_READINESS §0.8).

    Per (client_id, tool) bucket stored in database.
    Survives restarts and works across workers.
    """

    __table_args__ = (UniqueConstraint("client_id", "tool"),)

    id: Optional[int] = SQLField(default=None, primary_key=True)
    client_id: str = SQLField(index=True)
    tool: str = SQLField(index=True)
    tokens: float
    capacity: int
    refill_rate: float
    last_refill: int


# ---------- PRODUCTION_READINESS §0.8 Audit/Reason Codes ----------


class RejectionRecord(SQLModel, table=True):
    """Rejection record with reason_code, client_id, trace_id (PRODUCTION_READINESS §0.8)."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    order_id: str = SQLField(index=True)
    reason_code: str = SQLField(index=True)
    detail: str
    client_id: str = SQLField(index=True)
    trace_id: str = SQLField(index=True)
    created_at: int = SQLField(default_factory=_now)


# ---------- Webhook event storage (PRODUCTION_READINESS §1.4) ----------


class WebhookEventRow(SQLModel, table=True):
    """Persisted raw webhook event for replay tooling."""

    id: Optional[int] = SQLField(default=None, primary_key=True)
    event_id: str = SQLField(index=True, unique=True)
    event_type: str = SQLField(index=True)
    payload_json: str
    created_at: int = SQLField(default_factory=_now)
    processed: bool = SQLField(default=False)

