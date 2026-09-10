# OpenStore execution server — FastAPI app factory

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from openstore import __version__
from openstore.config import Settings, merchant_id


def console_print(msg: str) -> None:
    print(msg, flush=True)


def resolve_public_origin(config: Settings, request: Request | None = None) -> str:
    """SID-1: the origin used in manifests. public_base_url wins (subdomain /
    explicit deployment); otherwise derive from the request (same-origin proxy)."""
    if config.public_base_url:
        return config.public_base_url.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return (config.webauthn.origin or "http://localhost:8000").rstrip("/")


def _gated_paths() -> set[str]:
    from openstore.core.health import GATED_PATHS

    return GATED_PATHS


def _paths_match(route_path: str, gated_template: str) -> bool:
    # Convert FastAPI route path {x} to exact string compare; gated templates use
    # {campaign_id}/{cancel_token} which the app already mounts identically.
    return route_path == gated_template


# S11 Phase 3 (plan item #17): hold_release_worker_tick existed with zero
# callers. This loop runs it every 30s for the life of the process, and DMs
# chat-originated checkouts (Checkout.chat_user_id) a pre-expiry warning and
# a post-release notice. "Already warned" is a plain in-memory set scoped to
# this loop's closure — a soft UX nicety, not a money-critical invariant, so
# it does not need to survive a restart.
_HOLD_RELEASE_INTERVAL_SECONDS = 30
_CAMPAIGN_EXPIRY_INTERVAL_SECONDS = 60
_HOLD_WARNING_WINDOW_SECONDS = 120


