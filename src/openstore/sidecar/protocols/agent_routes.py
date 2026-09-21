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

import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openstore.sidecar.admission.oauth import Admission, AgentToken
from openstore.sidecar.admission.profile import ProfileFetcher, ProfileRefused
from openstore.sidecar.admission.ratelimit import RateLimiter, Tier
from openstore.sidecar.basket import Basket, BasketStore
from openstore.sidecar.core.codes import (
    TOOL_SCOPES,
    AuthorityKind,
    OrderStatus,
    PaymentMethod,
    ReasonCode,
    ToolName,
)
from openstore.sidecar.core.feed import build_feed
from openstore.sidecar.protocols import acp, ap2, mcp, tools, ucp
from openstore.sidecar.protocols.tools import ToolRefused
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
    public_origin: str = ""
    """The origin the cards advertise. Empty means `https://<merchant_domain>`,
    which is right for every real install and wrong for the plain-http demo —
    so the demo sets it."""
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
    keyring: Any = None
    """A `Keyring`. Held so a receipt can be sealed by the key this surface
    publishes — an empty default means this deploy has no signing key, and every
    seal refuses rather than producing an unverifiable receipt."""
    trait: Any = None
    """A `TraitClient`. The feed is built from Merchant truth read fresh through
    doors 1 and 2 — the sidecar holds no catalogue of its own (ADR-0001)."""
    baskets: BasketStore = field(default_factory=BasketStore)
    """One basket per agent, held here because the cart lives in the sidecar —
    an agent that keeps its own has two and shows the wrong one (SPEC §11)."""
    exposed: set[str] | None = None
    """Which SKUs the Merchant exposes to agents. `None` means every active
    item, which is the demo's configuration — not a default that quietly
    publishes something unexposed."""


#: The tools that read or write the Consumer's cart. Everything else — browsing,
#: placing, asking after an order — touches no basket, and is dispatched without
#: one so a missing database cannot break the catalogue.
BASKET_TOOLS: frozenset[ToolName] = frozenset(
    {
        ToolName.ADD_LINE,
        ToolName.REMOVE_LINE,
        ToolName.SET_DESTINATION,
        ToolName.SET_CONTACT,
        ToolName.CHOOSE_FULFILLMENT,
        ToolName.APPLY_PUBLIC_CODE,
        ToolName.START_CHECKOUT,
    }
)

_surface = AgentSurface()


def _origin() -> str:
    return _surface.public_origin or f"https://{_surface.merchant_domain}"


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
        origin=_origin(),
        enabled_methods=_surface.enabled_methods,
        enabled_authority_kinds=_surface.enabled_authority_kinds,
        demo=_surface.demo,
    )


