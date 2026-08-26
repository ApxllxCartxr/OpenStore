from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
import uuid

from merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from merchant.config import settings
from merchant.db import init_db, get_session
from merchant.models import (
    AgentCommerceDescriptor, MerchantDescriptor, AuthDescriptor,
    PolicyDescriptor, AuditLogEntry,
)
from merchant.trace import emit
from merchant.oauth.routes import router as oauth_router
from merchant.internal_routes import router as internal_router
from merchant.webhooks import router as webhook_router
from merchant.mcp_server import mcp
from mcp.server.transport_security import TransportSecuritySettings


def create_app() -> FastAPI:
    mcp_http_app = mcp.streamable_http_app(
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    session_manager = mcp._lowlevel_server._session_manager

    @asynccontextmanager
    async def lifespan(app):
        init_db()
        emit(
            channel="merchant-server",
            title="Merchant server started",
            fields={"merchant": "gelateria-roma"},
            trace_id=str(uuid.uuid4()),
            level="info",
        )
        async with session_manager.run():
            yield

    app = FastAPI(title="OpenStore Merchant Server", lifespan=lifespan)
    app.include_router(oauth_router)
    app.include_router(internal_router)
    app.include_router(webhook_router)
    app.mount("/agent/mcp", mcp_http_app)

    templates = Jinja2Templates(directory="merchant/storefront")
    catalog = YAMLCatalogAdapter(settings.merchant_config_path)

    @app.get("/")
    def storefront(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "merchant_name": "Gelateria Roma",
                "products": catalog.list_all(),
            },
        )

    @app.get("/.well-known/agent-commerce.json")
    def agent_commerce():
        return AgentCommerceDescriptor(
            merchant=MerchantDescriptor(name="Gelateria Roma", id="gelateria-roma"),
            storefront="http://localhost:8000/",
            catalog_endpoint="http://localhost:8000/agent/catalog",
            mcp_endpoint="http://localhost:8000/agent/mcp",
            a2a_agent_card="http://localhost:8001/.well-known/agent-card.json",
            auth=AuthDescriptor(
                authorization_server="http://localhost:8000/.well-known/oauth-authorization-server",
                scopes_supported=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
            ),
            policy=PolicyDescriptor(
                currency="INR",
                max_unconfirmed_spend_minor=0,
                requires_human_approval=True,
                default_per_tx_cap_minor=50000,
            ),
        )

    @app.get("/admin/audit", response_class=HTMLResponse)
    def admin_audit(request: Request, session: Session = Depends(get_session)):
        entries = session.exec(
            select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(100)
        ).all()
        return templates.TemplateResponse(
            request,
            "audit.html",
            {"request": request, "entries": entries},
        )

    return app


app = create_app()