async def _hold_release_loop(config: Settings) -> None:
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import Checkout, OrderState
    from openstore.notifier import send_dm, try_edit_dm
    from openstore.psp.razorpay_driver import hold_release_worker_tick_with_notifications

    warned: set[str] = set()
    while True:
        await asyncio.sleep(_HOLD_RELEASE_INTERVAL_SECONDS)
        session = get_session(config)
        try:
            # Q-032b: reconcile near-expiry HELD rows against the PSP before
            # warning/releasing, so a lost webhook does not kill a paid order.
            # Best-effort: never blocks the release path below.
            try:
                from openstore.psp.razorpay_driver import reconcile_held_before_release

                reconciled = reconcile_held_before_release(
                    config, session, warn_window_seconds=_HOLD_WARNING_WINDOW_SECONDS
                )
                if reconciled["reconciled"]:
                    session.commit()
                    console_print(
                        f"Hold loop reconciled {reconciled['reconciled']}/"
                        f"{reconciled['checked']} near-expiry hold(s) from PSP"
                    )
            except Exception as exc:
                console_print(f"Warning: pre-release PSP reconcile failed: {exc}")
            now = datetime.now(UTC).replace(tzinfo=None)
            warn_cutoff = now + timedelta(seconds=_HOLD_WARNING_WINDOW_SECONDS)
            soon = session.exec(
                select(Checkout).where(
                    Checkout.state == OrderState.HELD,
                    Checkout.expires_at <= warn_cutoff,
                    Checkout.expires_at > now,
                    Checkout.chat_user_id.is_not(None),  # type: ignore[union-attr]
                )
            ).all()
            for checkout in soon:
                if checkout.id in warned:
                    continue
                warned.add(checkout.id)
                assert checkout.chat_user_id is not None
                await send_dm(
                    config,
                    checkout.chat_user_id,
                    "Your hold expires in under 2 minutes — pay soon or it will be released.",
                )

            released = hold_release_worker_tick_with_notifications(config, session)
            session.commit()
            for item in released:
                warned.discard(item["checkout_id"])
                _release_msg = "Hold released — order not completed in time."
                if item.get("discord_message_id"):
                    await try_edit_dm(
                        config,
                        item["chat_user_id"],
                        item["discord_message_id"],
                        _release_msg,
                    )
                await send_dm(
                    config,
                    item["chat_user_id"],
                    _release_msg,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console_print(f"Warning: hold-release loop tick failed: {exc}")
        finally:
            session.close()


async def _campaign_expiry_loop(config: Settings) -> None:
    """PRD §9.4: 'EXPIRED is set by the sweeper when now >= ends_at.'

    Same shape as _hold_release_loop above — no scheduler dependency. Without
    this, ACTIVE campaigns past their window stayed ACTIVE forever; the signed
    feed filtered them out (INV-13) but the stored state lied, and PAUSED /
    EXPIRED were both unreachable.
    """
    from openstore.core.campaigns import expire_campaigns_due
    from openstore.core.database import session_scope

    while True:
        await asyncio.sleep(_CAMPAIGN_EXPIRY_INTERVAL_SECONDS)
        try:
            with session_scope(config) as session:
                expired = expire_campaigns_due(session)
            if expired:
                console_print(f"Campaign sweeper expired {len(expired)} campaign(s)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console_print(f"Warning: campaign expiry loop tick failed: {exc}")


async def _campaign_growth_loop(config: Settings) -> None:
    """DECISION-034: the autonomous half of the growth loop. On a timer,
    checks this merchant for stalled SKUs (real 30d demand, zero units in the
    last 7) with no live campaign already covering them, and — bounded by a
    per-merchant cooldown — drafts one. Same shape as the other two loops:
    never raises past its own tick, never touches money, and this alone can
    NEVER activate a campaign (auto_draft_campaign_if_stalled only ever
    reaches PENDING_APPROVAL) — the same human WebAuthn ceremony at
    /campaign/studio is still required either way (R0.10)."""
    from openstore.agents.campaign_agent import auto_draft_campaign_if_stalled
    from openstore.config import merchant_id as merchant_id_of
    from openstore.core.database import session_scope

    interval = config.campaign.growth_check_interval_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            with session_scope(config) as session:
                campaign = auto_draft_campaign_if_stalled(session, config, merchant_id_of(config))
                # Extract before the session (and any lazy-load access to a
                # detached instance) closes at the end of this block.
                logged = None if campaign is None else (campaign.id, campaign.title)
            if logged is not None:
                console_print(
                    f"Growth loop auto-drafted campaign {logged[0]} "
                    f"({logged[1]}) — pending approval at /campaign/studio"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console_print(f"Warning: campaign growth loop tick failed: {exc}")


def create_app(config: Settings) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup
        console_print(f"OpenStore server starting for {config.merchant.name}")

        # Register this loop so notifier.run_from_worker_thread (called from
        # FastAPI's sync BackgroundTasks threadpool, e.g. webhook processing)
        # can hand DM/trace coroutines back here instead of spinning up an
        # unrelated loop that breaks the live discord.Client's aiohttp session.
        from openstore.notifier import set_main_loop

        set_main_loop(asyncio.get_running_loop())

        # SID-3 boot order: config → migrations → keys → workers → serve.
        try:
            from openstore.psp.router import set_psp_config

            set_psp_config(config)
        except Exception as exc:
            console_print(f"Warning: PSP workers not started: {exc}")

        # S11 Phase 2: one discord.Client for the whole process, started here
        # and shared by the notifier (four trace channels + DMs) and the buyer
        # bot's DM-first command handler — never two separate Discord logins.
        # No-op when the token is absent/"token" so tests stay offline.
        discord_task: asyncio.Task[None] | None = None
        token = config.discord.bot_token
        if token and token != "token":
            try:
                import discord

                from openstore.agents.buyer_agent import BuyerAgent, BuyerBot
                from openstore.agents.mcp_client import InProcessMCPClient
                from openstore.notifier import set_discord_client

                intents = discord.Intents.default()
                intents.message_content = True  # privileged; toggle in the Discord dev portal
                client = discord.Client(intents=intents)
                set_discord_client(client)
                # Gated separately from the client itself: the client is
                # shared with the notifier (four trace channels + DMs)
                # regardless of whether this process also hosts the buyer
                # bot. Two merchants on one Discord bot_token must not both
                # register a BuyerBot, or both get the same DM and reply.
                if config.discord.buyer_bot_enabled:
                    BuyerBot(config, BuyerAgent(config, InProcessMCPClient(config))).register(
                        client
                    )
                discord_task = asyncio.create_task(client.start(token))
            except Exception as exc:
                console_print(f"Warning: Discord client not started: {exc}")

        # S11 Phase 3 (plan item #17): hold_release_worker_tick had zero
        # callers. Second background task, alongside the Discord client,
        # cancelled the same way on shutdown.
        hold_release_task: asyncio.Task[None] = asyncio.create_task(_hold_release_loop(config))
        # DECISION-025: PRD §9.4's campaign expiry sweeper.
        campaign_expiry_task: asyncio.Task[None] = asyncio.create_task(
            _campaign_expiry_loop(config)
        )
        # DECISION-034: autonomous growth trigger, alongside expiry.
        campaign_growth_task: asyncio.Task[None] = asyncio.create_task(
            _campaign_growth_loop(config)
        )

        yield

        # Shutdown
        if discord_task is not None:
            discord_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await discord_task
            from openstore.notifier import set_discord_client

            set_discord_client(None)

        hold_release_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hold_release_task

        campaign_expiry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await campaign_expiry_task

        campaign_growth_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await campaign_growth_task

        set_main_loop(None)

        console_print("OpenStore server shutting down")

    # SID-5: origin security boundary. CORS is restricted to the merchant origin
    # (public_base_url, else the WebAuthn origin). WebAuthn RP ID must equal the
    # public_base_url host when one is configured (fail loud, R0.5).
    merchant_origin = (
        (config.public_base_url or config.webauthn.origin).rstrip("/")
        if (config.public_base_url or config.webauthn.origin)
        else "http://localhost:8000"
    )
    if config.public_base_url:
        from urllib.parse import urlparse

        pub_host = urlparse(config.public_base_url).hostname
        if pub_host and pub_host != config.webauthn.rp_id:
            raise ValueError(
                "SID-5 origin mismatch: public_base_url host "
                f"({pub_host}) != webauthn.rp_id ({config.webauthn.rp_id})"
            )

    app = FastAPI(
        title=f"OpenStore — {config.merchant.name}",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[merchant_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SID-2 readiness gate: refuse agent/money traffic until the sidecar is ready.
    @app.middleware("http")
    async def readiness_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        from openstore.core.health import compute_readiness, is_gated, is_ready

        if is_gated(request.url.path) and not is_ready(config):
            from fastapi.responses import JSONResponse

            state = compute_readiness(config)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "reason_codes": state.reason_codes,
                },
            )
        return await call_next(request)

    # Health check (liveness, SID-2)
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "merchant": config.merchant.name}

    # SID-2 health endpoints
    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "alive", "merchant": config.merchant.name}

    @app.get("/health/ready", response_model=None)
    async def health_ready() -> dict[str, Any] | Response:
        from openstore.core.health import compute_readiness

        state = compute_readiness(config)
        if state.ready:
            return {"status": "ready", "merchant": config.merchant.name}
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "merchant": config.merchant.name,
                "reason_codes": state.reason_codes,
            },
        )

    # SID-7 metrics endpoint
    @app.get("/internal/metrics")
    async def internal_metrics() -> PlainTextResponse:
        from openstore.core.health import metrics_text

        return PlainTextResponse(metrics_text(config), media_type="text/plain")

    # Well-known manifests (S6.4 per §3.9)

    @app.get("/.well-known/agent-commerce.json")
    async def agent_commerce(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import build_agent_commerce_manifest

        origin = resolve_public_origin(config, request)
        return build_agent_commerce_manifest(config, origin)

    @app.get("/.well-known/agent-policy.json")
    async def agent_policy() -> dict[str, Any]:
        from openstore.surfaces.wellknown import build_agent_policy_manifest

        return build_agent_policy_manifest(config)

    @app.get("/.well-known/agent-card.json")
    async def agent_card(request: Request) -> dict[str, Any]:
        return {
            "name": config.merchant.name,
            "description": f"OpenStore agent-capable merchant: {config.merchant.name}",
            "url": resolve_public_origin(config, request),
            "capabilities": ["mcp", "catalog", "checkout"],
            "version": __version__,
            "poai_version": "0.1",
        }

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_auth_server(request: Request) -> dict[str, Any]:
        origin = resolve_public_origin(config, request)
        return {
            "issuer": origin,
            "authorization_endpoint": f"{origin}/oauth/authorize",
            "token_endpoint": f"{origin}/oauth/token",
            "jwks_uri": f"{origin}/oauth/jwks.json",
            "scopes_supported": [
                "catalog:read",
                "cart:write",
                "checkout:initiate",
                "checkout:confirm",
            ],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "client_credentials"],
            "code_challenge_methods_supported": ["S256"],
        }

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> Any:
        from fastapi.responses import JSONResponse

        from openstore.core.database import session_scope
        from openstore.core.oauth import (
            ACCESS_TOKEN_TTL_SECONDS,
            OAuthError,
            create_token_pair,
            validate_client,
        )

        body = await request.json()
        grant_type = body.get("grant_type")

        if grant_type != "client_credentials":
            return JSONResponse(
                status_code=400,
                content={
                    "error": "unsupported_grant_type",
                    "error_description": f"Grant type {grant_type!r} is not supported",
                },
            )

        client_id = body.get("client_id")
        client_secret = body.get("client_secret")
        requested_scope = body.get("scope")

        try:
            # session_scope commits on success / rolls back on error (INV-11) — a
            # plain get_session()+close() silently drops every write this call makes.
            with session_scope(config) as session:
                client = validate_client(
                    session,
                    client_id=str(client_id) if client_id else "",
                    client_secret=str(client_secret) if client_secret else None,
                )

                # Never grant a scope the client isn't registered for: intersect
                # any requested scope with the client's registered set.
                if requested_scope:
                    requested_scopes = set(str(requested_scope).split())
                    granted_scopes = [s for s in client.scopes if s in requested_scopes]
                else:
                    granted_scopes = list(client.scopes)

                access_token, _refresh_token = create_token_pair(
                    session,
                    client_id=client.client_id,
                    scopes=granted_scopes,
                )
        except OAuthError as e:
            return JSONResponse(
                status_code=e.status_code,
                content={"error": e.error, "error_description": e.description},
            )

        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "scope": " ".join(granted_scopes),
        }

    @app.get("/.well-known/poai-jwks.json")
    async def poai_jwks() -> dict[str, Any]:
        from openstore.surfaces.wellknown import get_poai_jwks

        return get_poai_jwks(config)

    @app.get("/.well-known/agent-campaigns.json")
    async def agent_campaigns_signed(request: Request) -> dict[str, Any]:
        origin = resolve_public_origin(config, request)
        from openstore.surfaces.wellknown import get_signed_campaign_feed

        return get_signed_campaign_feed(config, origin)

    # UCP discovery (DECISION-026)
    @app.get("/.well-known/ucp")
    async def ucp_manifest(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import build_ucp_manifest

        return build_ucp_manifest(config, resolve_public_origin(config, request))

    # Agent catalog (S6.5)
    @app.get("/agent/catalog")
    async def agent_catalog(request: Request) -> dict[str, Any]:
        from openstore.surfaces.catalog import serve_catalog_feed
        from openstore.surfaces.wellknown import get_catalog_signing_key

        return serve_catalog_feed(
            config,
            merchant_id=merchant_id(config),
            private_key_pem=get_catalog_signing_key(merchant_id(config)),
        )

    # MCP endpoint (S6.3)
    @app.post("/agent/mcp")
    async def agent_mcp(request: Request) -> dict[str, Any]:
        from openstore.core.database import get_session
        from openstore.core.oauth import OAuthError, validate_access_token
        from openstore.surfaces.mcp_server import handle_mcp_request

        body = await request.json()
        auth_header = request.headers.get("authorization", "")
        token_scopes: list[str] = []
        client_id = "anonymous"

        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:]
            session = get_session(config)
            try:
                token_record = validate_access_token(session, bearer_token)
                token_scopes = token_record.scopes
                client_id = token_record.client_id
            except OAuthError as e:
                session.close()
                return {"error": e.error, "message": e.description}
            finally:
                session.close()

        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})

        from openstore.core.database import session_scope

        # session_scope commits on success / rolls back on error (INV-11) — a
        # plain get_session()+close() silently drops every write this call makes.
        with session_scope(config) as session:
            result = handle_mcp_request(
                config=config,
                session=session,
                tool_name=tool_name,
                arguments=arguments,
                token_scopes=token_scopes,
                trace_id=None,
                client_id=client_id,
            )
        return result

    # ACP endpoint (stub). DECISION-026: the route stays registered, but it is no
    # longer advertised in the agent-commerce manifest — claiming a protocol the
    # sidecar does not speak is worse than claiming none.
    @app.post("/agent/acp")
    async def agent_acp() -> dict[str, str]:
        return {
            "error": "not_implemented",
            "message": (
                "ACP is not implemented. Use the MCP endpoint at /agent/mcp; "
                "capabilities are discoverable at /.well-known/ucp."
            ),
        }

    # Campaign feed (S6.4)
    @app.get("/agent/campaigns")
    async def agent_campaigns_feed(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import get_signed_campaign_feed

        origin = resolve_public_origin(config, request)
        return get_signed_campaign_feed(config, origin)

    # DECISION-024: /campaign/{id}/approve, /reject, /pause and /campaign/studio
    # moved into policy_studio_router (surfaces/studio.py). They lived here with
    # no authentication of any kind, and approve passed the request body straight
    # into activate_campaign, which only checked the assertion was truthy.

    # Demo storefront (stub)
    @app.get("/")
    async def storefront() -> FileResponse:
        return FileResponse(Path(__file__).parent / "surfaces" / "static" / "storefront.html")

    # PSP endpoints: webhook + hold/cancel (S5.3, S5.5)
    from openstore.psp.router import psp_router

    app.include_router(psp_router(config))

    # Policy Studio (S3.5 / S11 Phase 1): real WebAuthn registration/assertion +
    # blast-radius endpoints, previously built but never mounted.
    from openstore.surfaces.studio import policy_studio_router

    app.include_router(policy_studio_router(config))

    # Evidence surface (S11 Phase 4): serves the PoAI bundle produced when a
    # chat-originated checkout reaches RELEASED (psp/router.py).
    from openstore.surfaces.evidence import evidence_router

    app.include_router(evidence_router(config))

    return app
