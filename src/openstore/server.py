# OpenStore execution server — FastAPI app factory

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from openstore.config import Settings


def console_print(msg: str) -> None:
    print(msg, flush=True)


def create_app(config: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup
        console_print(f"OpenStore server starting for {config.merchant.name}")
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

    # Well-known manifests (stubs for Stage 1)
    @app.get("/.well-known/agent-commerce.json")
    async def agent_commerce() -> dict[str, str]:
        return {
            "merchant_id": config.merchant.name.lower().replace(" ", "-"),
            "name": config.merchant.name,
            "currency": config.merchant.currency,
            "catalog_url": "/agent/catalog",
            "mcp_url": "/agent/mcp",
            "policy_url": "/.well-known/agent-policy.json",
        }

    @app.get("/.well-known/agent-policy.json")
    async def agent_policy() -> dict[str, list[int]]:
        return {"policy_versions": [2]}

    @app.get("/.well-known/agent-card.json")
    async def agent_card() -> dict[str, str | list[str]]:
        return {
            "name": config.merchant.name,
            "description": "OpenStore demo merchant",
            "url": "http://localhost:8000",
            "capabilities": ["mcp", "catalog"],
        }

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_auth_server() -> dict[str, str]:
        return {
            "issuer": "http://localhost:8000",
            "authorization_endpoint": "http://localhost:8000/oauth/authorize",
            "token_endpoint": "http://localhost:8000/oauth/token",
            "jwks_uri": "http://localhost:8000/.well-known/poai-jwks.json",
        }

    @app.get("/.well-known/poai-jwks.json")
    async def poai_jwks() -> dict[str, list[Any]]:
        return {"keys": []}

    @app.get("/.well-known/agent-campaigns.json")
    async def agent_campaigns() -> dict[str, list[Any]]:
        return {"campaigns": []}

    # Agent catalog (stub)
    @app.get("/agent/catalog")
    async def agent_catalog() -> dict[str, list[Any]]:
        return {"items": []}

    # MCP endpoint (stub)
    @app.post("/agent/mcp")
    async def agent_mcp() -> dict[str, str]:
        return {"error": "not implemented"}

    # ACP endpoint (stub)
    @app.post("/agent/acp")
    async def agent_acp() -> dict[str, str]:
        return {"error": "not implemented"}

    # Campaign feed (stub)
    @app.get("/agent/campaigns")
    async def agent_campaigns_feed() -> dict[str, list[Any]]:
        return {"campaigns": []}

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

    return app