@router.get("/.well-known/ucp.json")
def ucp_card() -> dict[str, Any]:
    return ucp_manifest(
        merchant_domain=_surface.merchant_domain,
        merchant_name=_surface.merchant_name,
        origin=_origin(),
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
        token = _surface.admission.issue_for_stranger(
            profile.agent_id, name=profile.name, profile_url=profile.source_url
        )
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
def tools_list(request: Request) -> JSONResponse:
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

    _surface.admission.note_call(agent.agent_id)
    payload = body.get("input") if isinstance(body.get("input"), dict) else body
    try:
        result = await _run_tool(tool, agent.agent_id, payload)
    except ToolRefused as refusal:
        return _refuse(refusal.code, refusal.detail)
    except TraitError as exc:
        # The Merchant refused. Its reason is better than any we could invent.
        return _refuse(exc.code, exc.detail)
    return JSONResponse({"protocol": "mcp", "result": result})


async def _run_tool(tool: ToolName, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One tool, actually run, with this agent's basket loaded around it.

    The basket is read before the tool and written after it, at this one place,
    because the tools mutate the `Basket` object directly — a store that tried
    to notice would be guessing. Written even when the tool refuses: a refusal
    that still changed the basket (a line added, then a fulfillment option the
    Merchant rejected) must not silently roll the change back.
    """
    # Only the tools that touch a cart load one. `search` and `read-item` are
    # browsing, and making them read and write a basket row would put the whole
    # catalogue behind the database — a shop with a misconfigured
    # SIDECAR_DATABASE_URL should still be able to answer what it sells.
    basket = await _surface.baskets.for_agent(agent_id) if tool in BASKET_TOOLS else None
    try:
        result = await _dispatch(tool, agent_id, payload, basket)
    except Exception:
        # Saved on the way out too. A refusal that still changed the basket — a
        # line added, then a fulfillment option the Merchant rejected — must not
        # silently roll the change back.
        if basket is not None:
            await _surface.baskets.save(basket)
        raise

    if tool is ToolName.START_CHECKOUT:
        # The basket has become an order, so the agent starts a fresh one. Door
        # 7 keys on `cart_id:attempt`, so a second checkout built on the same
        # cart would idempotently return the FIRST order — and if that one was
        # already paid, the Consumer would be handed a receipt they had already
        # had. Cleared here rather than inside the tool so this is the one place
        # that decides whether the basket survives its own call.
        await _surface.baskets.clear(agent_id)
    elif basket is not None:
        await _surface.baskets.save(basket)
    return result


async def _dispatch(
    tool: ToolName, agent_id: str, payload: dict[str, Any], basket: Basket | None
) -> dict[str, Any]:
    """One tool, actually run.

    Every branch reaches Merchant truth or the money core; none of them answers
    `accepted: true`, which is what this surface used to do for all thirteen.
    """
    from openstore.sidecar import checkout as flow

    def merchant() -> Any:
        """The trait, or a refusal that says why there is none.

        Demanded per tool rather than up front: `place-order` hands back a link
        to a checkout that already exists and needs no Merchant round trip, and
        refusing it for a missing trait would report the wrong problem.
        """
        if _surface.trait is None:
            raise ToolRefused(
                ReasonCode.NOT_FOUND,
                "This sidecar has no Merchant wired, so it can answer nothing about a "
                "catalogue. Check /readyz.",
            )
        return _surface.trait

    def cart() -> Basket:
        """The basket, for the tools that have one.

        `None` here is a mistake in `BASKET_TOOLS`, not a state to handle: it
        would mean this tool was dispatched without the cart it edits, and an
        empty stand-in would quietly drop the Consumer's lines.
        """
        assert basket is not None, f"{tool.value} was dispatched without a basket"
        return basket

    if tool is ToolName.SEARCH:
        return await tools.search(merchant(), str(payload.get("query", "")))
    if tool is ToolName.READ_ITEM:
        return await tools.read_item(merchant(), str(payload.get("group", "")))
    if tool is ToolName.ADD_LINE:
        parent = payload.get("parent")
        return await tools.add_line(
            merchant(),
            cart(),
            str(payload.get("sku", "")),
            int(payload.get("qty", 1)),
            str(parent) if parent else None,
        )
    if tool is ToolName.REMOVE_LINE:
        if not cart().remove(str(payload.get("sku", ""))):
            raise ToolRefused(ReasonCode.NOT_FOUND, "That SKU is not in the basket.")
        return await tools.summary(merchant(), cart())
    if tool is ToolName.SET_DESTINATION:
        tools.set_destination(cart(), dict(payload.get("destination") or payload))
        return await tools.summary(merchant(), cart())
    if tool is ToolName.SET_CONTACT:
        tools.set_contact(cart(), dict(payload.get("contact") or payload))
        return await tools.summary(merchant(), cart())
    if tool is ToolName.CHOOSE_FULFILLMENT:
        option = str(payload.get("id", ""))
        if not option:
            return await tools.fulfillment_options(merchant(), cart())
        return await tools.choose_fulfillment(merchant(), cart(), option)
    if tool is ToolName.APPLY_PUBLIC_CODE:
        return await tools.apply_public_code(merchant(), cart(), str(payload.get("code", "")))

    if tool is ToolName.START_CHECKOUT:
        if not cart().quotable():
            summary = await tools.summary(merchant(), cart())
            raise ToolRefused(
                ReasonCode.NOT_FOUND,
                f"This basket is not ready to check out; it still needs: "
                f"{', '.join(summary.get('needs', []))}.",
            )
        if not cart().contact:
            raise ToolRefused(
                ReasonCode.NOT_FOUND,
                "The shop needs a Contact Point to send the order confirmation to.",
            )
        destination = cart().destination
        assert destination is not None
        checkout = await flow.start(
            flow.get_context(),
            cart_id=cart().cart_id,
            lines=list(cart().lines),
            destination=destination,
            contact=dict(cart().contact),
            fulfillment_option_id=cart().fulfillment_option_id,
            method=tools.method_for(str(payload.get("method", "upi"))),
            agent_id=agent_id,
            discount_code=cart().discount_code,
        )
        return {
            "order_id": checkout.order_id,
            "total_minor": checkout.total_minor,
            "currency": checkout.quote.currency,
            "quote": checkout.quote.model_dump(mode="json"),
            "expires_utc": checkout.expiry_utc,
            "next": "place-order returns the link the Consumer approves.",
        }

    if tool is ToolName.PLACE_ORDER:
        # **Never an order.** The Consumer approves the exact amount on this
        # Merchant's own domain, and that is the whole posture (ADR-0008).
        ctx = flow.get_context()
        if not ctx.ready():
            # The money path is not wired, so there is no checkout to approve
            # and there never could be. Refused with the reason rather than
            # searching a store that has no database behind it.
            raise ToolRefused(
                ReasonCode.NOT_FOUND,
                "There is no started checkout to approve: this sidecar's money path is "
                "not wired, so start-checkout cannot have run. Check /readyz.",
            )
        # Only a checkout still waiting on its tap. A paid one would hand back
        # an approve URL whose token is already spent, which reads to the
        # Consumer as a broken link rather than as "you already bought this".
        started = [
            c
            for c in await ctx.store.live()
            if c.agent_id == agent_id and c.status is OrderStatus.PENDING
        ]
        if not started:
            raise ToolRefused(
                ReasonCode.NOT_FOUND,
                "There is no started checkout to approve. Call start-checkout first.",
            )
        checkout = started[-1]
        return {
            "order_id": checkout.order_id,
            "approve_url": flow.approve_url(ctx, checkout),
            "total_minor": checkout.total_minor,
            "currency": checkout.quote.currency,
            "note": (
                "Hand this to the Consumer. They approve the exact amount on the "
                "shop's own page; this agent holds no payment credential and "
                "cannot complete the purchase itself."
            ),
        }

    if tool is ToolName.ORDER_STATUS:
        order = await merchant().orders_read(str(payload.get("order_id", "")))
        ctx = flow.get_context()
        # The receipt id lives on the checkout row, and a sidecar with no
        # database has no row to read — the Merchant's status still answers.
        known = await ctx.store.get(order.order_id) if ctx.store.sessionmaker else None
        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "receipt_id": known.receipt_id if known and known.receipt_id else None,
        }

    if tool is ToolName.CANCEL_ORDER:
        ctx = flow.get_context()
        order_id = str(payload.get("order_id", ""))
        try:
            cancelled = await flow.cancel(ctx, order_id, reason=str(payload.get("reason", "")))
        except flow.CheckoutRefused as refusal:
            raise ToolRefused(refusal.code, refusal.detail) from None
        return {"order_id": cancelled.order_id, "status": cancelled.status.value}

    if tool is ToolName.REQUEST_REFUND:
        # **The one thing an agent may do about a refund: ask.** A refund moves
        # money and belongs to the Merchant; an agent that could refund could
        # move money out of a shop it holds no credential for (ADR-0013). So
        # this records an ask in the queue `/agentic` → Refunds shows, and says
        # plainly that nothing has been promised.
        from openstore.sidecar.console.refunds import get_refund_queue

        order = await merchant().orders_read(str(payload.get("order_id", "")))
        try:
            request = await get_refund_queue().request(
                order_id=order.order_id,
                agent_id=agent_id,
                reason=str(payload.get("reason", "")),
                order_status=order.status,
            )
        except TraitError as exc:
            raise ToolRefused(exc.code, exc.detail) from None
        return {
            "request_id": request.request_id,
            "order_id": request.order_id,
            "state": request.state.value,
            "note": (
                "Queued for the shop. No money has moved and none is promised: a refund "
                "is the Merchant's to make, and they decide the amount."
            ),
        }

    raise tools.unsupported(tool)


# ── The other three protocols ────────────────────────────────────────────────
#
# The card has advertised `["mcp", "ucp", "ap2", "acp"]` since it was written
# and only MCP had an endpoint: `ucp.py`, `acp.py` and `ap2.py` produced their
# envelopes for tests and for nothing else. A2 through A6 were green on that.
#
# They are translators, and that is the whole design: each one starts a checkout
# through **the same** `checkout.start`, over the same Gate and the same Quote,
# and differs only in the envelope it writes and the refusal it owes its own
# spec. The core Transcript is byte-identical across all four because there is
# only one core.


async def _start_from(payload: dict[str, Any], agent_id: str) -> Any:
    """Build a checkout from a protocol-shaped request body.

    Every protocol carries the same four facts under different names; this is
    where the naming stops mattering.
    """
    from openstore.sidecar import checkout as flow
    from openstore.sidecar.trait.models import Destination, Line

    lines_in = payload.get("lines") or payload.get("items") or payload.get("line_items") or []
    lines = [
        Line(
            sku=str(entry.get("sku") or entry.get("id") or entry.get("item", {}).get("id", "")),
            qty=int(entry.get("qty") or entry.get("quantity") or 1),
        )
        for entry in lines_in
    ]
    if not lines:
        raise ToolRefused(ReasonCode.NOT_FOUND, "This checkout carries no lines.")

    raw_destination = (
        payload.get("destination")
        or payload.get("fulfillment_address")
        or payload.get("shipping_address")
        or {}
    )
    try:
        destination = Destination.model_validate(raw_destination)
    except Exception:
        raise ToolRefused(
            ReasonCode.NOT_FOUND, "This checkout carries no Destination the shop can read."
        ) from None

    contact = payload.get("contact") or payload.get("buyer") or {}
    return await flow.start(
        flow.get_context(),
        cart_id=str(payload.get("cart_id") or f"cart_{secrets.token_hex(8)}"),
        lines=lines,
        destination=destination,
        contact={k: str(v) for k, v in contact.items() if k in {"email", "phone"} and v},
        fulfillment_option_id=str(
            payload.get("fulfillment_option_id") or payload.get("fulfillment_option") or ""
        ),
        method=tools.method_for(str(payload.get("method", "upi"))),
        agent_id=agent_id,
    )


@router.post("/agent/ucp/checkout")
async def ucp_checkout(request: Request) -> JSONResponse:
    """UCP: a checkout whose completion is a buyer escalation to this domain.

    `direct_completion: false` is already in the manifest; this is the endpoint
    that manifest was describing.
    """
    from openstore.sidecar import checkout as flow

    agent = _bearer(request)
    if agent is None:
        return _refuse(ReasonCode.SIGNATURE_INVALID, "This surface needs a token.")
    refused = _limit(request, "agent", agent.agent_id, agent.tier)
    if refused:
        return refused
    try:
        checkout = await _start_from(await request.json(), agent.agent_id)
    except ToolRefused as exc:
        return _refuse(exc.code, exc.detail)
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)

    ctx = flow.get_context()
    return JSONResponse(
        ucp.checkout(checkout.quote, approve_url=flow.approve_url(ctx, checkout))
        | {"order_id": checkout.order_id}
    )


@router.post("/agent/acp/checkout_sessions")
async def acp_create_session(request: Request) -> JSONResponse:
    """ACP `createCheckoutSession`."""
    from openstore.sidecar import checkout as flow

    agent = _bearer(request)
    if agent is None:
        return _refuse(ReasonCode.SIGNATURE_INVALID, "This surface needs a token.")
    try:
        acp.check_headers("createCheckoutSession", dict(request.headers))
    except acp.AcpError as exc:
        return JSONResponse(status_code=exc.status, content=exc.to_payload())
    try:
        checkout = await _start_from(await request.json(), agent.agent_id)
    except ToolRefused as exc:
        return _refuse(exc.code, exc.detail)
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)

    session = acp.session_from_quote(checkout.order_id, checkout.quote)
    ctx = flow.get_context()
    return JSONResponse(acp.envelope(session) | {"approve_url": flow.approve_url(ctx, checkout)})


@router.post("/agent/acp/checkout_sessions/{session_id}/complete")
async def acp_complete(session_id: str, request: Request) -> JSONResponse:
    """ACP `completeCheckoutSession` — **the documented refusal**.

    It is not unimplemented. A completion carrying a delegated credential is
    exactly the authority this Merchant does not grant an agent, and the badge
    on the card says so before an agent starts.
    """
    from openstore.sidecar import checkout as flow

    agent = _bearer(request)
    if agent is None:
        return _refuse(ReasonCode.SIGNATURE_INVALID, "This surface needs a token.")
    try:
        acp.check_headers("completeCheckoutSession", dict(request.headers))
    except acp.AcpError as exc:
        return JSONResponse(status_code=exc.status, content=exc.to_payload())

    ctx = flow.get_context()
    checkout = await ctx.store.get(session_id)
    if checkout is None:
        return _refuse(ReasonCode.NOT_FOUND, f"No checkout session {session_id!r}.")
    body = await request.json()
    session = acp.session_from_quote(checkout.order_id, checkout.quote)
    return JSONResponse(
        status_code=400,
        content=acp.complete(
            session,
            approve_url=flow.approve_url(ctx, checkout),
            payment_data=body.get("payment_data"),
        ),
    )


@router.post("/agent/ap2/checkout")
async def ap2_checkout(request: Request) -> JSONResponse:
    """AP2: the mandate layer over the same checkout.

    The Merchant signs a Checkout Mandate over the exact cart(). The agent's
    user signs theirs over `checkout_hash` — and the human tap on this domain
    still happens, because a mandate is evidence of intent and not a payment
    credential.
    """
    from openstore.sidecar import checkout as flow

    agent = _bearer(request)
    if agent is None:
        return _refuse(ReasonCode.SIGNATURE_INVALID, "This surface needs a token.")
    if _surface.keyring is None:
        return _refuse(ReasonCode.NOT_FOUND, "This sidecar holds no signing key to mandate with.")
    try:
        checkout = await _start_from(await request.json(), agent.agent_id)
    except ToolRefused as exc:
        return _refuse(exc.code, exc.detail)
    except TraitError as exc:
        return _refuse(exc.code, exc.detail)

    ctx = flow.get_context()
    mandate = ap2.sign_checkout(
        {
            "cart_hash": checkout.cart_hash,
            "amount_minor": checkout.total_minor,
            "currency": checkout.quote.currency,
            "merchant_domain": _surface.merchant_domain,
            "order_id": checkout.order_id,
            "expiry_utc": checkout.expiry_utc,
        },
        _surface.keyring.current,
    )
    return JSONResponse(
        {
            "protocol": "ap2",
            "checkout_mandate": mandate,
            "checkout_hash": ap2.checkout_hash(mandate),
            "order_id": checkout.order_id,
            "approve_url": flow.approve_url(ctx, checkout),
            "note": (
                "Sign your Cart Mandate over this checkout_hash. Completion is still a human "
                "tap on this domain: a mandate is evidence of intent, not a payment credential."
            ),
        }
    )


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
            base_url=_origin(),
            exposed=_surface.exposed,
        )
    )
