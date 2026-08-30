"""HTTP surface for the OpenStore merchant, including the WebAuthn ceremony.

Exposes the WebAuthn register/begin/complete endpoints over REST so a real
browser can drive the human-authorization leg, plus the minimal merchant
actions the buyer agent needs. The ceremony endpoints delegate to
``MerchantRuntime.webauthn`` (the RP); the order endpoint accepts the verified
``authority`` (or a raw assertion the server verifies).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from openstore.auth import Auth
from openstore.client import OpenStoreClient
from openstore.config import settings
from openstore.models import Mandate, Quote
from openstore.runtime import MerchantRuntime
from openstore.surfaces import register_surfaces

STATIC_DIR = Path(__file__).parent / "static"


def create_http_app(
    runtime: MerchantRuntime,
    operator_password: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="OpenStore Merchant", version="0.1.0")
    client = OpenStoreClient(runtime)
    auth = Auth(operator_password or os.environ.get("OPENSTORE_OPERATOR_PASSWORD", "changeme"))

    @app.get("/health")
    def health():
        return {"status": "ok", "did": runtime.did}

    @app.get("/.well-known/agent-commerce.json")
    def agent_commerce_doc():
        # INTEROP_SPEC §5 — bump to 0.2 and add the generated `protocols[]`
        # array. Retain the top-level mcp_endpoint/auth/policy objects for
        # backward compatibility (R5.1a).
        from openstore.protocols import protocols_doc, read_excerpt

        base = runtime.discover()
        doc = base.model_dump() if hasattr(base, "model_dump") else dict(base)
        doc["version"] = "0.2"
        doc["protocols"] = protocols_doc()
        return doc

    @app.get("/protocols/{name}/spec-excerpt")
    def protocol_spec_excerpt(name: str):
        # R5.1c — serve the committed excerpt as text/markdown so a reviewer can
        # see exactly which spec version was built against.
        from openstore.protocols import read_excerpt

        try:
            text = read_excerpt(name)
        except FileNotFoundError:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"no spec excerpt for {name}")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(text, media_type="text/markdown")

    # ---------- WebAuthn ceremony ----------

    @app.post("/webauthn/register/begin")
    def webauthn_register_begin():
        return runtime.webauthn.begin_registration()

    @app.post("/webauthn/register/complete")
    def webauthn_register_complete(payload: dict):
        cid = runtime.webauthn.register_credential(
            attestation_object=payload["attestation_object"],
            client_data_json=payload["client_data_json"],
            session_id=payload["session_id"],
        )
        return {"credential_id": cid}

    @app.post("/webauthn/begin")
    def webauthn_begin(payload: dict):
        q = Quote.model_validate(payload["quote"])
        oc = runtime._order_context(q)
        return runtime.webauthn.begin_assertion(oc["policy_dict"], oc["cart_hash"])

    @app.post("/webauthn/complete")
    def webauthn_complete(payload: dict):
        q = Quote.model_validate(payload["quote"])
        oc = runtime._order_context(q)
        authority = runtime.webauthn.complete_assertion(
            payload["session_id"], payload["assertion"],
            policy=oc["policy_dict"], cart_hash=oc["cart_hash"],
        )
        return authority

    # ---------- Merchant actions ----------

    @app.post("/quote")
    def create_quote(payload: dict):
        return client.create_quote(payload["items"])

    @app.post("/mandate")
    def submit_mandate(payload: dict):
        return client.submit_mandate(Mandate.model_validate(payload))

    @app.post("/order")
    def create_order(payload: dict):
        return client.create_order(
            payload["quote"], Mandate.model_validate(payload["mandate"]),
            authority=payload.get("authority"),
            webauthn_assertion=payload.get("webauthn_assertion"),
        )

    @app.get("/receipt/{receipt_id}")
    def get_receipt(receipt_id: str):
        r = runtime.get_trust_receipt(receipt_id)
        return r.model_dump() if r is not None else {"error": "not found"}

    @app.post("/verify")
    def verify_receipt(payload: dict):
        return client.verify_receipt(payload["receipt_id"], runtime.did)

    # ---------- Webhooks (PRODUCTION_READINESS §1.4) ----------

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request):
        from openstore.webhooks import handle_webhook
        webhook_secret = settings.razorpay_webhook_secret
        return await handle_webhook(request, runtime.ledger, webhook_secret)

    # §8 / §9 operator + agent surfaces (session/bearer-gated).
    register_surfaces(app, runtime, auth)

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
