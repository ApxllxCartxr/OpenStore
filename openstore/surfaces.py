"""HTTP surface for the merchant operator + agent (IMPLEMENTATION_SPEC §8 / §9).

Public: discovery JWKS/policy, evidence download + viewer, unauthenticated hold
cancel. Session-gated (Argon2 cookie + CSRF): Policy Studio, Agent Console,
WebAuthn internal ceremony, step-up complete. Bearer-gated: step-up begin.
"""

from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from openstore.auth import require_bearer, require_session
from openstore.blast_radius import compute_blast_radius
from openstore.errors import (
    error_envelope,
    AUTH_UNAUTHENTICATED,
    AUTH_FORBIDDEN,
    AUTH_CSRF,
    EVIDENCE_NOT_FOUND,
    HOLD_INVALID,
    CHECKOUT_INVALID,
    OpenStoreError,
)
from openstore.models import Quote
from sqlmodel import Session, select

STATIC = Path(__file__).parent / "static"


def _status_for_code(code: str) -> int:
    """Map an OpenStoreError code namespace to an HTTP status."""
    namespace = code.split(".", 1)[0]
    return {
        "auth": 401,
        "policy": 403,
        "checkout": 422,
        "psp": 400,
        "ratelimit": 429,
        "hold": 422,
        "evidence": 404,
        "orchestration": 502,
        "delegation": 403,
        "spendchain": 409,
        "verifier": 422,
    }.get(namespace, 400)


def _bearer_checkout(request: Request, authorization: str | None = None):
    return require_bearer("checkout:confirm", request, authorization)


