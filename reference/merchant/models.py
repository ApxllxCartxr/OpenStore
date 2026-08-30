from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlmodel import SQLModel, Field, JSON, Column


class CheckoutStatus(str, Enum):
    PENDING = "PENDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    MANDATE_ISSUED = "MANDATE_ISSUED"
    POLICY_VERIFIED = "POLICY_VERIFIED"
    ORDER_CREATED = "ORDER_CREATED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    FAILED = "FAILED"

class AuthDescriptor(BaseModel):
    type: str = "oauth2"
    authorization_server: str
    scopes_supported: list[str]

class PolicyDescriptor(BaseModel):
    currency: str
    max_unconfirmed_spend_minor: int
    requires_human_approval: bool
    default_per_tx_cap_minor: int

class MerchantDescriptor(BaseModel):
    name: str
    id: str

class AgentCommerceDescriptor(BaseModel):
    version: str = "0.1"
    merchant: MerchantDescriptor
    storefront: str
    catalog_endpoint: str
    mcp_endpoint: str
    a2a_agent_card: str
    auth: AuthDescriptor
    policy: PolicyDescriptor

class MandateCart(BaseModel):
    hash: str
    version: int
    items: list[dict]  # {sku, qty, unit_minor} — tightened once checkout.py exists

class MandatePayload(BaseModel):
    iss: str          # merchant id
    jti: str          # mandate id, single-use
    aud: str = "openstore-mcp"
    chk: str          # checkout_id
    sub: str          # oauth client_id
    cart: MandateCart
    iat: int
    exp: int
    amt: int          # total, minor units
    cur: str = "INR"
    nonce: str
    dlv: str          # sha256 of canonical delivery address


class IntentPolicy(BaseModel):
    """The machine-readable policy a human signs via WebAuthn."""
    max_spend_minor: int              # maximum total for any single checkout
    allowed_tags: list[str]           # only items with at least one of these tags are permitted
    blocked_skus: list[str] = []      # explicitly forbidden items (belt-and-suspenders)
    merchant_id: str                  # lock to a specific merchant
    expires_at: int                   # Unix timestamp; policy is invalid after this


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(unique=True, index=True)
    name: str
    description: str
    price_minor: int
    currency: str = "INR"
    related_skus: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

class Cart(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    version: int = Field(default=1)
    items_json: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Checkout(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(unique=True, index=True)
    cart_id: int = Field(foreign_key="cart.id")
    client_id: str = Field(index=True)
    status: str = Field(default="PENDING")
    cart_hash: str
    total_minor: int
    delivery_address: str
    cart_snapshot_json: list[dict] = Field(default_factory=list, sa_column=Column(JSON))  # PoAI: compiler runs over this
    cart_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

class OTPChallenge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(index=True)
    otp_hash: str
    attempts: int = Field(default=0)
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Mandate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jti: str = Field(unique=True, index=True)
    checkout_id: str = Field(index=True)
    jws_compact: str
    fingerprint: str
    burned: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(index=True)
    razorpay_order_id: Optional[str] = None
    razorpay_payment_link_id: Optional[str] = None
    status: str = Field(default="CREATED")
    total_minor: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class WebhookEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    razorpay_event_id: str = Field(unique=True, index=True)
    event_type: str
    payload_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    processed_at: datetime = Field(default_factory=datetime.utcnow)

class OAuthClient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(unique=True, index=True)
    client_secret_hash: str
    display_name: str
    redirect_uris: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    active: bool = Field(default=True)

class OAuthToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jti: str = Field(unique=True, index=True)
    client_id: str = Field(index=True)
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    expires_at: datetime
    revoked: bool = Field(default=False)

class SpendLedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    amount_minor: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class IdempotencyRecord(SQLModel, table=True):
    __tablename__ = "ref_idempotencyrecord"
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    idempotency_key: str = Field(index=True)
    response_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

class IntentPolicyRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    credential_id: str = Field(unique=True, index=True)   # base64url-encoded
    public_key: str                                         # base64url-encoded COSE public key
    sign_count: int = Field(default=0)
    policy_json: dict = Field(default_factory=dict, sa_column=Column(JSON))  # IntentPolicy as dict
    policy_version: int = Field(default=1)                 # PoAI: 1 = legacy, 2 = v2
    user_id: str = Field(default="default-user", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = Field(default=True)

class PolicyChallenge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    challenge_id: str = Field(unique=True, index=True)     # base64url-encoded random nonce
    policy_hash: str                                         # SHA-256 of the canonical policy JSON
    state_json: dict = Field(default_factory=dict, sa_column=Column(JSON))  # fido2 server state (challenge, user_verification)
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AssertionRow(SQLModel, table=True):
    """A signed policy token: a WebAuthn assertion bound to a specific policy."""
    id: Optional[int] = Field(default=None, primary_key=True)
    credential_id: str = Field(index=True)                  # base64url credential ID
    assertion_json: dict = Field(default_factory=dict, sa_column=Column(JSON))  # {id, rawId, response: {clientDataJSON, authenticatorData, signature}, type}
    policy_json: dict = Field(default_factory=dict, sa_column=Column(JSON))     # the IntentPolicy that was signed
    nonce: str = Field(index=True)                          # base64url-encoded random nonce
    user_id: str = Field(default="default-user", index=True)  # merchant user who signed
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AuditLogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True)
    client_id: Optional[str] = None
    tool: str
    args_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result_summary: str
    success: bool
    latency_ms: float
    reason_code: Optional[str] = Field(default=None, index=True)  # PoAI: populated on every rejection
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------- PoAI evidence layer (IMPLEMENTATION_SPEC §2) ----------


class CatalogAttestation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True)
    price_minor: int
    tags_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    catalog_digest: str                 # "sha256:…" of the whole catalog at issue time
    jws_compact: str                    # ES256 JWS, payload per §4.1
    issued_at: datetime = Field(index=True)


class EvidenceBundle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bundle_id: str = Field(unique=True, index=True)
    checkout_id: str = Field(unique=True, index=True)
    aal_level: int = Field(index=True)          # 0..3
    root: str                                    # "sha256:…"
    bundle_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    anchor_type: str = Field(default="none")     # "rekor" | "merkle_daily" | "none"
    anchor_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    issued_at: datetime = Field(default_factory=datetime.utcnow)


class AgentSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_key: str = Field(unique=True, index=True)   # f"{client_id}:{policy_hash}"
    client_id: str = Field(index=True)
    policy_hash: str = Field(index=True)
    display_name: str
    frozen: bool = Field(default=False)
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class HoldRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    checkout_id: str = Field(unique=True, index=True)
    aal_level: int
    hold_seconds: int
    state: str = Field(default="HELD", index=True)   # HELD | RELEASED | CANCELLED
    holds_until_at: datetime = Field(index=True)
    cancel_token: str = Field(unique=True, index=True)  # single-use capability
    resolved_at: Optional[datetime] = None

