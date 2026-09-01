# OpenStore execution server — FastAPI app factory

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from openstore.config import Settings


def console_print(msg: str) -> None:
    print(msg, flush=True)


def create_app(config: Settings) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup
        console_print(f"OpenStore server starting for {config.merchant.name}")

        # Wire PSP config and start workers
        try:
            from openstore.psp.router import set_psp_config

            set_psp_config(config)
        except Exception as exc:
            console_print(f"Warning: PSP workers not started: {exc}")

        yield

        # Shutdown
        console_print("OpenStore server shutting down")

    app = FastAPI(
        title=f"OpenStore — {config.merchant.name}",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Health check
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "merchant": config.merchant.name}

    # Well-known manifests (S6.4 per §3.9)
    def _mid(cfg: Settings) -> str:
        return cfg.merchant.name.lower().replace(" ", "-").replace("'", "")

    @app.get("/.well-known/agent-commerce.json")
    async def agent_commerce(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import build_agent_commerce_manifest
        origin = str(request.base_url).rstrip("/")
        return build_agent_commerce_manifest(config, origin)

    @app.get("/.well-known/agent-policy.json")
    async def agent_policy() -> dict[str, Any]:
        from openstore.surfaces.wellknown import build_agent_policy_manifest
        return build_agent_policy_manifest(config)

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> dict[str, Any]:
        return {
            "name": config.merchant.name,
            "description": f"OpenStore agent-capable merchant: {config.merchant.name}",
            "url": config.webauthn.origin or "http://localhost:8000",
            "capabilities": ["mcp", "catalog", "checkout"],
            "version": "0.1.0",
        }

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_auth_server(request: Request) -> dict[str, Any]:
        origin = str(request.base_url).rstrip("/")
        return {
            "issuer": origin,
            "authorization_endpoint": f"{origin}/oauth/authorize",
            "token_endpoint": f"{origin}/oauth/token",
            "jwks_uri": f"{origin}/oauth/jwks.json",
            "scopes_supported": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
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
        origin = str(request.base_url).rstrip("/")
        from openstore.surfaces.wellknown import get_signed_campaign_feed
        return get_signed_campaign_feed(config, origin)

    # Agent catalog (S6.5)
    @app.get("/agent/catalog")
    async def agent_catalog(request: Request) -> dict[str, Any]:
        from openstore.surfaces.catalog import serve_catalog_feed
        from openstore.surfaces.wellknown import get_catalog_signing_key
        origin = str(request.base_url).rstrip("/")
        return serve_catalog_feed(
            config, merchant_id=_mid(config),
            private_key_pem=get_catalog_signing_key(_mid(config)),
        )

    # MCP endpoint (S6.3)
    @app.post("/agent/mcp")
    async def agent_mcp(request: Request) -> dict[str, Any]:
        from openstore.surfaces.mcp_server import handle_mcp_request
        from openstore.core.database import get_session
        from openstore.core.oauth import validate_access_token

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

        session = get_session(config)
        try:
            result = handle_mcp_request(
                config=config,
                session=session,
                tool_name=tool_name,
                arguments=arguments,
                token_scopes=token_scopes,
                trace_id=None,
                client_id=client_id,
            )
        finally:
            session.close()
        return result

    # ACP endpoint (stub)
    @app.post("/agent/acp")
    async def agent_acp() -> dict[str, str]:
        return {"error": "not implemented"}

    # Campaign feed (S6.4)
    @app.get("/agent/campaigns")
    async def agent_campaigns_feed(request: Request) -> dict[str, Any]:
        from openstore.surfaces.wellknown import get_signed_campaign_feed
        origin = str(request.base_url).rstrip("/")
        return get_signed_campaign_feed(config, origin)

    # Campaign approve / reject (REGISTRY routes)
    @app.post("/campaign/{campaign_id}/approve")
    async def campaign_approve(campaign_id: str, request: Request) -> dict[str, Any]:
        from openstore.core.campaigns import activate_campaign, CampaignValidationError
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
                return {"status": "approved", "campaign_id": campaign.id, "state": campaign.state.value}
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
                return {"error": "campaign.not_found", "message": f"Campaign {campaign_id} not found"}
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

    # Policy Studio (stub)
    @app.get("/intent/studio")
    async def policy_studio() -> FileResponse:
        return FileResponse(Path(__file__).parent / "surfaces" / "static" / "policy_studio.html")

    # Demo storefront (stub)
    @app.get("/")
    async def storefront() -> FileResponse:
        return FileResponse(Path(__file__).parent / "surfaces" / "static" / "storefront.html")

    # PSP endpoints: webhook + hold/cancel (S5.3, S5.5)
    from openstore.psp.router import psp_router

    app.include_router(psp_router(config))

    return app
