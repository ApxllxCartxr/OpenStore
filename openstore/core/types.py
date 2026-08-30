"""Protocol-free commerce core types (INTEROP_SPEC §2.1).

No protocol library is imported here. These are the only structures an adapter
may construct or receive from `openstore.core.api.CommerceCore`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str                       # stable caller id; for OAuth this is client_id
    display_name: str
    scopes: frozenset[str]
    protocol: str                      # closed set, §5.2
    protocol_session_id: str | None    # e.g. an ACP checkout_session id
    auth_method: str                   # "oauth2_bearer" | "http_message_signature"


@dataclass(frozen=True, slots=True)
class LineItemRequest:
    sku: str
    qty: int                          # price is NEVER accepted from a caller, in any protocol


@dataclass(frozen=True, slots=True)
class DeliveryAddress:
    raw: str


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ProductView:
    sku: str
    title: str
    unit_price_paise: int
    tags: Tuple[str, ...]
    available: bool


@dataclass(frozen=True, slots=True)
class CartView:
    cart_id: int
    items: Tuple[LineItemRequest, ...]
    total_minor: int


@dataclass(frozen=True, slots=True)
class CheckoutView:
    checkout_id: str
    cart_id: int
    total_minor: int
    required_aal: int
    delivery: DeliveryAddress
    expires_at_unix: int = 0  # PRODUCTION_READINESS §0.3 — checkout expiry enforcement


@dataclass(frozen=True, slots=True)
class HoldView:
    hold_id: str
    order_id: str
    level: int
    status: str
    expires_at_unix: int


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    checkout_id: str
    status: str                        # "ORDER_CREATED" | "HELD"
    payment_link_url: str
    aal_level: int
    hold: HoldView | None
    bundle_id: str


@dataclass(frozen=True, slots=True)
class OrderView:
    order_id: str
    checkout_id: str
    status: str
    total_minor: int
    aal_level: int


AuthorityScheme = str  # Literal closed to the five values in VALID_SCHEMES


@dataclass(frozen=True, slots=True)
class AuthorityPresentation:
    scheme: AuthorityScheme
    raw: Mapping[str, Any]             # the presentation, verbatim as received
    policy_json: Mapping[str, Any] | None  # v2 IntentPolicy, when the scheme carries one


VALID_SCHEMES: Tuple[str, ...] = (
    "native_webauthn",
    "ap2_intent_mandate",
    "ap2_cart_mandate",
    "acp_delegated_token",
    "none",
)


PROTOCOL_SET: Tuple[str, ...] = ("mcp", "acp", "ap2", "a2a", "internal")

SCOPE_VOCABULARY: Tuple[str, ...] = (
    "catalog:read",
    "cart:write",
    "checkout:initiate",
    "checkout:confirm",
)


def normalize_scopes(raw: Tuple[str, ...]) -> frozenset[str]:
    """Map any protocol's scope strings onto OpenStore's vocabulary (R2.1b)."""
    out: set[str] = set()
    for s in raw:
        low = s.lower()
        for v in SCOPE_VOCABULARY:
            if v in low or low in v:
                out.add(v)
    return frozenset(out)
