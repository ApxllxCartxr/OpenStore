from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
import time
import uuid

from reference.merchant.openstore_bridge import build_openstore_runtime

from reference.merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from reference.merchant.config import settings
from reference.merchant.db import init_db, get_session
from reference.merchant.models import (
    AgentCommerceDescriptor, MerchantDescriptor, AuthDescriptor,
    PolicyDescriptor, AuditLogEntry,
)
from reference.merchant.trace import emit
from reference.merchant.oauth.routes import router as oauth_router
from reference.merchant.internal_routes import router as internal_router
from reference.merchant.webhooks import router as webhook_router
from reference.merchant.intent_routes import intent_router
from reference.merchant.mcp_server import mcp
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
            fields={"merchant": settings.merchant_id},
            trace_id=str(uuid.uuid4()),
            level="info",
        )
        async with session_manager.run():
            yield

    app = FastAPI(title="OpenStore Merchant Server", lifespan=lifespan)
    app.include_router(oauth_router)
    app.include_router(internal_router)
    app.include_router(webhook_router)
    app.include_router(intent_router)
    app.mount("/agent/mcp", mcp_http_app)

    @app.get("/health")
    def health():
        return {"status": "ok", "merchant": settings.merchant_id}

    templates = Jinja2Templates(directory="reference/merchant/storefront")
    catalog = YAMLCatalogAdapter(settings.merchant_config_path)

    @app.get("/")
    def storefront(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "merchant_name": catalog.merchant_name,
                "products": catalog.list_all(),
            },
        )

    @app.get("/.well-known/agent-commerce.json")
    def agent_commerce(request: Request):
        base = str(request.base_url).rstrip("/")
        return AgentCommerceDescriptor(
            merchant=MerchantDescriptor(name=catalog.merchant_name, id=catalog.merchant_id),
            storefront=f"{base}/",
            catalog_endpoint=f"{base}/agent/catalog",
            mcp_endpoint=f"{base}/agent/mcp",
            a2a_agent_card="http://localhost:8001/.well-known/agent-card.json",
            auth=AuthDescriptor(
                authorization_server=f"{base}/.well-known/oauth-authorization-server",
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

    @app.get("/mandate", response_class=HTMLResponse)
    def mandate_form(request: Request):
        return templates.TemplateResponse(request, "mandate.html", {"request": request})

    @app.post("/mandate", response_class=HTMLResponse)
    def mandate_create(
        request: Request,
        scope: str = "global",
        max_amount: int = 10000000,
        currency: str = "INR",
        expires_in: int = 1440,
    ):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from openstore.core import did as oc_did
        from openstore.models import Mandate

        priv = Ed25519PrivateKey.generate()
        payer_did = oc_did.did_from_pubkey(priv.public_key())
        mandate = Mandate.create(
            private_key=priv,
            mandate_id=f"M-{int(time.time())}",
            merchant_did=build_openstore_runtime().did,
            payer=payer_did,
            scope=scope,
            max_amount_paise=max_amount,
            currency=currency,
            expires_at=int(time.time()) + expires_in * 60,
        )
        result = {
            "payer_did": payer_did,
            "mandate_json": mandate.model_dump_json(),
            "private_key_pem": priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode(),
        }
        return templates.TemplateResponse(request, "mandate.html", {"request": request, "result": result})

    return app


app = create_app()
