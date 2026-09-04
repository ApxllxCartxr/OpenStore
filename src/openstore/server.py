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
_HOLD_WARNING_WINDOW_SECONDS = 120


async def _hold_release_loop(config: Settings) -> None:
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import Checkout, OrderState
    from openstore.notifier import send_dm
    from openstore.psp.razorpay_driver import hold_release_worker_tick_with_notifications

    warned: set[str] = set()
    while True:
        await asyncio.sleep(_HOLD_RELEASE_INTERVAL_SECONDS)
        session = get_session(config)
        try:
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
                await send_dm(
                    config,
                    item["chat_user_id"],
                    "Hold released — order not completed in time.",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console_print(f"Warning: hold-release loop tick failed: {exc}")
        finally:
            session.close()


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
                BuyerBot(config, BuyerAgent(config, InProcessMCPClient(config))).register(client)
                discord_task = asyncio.create_task(client.start(token))
            except Exception as exc:
                console_print(f"Warning: Discord client not started: {exc}")

        # S11 Phase 3 (plan item #17): hold_release_worker_tick had zero
        # callers. Second background task, alongside the Discord client,
        # cancelled the same way on shutdown.
        hold_release_task: asyncio.Task[None] = asyncio.create_task(_hold_release_loop(config))

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
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
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
        from openstore.core.oauth import validate_access_token
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
            except Exception:
                pass
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

    # ACP endpoint (stub)
    @app.post("/agent/acp")
    async def agent_acp() -> dict[str, str]:
        return {"error": "not implemented"}

    # Campaign feed (S6.4)
    @app.get("/agent/campaigns")
    async def agent_campaigns_feed(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import get_signed_campaign_feed

        origin = resolve_public_origin(config, request)
        return get_signed_campaign_feed(config, origin)

    # Campaign approve / reject (REGISTRY routes)
    @app.post("/campaign/{campaign_id}/approve")
    async def campaign_approve(campaign_id: str, request: Request) -> dict[str, Any]:
        from openstore.core.campaigns import CampaignValidationError, activate_campaign
        from openstore.core.database import get_session as _get_session

        body = await request.json()
        approver_credential_id = body.get("approver_credential_id", "")
        webauthn_assertion = body.get("webauthn_assertion")

        session = _get_session(config)
        try:
            try:
                campaign = activate_campaign(
                    session,
                    campaign_id,
                    approver_credential_id=approver_credential_id,
                    webauthn_assertion=webauthn_assertion,
                )
                session.commit()
                return {
                    "status": "approved",
                    "campaign_id": campaign.id,
                    "state": campaign.state.value,
                }
            except CampaignValidationError as e:
                session.rollback()
                return {"error": e.reason_code, "message": e.message}
        finally:
            session.close()

    @app.post("/campaign/{campaign_id}/reject")
    async def campaign_reject(campaign_id: str, request: Request) -> dict[str, Any]:
        from openstore.core.database import get_session as _get_session
        from openstore.models import Campaign, CampaignState

        body = await request.json()
        reason = body.get("reason", "")

        session = _get_session(config)
        try:
            from sqlmodel import select as _select

            campaign = session.exec(_select(Campaign).where(Campaign.id == campaign_id)).first()
            if not campaign:
                return {
                    "error": "campaign.not_found",
                    "message": f"Campaign {campaign_id} not found",
                }
            campaign.state = CampaignState.REJECTED
            campaign.updated_at = datetime.now(UTC)
            session.add(campaign)
            session.commit()
            return {"status": "rejected", "campaign_id": campaign.id, "reason": reason}
        finally:
            session.close()

    # Campaign Studio (stub)
    @app.get("/campaign/studio")
    async def campaign_studio() -> FileResponse:
        return FileResponse(Path(__file__).parent / "surfaces" / "static" / "campaign_studio.html")

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
