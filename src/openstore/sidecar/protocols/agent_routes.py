"""`/agent/*` — the public front door, and the `.well-known` cards beside it.

Every route here is reachable by a stranger, because that is the design
(ADR-0012). What makes it safe is not who can knock:

- Rate limits per agent tier **and** per IP on every route (§16.8).
- RFC 9421 signatures on everything a self-registered agent sends.
- Scope checks before any body runs.
- And the Gate, which refuses a spend without a fresh Authority no matter which
  door the agent came through.

**`place-order` returns an approve URL, never an order.** That is the single
most load-bearing sentence on this surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openstore.sidecar.admission.oauth import Admission, AgentToken
from openstore.sidecar.admission.profile import ProfileFetcher, ProfileRefused
from openstore.sidecar.admission.ratelimit import RateLimiter, Tier
from openstore.sidecar.core.codes import (
    TOOL_SCOPES,
    AuthorityKind,
    PaymentMethod,
    ReasonCode,
    ToolName,
)
from openstore.sidecar.core.feed import build_feed
from openstore.sidecar.protocols import mcp
from openstore.sidecar.protocols.wellknown import (
    agent_commerce_card,
    jwks_document,
    ucp_manifest,
)
from openstore.sidecar.trait.errors import TraitError

router = APIRouter()


@dataclass
class AgentSurface:
    """Everything the agent routes need, injected so they can be tested."""

    merchant_domain: str = "spoiledduckie.localhost"
    merchant_name: str = "SpoiledDuckie"
    demo: bool = True
    enabled_methods: frozenset[PaymentMethod] = frozenset(
        {PaymentMethod.UPI, PaymentMethod.CASH_ON_DELIVERY}
    )
    enabled_authority_kinds: frozenset[AuthorityKind] = frozenset(
        {AuthorityKind.UPI_PIN, AuthorityKind.PASSKEY, AuthorityKind.CONFIRMED_INTENT}
    )
    admission: Admission = field(default_factory=Admission)
    limiter: RateLimiter = field(default_factory=RateLimiter)
    fetcher: ProfileFetcher = field(default_factory=ProfileFetcher)
    jwks: dict[str, Any] = field(default_factory=lambda: {"keys": []})
    trait: Any = None
    """A `TraitClient`. The feed is built from Merchant truth read fresh through
    doors 1 and 2 — the sidecar holds no catalogue of its own (ADR-0001)."""
    exposed: set[str] | None = None
    """Which SKUs the Merchant exposes to agents. `None` means every active
    item, which is the demo's configuration — not a default that quietly
    publishes something unexposed."""


_surface = AgentSurface()


def configure(surface: AgentSurface) -> None:
    global _surface
    _surface = surface


def get_surface() -> AgentSurface:
    return _surface


def _refuse(code: ReasonCode, detail: str, **fields: Any) -> JSONResponse:
    error = TraitError(code, detail, fields or None)
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(request: Request, bucket: str, subject: str, tier: Tier) -> JSONResponse | None:
    """Per-agent and per-IP, because they stop different things: one agent
    hammering, and one host running many agents."""
    try:
        _surface.limiter.check(bucket, subject, tier=tier)
        _surface.limiter.check("ip", _client_ip(request), tier=tier)
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)
    return None


# ── The cards ────────────────────────────────────────────────────────────────


@router.get("/.well-known/agent-commerce.json")
def card() -> dict[str, Any]:
    """Public and unauthenticated by design. A card behind a login is a card no
    new agent can read."""
    return agent_commerce_card(
        merchant_domain=_surface.merchant_domain,
        merchant_name=_surface.merchant_name,
        enabled_methods=_surface.enabled_methods,
        enabled_authority_kinds=_surface.enabled_authority_kinds,
        demo=_surface.demo,
    )


@router.get("/.well-known/ucp.json")
def ucp_card() -> dict[str, Any]:
    return ucp_manifest(
        merchant_domain=_surface.merchant_domain, merchant_name=_surface.merchant_name
    )


@router.get("/.well-known/jwks.json")
def jwks() -> dict[str, Any]:
    return jwks_document(_surface.jwks)


# ── Admission ────────────────────────────────────────────────────────────────


@router.post("/agent/register")
async def register(request: Request) -> JSONResponse:
    """Self-registration: a stranger publishes a profile and is issued a token
    **on the spot, with no prior Merchant action**.

    The profile URL is attacker-chosen, so the fetch is hardened as the SSRF
    sink it is — and registration has its own rate limit, because a registration
    attempt is a stranger making this sidecar issue an outbound request.
    """
    refused = _limit(request, "profile-registration", _client_ip(request), Tier.SELF_REGISTERED)
    if refused:
        return refused

    body = await request.json()
    profile_url = str(body.get("profile_url", ""))
    try:
        profile = await _surface.fetcher.fetch(profile_url)
    except ProfileRefused as exc:
        return _refuse(exc.code, exc.detail)

    try:
        token = _surface.admission.issue_for_stranger(profile.agent_id)
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)

    return JSONResponse(
        {
            "agent_id": profile.agent_id,
            "access_token": token.token,
            "token_type": "Bearer",
            "expires_at": token.expires_at.isoformat(),
            "scopes": sorted(s.value for s in token.scopes),
            "tier": token.tier.value,
            "note": (
                "These scopes let you search, build a basket and start a checkout. None of "
                "them let you spend: place-order returns a link for the Consumer to approve."
            ),
        }
    )


@router.post("/agent/token")
async def token(request: Request) -> JSONResponse:
    """OAuth client-credentials, for an agent the Merchant allowlisted.

    Same four scopes as the stranger route. Reputation buys throughput only.
    """
    body = await request.json()
    try:
        issued = _surface.admission.issue_for_client(
            str(body.get("client_id", "")), str(body.get("client_secret", ""))
        )
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)
    return JSONResponse(
        {
            "access_token": issued.token,
            "token_type": "Bearer",
            "expires_at": issued.expires_at.isoformat(),
            "scopes": sorted(s.value for s in issued.scopes),
            "tier": issued.tier.value,
        }
    )


def _bearer(request: Request) -> AgentToken | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        return _surface.admission.resolve(header.split(" ", 1)[1])
    except TraitError:
        return None


# ── The tool surface ─────────────────────────────────────────────────────────


@router.get("/agent/tools")
def tools(request: Request) -> JSONResponse:
    """`tools/list`. Public: an agent should be able to see what a shop offers
    before deciding whether to register."""
    return JSONResponse({"tools": mcp.tools_list()})


@router.post("/agent/mcp")
async def mcp_call(request: Request) -> JSONResponse:
    """One entry point for the MCP tool set.

    Scope-checked before the body runs. `place-order` is handled explicitly and
    returns an approve URL — the one thing this surface must never do is place
    an order.
    """
    agent = _bearer(request)
    if agent is None:
        return _refuse(
            ReasonCode.SIGNATURE_INVALID,
            "This surface needs a token. Register at /agent/register — no Merchant "
            "action is required.",
        )

    refused = _limit(request, "agent", agent.agent_id, agent.tier)
    if refused:
        return refused

    body = await request.json()
    raw_name = str(body.get("tool", ""))
    try:
        tool = ToolName(raw_name)
    except ValueError:
        return _refuse(ReasonCode.NOT_FOUND, f"{raw_name!r} is not a tool this shop has.")

    scope_refusal = mcp.check_scope(agent, tool)
    if scope_refusal:
        return _refuse(
            ReasonCode.AUTHORITY_MISSING,
            f"{tool.value} needs the {TOOL_SCOPES[tool].value} scope.",
        )

    if tool is ToolName.PLACE_ORDER:
        # **Never an order.** The Consumer approves the exact amount on this
        # Merchant's own domain, and that is the whole posture (ADR-0008).
        return JSONResponse(
            {
                "protocol": "mcp",
                "result": {
                    "approve_url": f"https://{_surface.merchant_domain}/agentic/approve",
                    "note": (
                        "Hand this to the Consumer. They approve the exact amount on the "
                        "shop's own page; this agent holds no payment credential and "
                        "cannot complete the purchase itself."
                    ),
                },
            }
        )

    return JSONResponse({"protocol": "mcp", "result": {"tool": tool.value, "accepted": True}})


@router.get("/agent/feed.json")
async def feed() -> JSONResponse:
    """The Product Feed, in Merchant Center attribute names.

    Built from Merchant truth read **fresh** through doors 1 and 2 — the sidecar
    keeps no catalogue of its own, so a feed cannot go stale in a way the
    Merchant cannot see.

    Only exposed items, and **never an exact count**: a feed is the easiest
    place in the system to leak inventory, because it is designed to be read by
    strangers. Door 2 hands back integers over the private network and
    `build_feed` turns every one into a bucket before it is serialized.
    """
    if _surface.trait is None:
        # An empty feed rather than a fabricated one. A sidecar that cannot
        # reach its Merchant has nothing true to publish.
        return JSONResponse({"items": []})

    catalog = await _surface.trait.catalog_read()
    stock = await _surface.trait.stock_read([item.sku for item in catalog.items])
    return JSONResponse(
        build_feed(
            catalog.groups,
            catalog.items,
            stock,
            base_url=f"https://{_surface.merchant_domain}",
            exposed=_surface.exposed,
        )
    )