def register_surfaces(app, runtime, auth) -> None:
    app.state.auth = auth

    # R8.3 / PRODUCTION_READINESS §1.6 — structured error envelope with namespaces.
    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        trace_id = uuid.uuid4().hex[:16]
        return JSONResponse(status_code=400, content=error_envelope(CHECKOUT_INVALID, str(exc), trace_id=trace_id))

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        trace_id = uuid.uuid4().hex[:16]
        if exc.status_code == 401:
            return JSONResponse(status_code=401, content=error_envelope(AUTH_UNAUTHENTICATED, exc.detail, trace_id=trace_id))
        if exc.status_code == 403:
            return JSONResponse(status_code=403, content=error_envelope(AUTH_FORBIDDEN, exc.detail, trace_id=trace_id))
        return JSONResponse(status_code=exc.status_code, content=error_envelope(CHECKOUT_INVALID, exc.detail, trace_id=trace_id))

    @app.exception_handler(OpenStoreError)
    async def _openstore_error(request: Request, exc: OpenStoreError):
        status = _status_for_code(exc.code)
        return JSONResponse(status_code=status, content=exc.to_envelope())

    # ---------- Public (none) ----------

    @app.get("/.well-known/poai-jwks.json")
    def poai_jwks():
        return runtime.poai_jwks()

    @app.get("/.well-known/agent-policy.json")
    def agent_policy():
        return runtime.agent_policy_doc()

    @app.get("/agents")
    def agents_page():
        doc = runtime.agent_policy_doc()
        try:
            rows = "".join(
                f"<li><code>{k}</code>: {json.dumps(v)}</li>" for k, v in doc["policy"].items()
            )
        except (TypeError, ValueError):
            rows = "<li>Policy loaded</li>"
        return HTMLResponse(
            f"<h1>Agent Policy — {doc['merchant_did']}</h1>"
            f"<p>PoAI {doc['poai_version']}</p>"
            f"<ul>{rows}</ul><p><a href='/intent/studio'>Policy Studio</a></p>"
        )

    # R9.5c — storefront fit badge: each product shows whether the signed policy covers it
    @app.get("/")
    def storefront():
        catalog = runtime.catalog
        products_html = ""
        for sku, prod in catalog.items():
            tags = prod.get("tags", [])
            name = prod.get("title", sku)
            price = prod.get("unit_price_paise", 0)
            badge = ""
            try:
                from openstore.blast_radius import compute_blast_radius
                pol = runtime.compiler_policy_dict()
                br = compute_blast_radius(pol, [{"sku": sku, "price_minor": price, "tags": tags}])
                if sku in br.get("allowed_skus", []):
                    badge = "✓ your agent can buy this"
                else:
                    badge = "✗ outside your policy"
            except Exception:
                badge = ""
            products_html += (
                f"<div style='border:1px solid #e2e8f0;padding:1rem;margin:0.5rem 0;border-radius:6px'>"
                f"<b>{name}</b> ({sku}) — ₹{price//100}<br>"
                f"<span style='color:#64748b;font-size:0.85rem'>tags: {', '.join(tags)}</span><br>"
                f"<span style='font-size:0.85rem'>{badge}</span>"
                f"</div>"
            )
        return HTMLResponse(
            f"<h1>OpenStore</h1>"
            f"<p><a href='/agents'>Agent Policy</a> · <a href='/intent/studio'>Policy Studio</a> "
            f"· <a href='/admin/agents'>Agent Console</a></p>"
            f"<h2>Products</h2>{products_html}"
        )

    # Policy revocation endpoint (PRODUCTION_READINESS §2.2)
    @app.post("/internal/policy/revoke")
    def revoke_policy(payload: dict, session: dict = Depends(require_session)):
        """Revoke the active policy by setting active=False.

        POST {"policy_id": "..."} or {"revoke_all": true}
        After revocation, any checkout_confirm using this policy must fail
        closed with policy.revoked.
        """
        from openstore.models import AgentSessionRow
        with Session(runtime.ledger.engine) as s:
            if payload.get("revoke_all"):
                # Mark all sessions as frozen (provisional revocation)
                for rec in s.exec(select(AgentSessionRow)).all():
                    rec.frozen = True
                    s.add(rec)
                s.commit()
                return {"ok": True, "revoked": "all"}
            policy_id = payload.get("policy_id")
            if policy_id:
                # Freeze sessions matching this policy hash
                count = 0
                for rec in s.exec(select(AgentSessionRow)).all():
                    if policy_id in rec.session_key:
                        rec.frozen = True
                        s.add(rec)
                        count += 1
                s.commit()
                return {"ok": True, "revoked": count}
        return JSONResponse(status_code=400, content=error_envelope(
            CHECKOUT_INVALID, "provide policy_id or revoke_all"))

    @app.get("/orders/{checkout_id}/evidence")
    def order_evidence(checkout_id: str):
        bundle = runtime.get_bundle(checkout_id)
        if bundle is None:
            return JSONResponse(status_code=404, content=error_envelope(EVIDENCE_NOT_FOUND, "no bundle"))
        return JSONResponse(
            content=bundle,
            headers={"Content-Disposition": f'attachment; filename="{bundle["bundle_id"]}.json"'},
        )

    @app.get("/orders/{checkout_id}/evidence/view")
    def order_evidence_view(checkout_id: str):
        bundle = runtime.get_bundle(checkout_id)
        if bundle is None:
            return HTMLResponse("<h1>No evidence for this order</h1>", status_code=404)
        html = (STATIC / "viewer.html").read_text()
        inject = f"<script>window.__BUNDLE__ = {json.dumps(bundle)};</script>"
        html = html.replace("</body>", inject + "</body>")
        return HTMLResponse(html)

    @app.get("/hold/{token}")
    def hold_page(token: str):
        hold = runtime.holds.get(token)
        if hold is None:
            return HTMLResponse("<h1>Unknown hold</h1>", status_code=404)
        return HTMLResponse(
            f"<h1>Hold on order {hold['order_id']}</h1>"
            f"<p>Status: <b>{hold['status']}</b> · AAL {hold['aal_level']} · "
            f"releases after {hold['hold_seconds']}s</p>"
            f"<form method='post' action='/hold/{token}/cancel'>"
            f"<button type='submit'>Cancel order</button></form>"
        )

    @app.post("/hold/{token}/cancel")
    def hold_cancel(token: str):
        try:
            res = runtime.cancel_hold(token)
        except ValueError as e:
            return JSONResponse(status_code=400, content=error_envelope(HOLD_INVALID, str(e)))
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                runtime.cancel_order(res["order_id"], "buyer cancelled via hold token")
            )
        except Exception:
            pass
        return JSONResponse(content=res)

    # ---------- Session-gated (operator) ----------

    @app.post("/admin/login")
    def admin_login(payload: dict):
        token = auth.login(payload.get("password", ""))
        if token is None:
            return JSONResponse(status_code=401, content=error_envelope(AUTH_UNAUTHENTICATED, "bad password"))
        csrf = secrets.token_urlsafe(32)
        resp = JSONResponse({"ok": True})
        resp.set_cookie("openstore_session", token, httponly=True, samesite="strict", max_age=43200)
        resp.set_cookie("openstore_csrf", csrf, samesite="strict", max_age=43200)
        return resp

    @app.get("/intent/studio")
    def policy_studio(session: dict = Depends(require_session)):
        html_path = STATIC / "policy_studio.html"
        if not html_path.exists():
            return HTMLResponse("<h1>Policy Studio</h1><p>UI not found.</p>", status_code=500)
        catalog = [{"sku": sku, "price_minor": p.get("unit_price_paise", 0),
                    "tags": p.get("tags", [])} for sku, p in runtime.catalog.items()]
        html = html_path.read_text()
        # Inject catalog and merchant ID for the client-side blast-radius preview
        import json as _json
        inject = f"<script>window.__CATALOG__={_json.dumps(catalog)};window.__MERCHANT_ID__={_json.dumps(runtime.did)};</script>"
        html = html.replace("</head>", inject + "</head>")
        return HTMLResponse(html)

    @app.post("/internal/policy/blast-radius")
    def blast_radius(payload: dict, session: dict = Depends(require_session)):
        policy = payload.get("policy", runtime.compiler_policy_dict())
        catalog = [{"sku": sku, "price_minor": p.get("unit_price_paise", 0),
                    "tags": p.get("tags", [])} for sku, p in runtime.catalog.items()]
        return compute_blast_radius(policy, catalog)

    @app.post("/internal/webauthn/challenge")
    def wa_challenge(payload: dict, session: dict = Depends(require_session)):
        return runtime.webauthn.begin_registration()

    @app.post("/internal/webauthn/register")
    def wa_register(payload: dict, session: dict = Depends(require_session)):
        cid = runtime.webauthn.register_credential(
            attestation_object=payload["attestation_object"],
            client_data_json=payload["client_data_json"], session_id=payload["session_id"])
        return {"credential_id": cid}

    @app.post("/internal/webauthn/begin-signing")
    def wa_begin_signing(payload: dict, session: dict = Depends(require_session)):
        q = runtime._order_context(Quote.model_validate(payload["quote"]))
        return runtime.webauthn.begin_assertion(q["policy_dict"], q["cart_hash"])

    @app.post("/internal/webauthn/complete-signing")
    def wa_complete_signing(payload: dict, session: dict = Depends(require_session)):
        q = runtime._order_context(Quote.model_validate(payload["quote"]))
        authority = runtime.webauthn.complete_assertion(
            payload["session_id"], payload["assertion"],
            policy=q["policy_dict"], cart_hash=q["cart_hash"])
        return authority

    @app.get("/admin/agents")
    def agent_console(session: dict = Depends(require_session)):
        html_path = STATIC / "agents_console.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text())
        sessions = runtime.list_agent_sessions()
        rej = runtime.list_rejections()
        s_rows = "".join(
            f"<li>{s['session_key'][:12]}… client={s['client_id']} scopes={s['scopes']} "
            f"frozen={s['frozen']}</li>" for s in sessions)
        r_rows = "".join(f"<li>{r['order_id']} {r['reason_code']} — {r['detail']}</li>" for r in rej)
        return HTMLResponse(
            f"<h1>Agent Console</h1><h2>Sessions</h2><ul>{s_rows or '<li>none</li>'}</ul>"
            f"<h2>Rejections</h2><ul>{r_rows or '<li>none</li>'}</ul>"
        )

    @app.get("/internal/agents/sessions")
    def agents_sessions(session: dict = Depends(require_session)):
        return {"sessions": runtime.list_agent_sessions()}

    @app.get("/internal/agents/rejections")
    def agents_rejections(session: dict = Depends(require_session)):
        return {"rejections": runtime.list_rejections()}

    @app.post("/internal/agents/freeze")
    def agents_freeze(payload: dict, session: dict = Depends(require_session)):
        runtime.freeze_agent_session(payload["session_key"], bool(payload.get("frozen", True)))
        return {"ok": True}

    # ---------- Bearer / session step-up ----------
    # IMPLEMENTATION_SPEC §5.2 — step-up ceremony bound to cart_hash

    @app.post("/intent/step-up/begin")
    def step_up_begin(payload: dict, session: dict = Depends(_bearer_checkout)):
        """R8 step-up begin — start a WebAuthn assertion bound to cart_hash (§5.2 mode: cart)."""
        cart_hash = payload.get("cart_hash")
        if not cart_hash:
            return JSONResponse(status_code=400, content=error_envelope(
                CHECKOUT_INVALID, "cart_hash required for step-up"))
        checkout_id = payload.get("checkout_id", "")
        policy_dict = runtime._merchant_policy_dict()
        result = runtime.webauthn.begin_assertion(policy_dict, cart_hash=cart_hash)
        result["checkout_id"] = checkout_id
        return result

    @app.post("/intent/step-up/complete")
    def step_up_complete(payload: dict, session: dict = Depends(require_session)):
        """R8 step-up complete — verify the assertion and return the authority."""
        session_id = payload.get("session_id")
        assertion = payload.get("assertion")
        checkout_id = payload.get("checkout_id", "")
        cart_hash = payload.get("cart_hash")
        if not session_id or not assertion:
            return JSONResponse(status_code=400, content=error_envelope(
                CHECKOUT_INVALID, "session_id and assertion required"))
        policy_dict = runtime._merchant_policy_dict()
        authority = runtime.webauthn.complete_assertion(
            session_id, assertion, policy=policy_dict, cart_hash=cart_hash)
        return {"status": "signed", "checkout_id": checkout_id, "authority": authority}
