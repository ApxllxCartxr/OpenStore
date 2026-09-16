# Demo-only surfaces, mounted ONLY when config.demo_mode is true.
#
# Two jobs, both of which exist to remove a credential from the evaluation
# path without removing the machinery underneath it:
#
#   /demo/pay/<link id>   the fake PSP page a demo payment link points at.
#                         Its buttons emit a REAL HMAC-signed webhook through
#                         process_incoming_webhook — same signature check,
#                         same transition table, same ledger entries.
#
#   /demo/webauthn/*      a server-side ceremony driven by devtools'
#                         VirtualAuthenticator, reached through a shim that
#                         replaces navigator.credentials in the browser. The
#                         registration and assertion are really verified by
#                         py_webauthn; what is faked is the hardware, not the
#                         cryptography. No studio template is touched.
#
# Nothing here is importable into a real deployment's request path: create_app
# mounts this router only under demo_mode, and refuses demo_mode beside a
# non-demo key.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from openstore.config import Settings
from openstore.devtools.virtual_authenticator import (
    VirtualAuthenticator,
    generate_es256_authenticator,
)
from openstore.psp import demo_driver

# One virtual device for the sitting, built lazily so rp_id/origin come from
# the running config. A real authenticator is likewise one device across many
# ceremonies, and the sign counter has to keep climbing across them.
_AUTHENTICATOR: VirtualAuthenticator | None = None

SHIM_JS = """\
// Demo passkey shim — replaces navigator.credentials with a call to the
// server-side VirtualAuthenticator. Only loaded under `openstore demo`.
"use strict";
(function () {
  const b64u = (buf) => {
    const bytes = new Uint8Array(buf);
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
  };
  const b64d = (s) => {
    const pad = s.replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(pad + "===".slice((pad.length + 3) % 4));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  };
  const post = async (path, body) => {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("demo passkey failed: " + res.status);
    return res.json();
  };
  navigator.credentials.create = async (options) => {
    const r = await post("/demo/webauthn/create", {
      challenge: b64u(options.publicKey.challenge),
    });
    return {
      id: r.credential_id,
      rawId: b64d(r.credential_id),
      type: "public-key",
      response: {
        clientDataJSON: b64d(r.client_data_json),
        attestationObject: b64d(r.attestation_object),
      },
    };
  };
  navigator.credentials.get = async (options) => {
    const r = await post("/demo/webauthn/get", {
      challenge: b64u(options.publicKey.challenge),
    });
    return {
      id: r.credential_id,
      rawId: b64d(r.credential_id),
      type: "public-key",
      response: {
        clientDataJSON: b64d(r.client_data_json),
        authenticatorData: b64d(r.authenticator_data),
        signature: b64d(r.signature),
      },
    };
  };
})();
"""

BANNER = (
    '<div style="position:fixed;bottom:0;left:0;right:0;z-index:9999;'
    "background:#1f2933;color:#fff;font:13px/1.5 system-ui,sans-serif;"
    'padding:.6rem 1rem;display:flex;gap:1rem;align-items:center">'
    "<b>DEMO</b><span>Payments are simulated and passkeys come from a virtual "
    "authenticator. Nothing here reaches a real PSP.</span></div>"
)


class _Ceremony(BaseModel):
    challenge: str


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _authenticator(config: Settings) -> VirtualAuthenticator:
    global _AUTHENTICATOR
    if _AUTHENTICATOR is None:
        _AUTHENTICATOR = generate_es256_authenticator(
            rp_id=config.webauthn.rp_id,
            origin=config.webauthn.origin,
        )
    return _AUTHENTICATOR


def reset() -> None:
    """Forget the virtual device (a fresh sitting, or a test)."""
    global _AUTHENTICATOR
    _AUTHENTICATOR = None
    demo_driver.reset()


def _signed_webhook(config: Settings, event: str, link: dict[str, Any], payment_id: str) -> str:
    """Emit a signed webhook the way POST /webhooks/razorpay handles one.

    Verify the signature, persist the raw event, then run the SAME worker the
    route schedules as a background task — the worker is where the evidence
    bundle is built, so calling only process_incoming_webhook would leave a
    demo order proof-less, which is most of what there is to show.
    """
    from openstore.core.database import get_session
    from openstore.psp.razorpay_driver import persist_raw_webhook_event, verify_webhook_signature
    from openstore.psp.router import _process_event_in_worker

    payload: dict[str, Any] = {
        "event": event,
        "payload": {"payment_link": {"entity": link}},
    }
    if event == "payment_link.paid":
        payload["payload"]["payment"] = {"entity": {"id": payment_id, "status": "captured"}}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    secret = config.razorpay.webhook_secret or demo_driver.DEMO_WEBHOOK_SECRET
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    if not verify_webhook_signature(raw, signature, secret):
        raise HTTPException(status_code=500, detail="demo webhook signature mismatch")

    event_id = f"evt_demo{secrets.token_hex(8)}"
    session = get_session(config)
    try:
        persist_raw_webhook_event(
            session=session,
            raw_body=raw,
            signature=signature,
            x_event_id=event_id,
            trace_id=f"demo_{link['reference_id'][:16]}",
            client_id="demo",
        )
        session.commit()
    finally:
        session.close()
    _process_event_in_worker(event_id, config)
    return event_id


def _pay_page(config: Settings, link: dict[str, Any]) -> str:
    amount = f"{link['amount'] / 100:,.2f}"
    paid = link["status"] == "paid"
    cancelled = link["status"] == "cancelled"
    if paid or cancelled:
        state = "Paid" if paid else "Cancelled"
        actions = f'<p class="done">{state}. ' '<a href="/">Back to the storefront</a></p>'
    else:
        # Action rides the query string, not a form body: FastAPI's Form()
        # would pull in python-multipart for two buttons.
        link_id = link["id"]
        actions = (
            f'<form method="post" action="/demo/pay/{link_id}?action=paid">'
            f'<button class="pay">Pay &#8377;{amount}</button></form>'
            f'<form method="post" action="/demo/pay/{link_id}?action=cancelled">'
            '<button class="alt">Cancel payment</button></form>'
        )
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo payment — {link["reference_id"]}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.6 system-ui, sans-serif; max-width: 26rem;
         margin: 4rem auto; padding: 0 1.5rem; }}
  .card {{ border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
           border-radius: 10px; padding: 1.5rem; }}
  .amount {{ font-size: 2rem; font-weight: 600; margin: .25rem 0 1rem; }}
  .muted {{ opacity: .65; font-size: .85rem; }}
  form {{ display: inline; }}
  button {{ font: inherit; padding: .6rem 1rem; border-radius: 7px;
            border: 1px solid transparent; cursor: pointer; margin-right: .5rem; }}
  .pay {{ background: #1a7f37; color: #fff; }}
  .alt {{ background: transparent; border-color: currentColor; }}
  .done {{ font-weight: 600; }}
</style>
<div class="card">
  <p class="muted">Demo payment gateway</p>
  <p class="amount">&#8377;{amount}</p>
  <p class="muted">Order {link["reference_id"]}<br>{link["description"]}</p>
  {actions}
</div>
<p class="muted">This page stands in for the PSP's hosted checkout. Paying here
emits a signed <code>payment_link.paid</code> webhook into the same handler a
live payment would.</p>
"""


def demo_router(config: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/demo/shim.js")
    async def shim() -> PlainTextResponse:
        return PlainTextResponse(SHIM_JS, media_type="application/javascript")

    @router.post("/demo/webauthn/create")
    async def webauthn_create(body: _Ceremony) -> dict[str, Any]:
        auth = _authenticator(config)
        result = auth.register(_b64u_decode(body.challenge))
        return {
            "credential_id": result.credential_id,
            "client_data_json": result.client_data_json,
            "attestation_object": result.attestation_object,
        }

    @router.post("/demo/webauthn/get")
    async def webauthn_get(body: _Ceremony) -> dict[str, Any]:
        auth = _authenticator(config)
        # The RP rejects a sign counter that fails to advance, so the virtual
        # device increments exactly as real hardware does.
        result = auth.assert_credential(
            _b64u_decode(body.challenge), sign_count=auth.sign_count + 1
        )
        return {
            "credential_id": result.credential_id,
            "client_data_json": result.client_data_json,
            "authenticator_data": result.authenticator_data,
            "signature": result.signature,
        }

    @router.get("/demo/pay/{link_id}", response_class=HTMLResponse)
    async def pay_page(link_id: str) -> HTMLResponse:
        link = demo_driver.get_link(link_id)
        if link is None:
            raise HTTPException(status_code=404, detail="unknown payment link")
        return HTMLResponse(_pay_page(config, link))

    @router.post("/demo/pay/{link_id}", response_class=HTMLResponse)
    async def pay_action(link_id: str, action: str = "paid") -> HTMLResponse:
        link = demo_driver.get_link(link_id)
        if link is None:
            raise HTTPException(status_code=404, detail="unknown payment link")
        if link["status"] != "created":
            return HTMLResponse(_pay_page(config, link))
        # The worker MUST NOT run on the event-loop thread: notifier
        # .run_from_worker_thread schedules its coroutine onto that same loop
        # and blocks for the result, which deadlocks if we are already on it.
        # BackgroundTasks gets this right by running sync callables in a
        # threadpool; run_in_threadpool is the same move, awaited so the page
        # can render the settled state instead of racing it.
        if action == "paid":
            updated = demo_driver.mark_paid(link_id, f"pay_demo{secrets.token_hex(8)}")
            await run_in_threadpool(
                _signed_webhook,
                config,
                "payment_link.paid",
                updated,
                updated["payments"][0]["id"],
            )
        elif action == "cancelled":
            updated = demo_driver.get_link(link_id) or {}
            updated["status"] = "cancelled"
            await run_in_threadpool(_signed_webhook, config, "payment_link.cancelled", updated, "")
        else:
            raise HTTPException(status_code=422, detail="unknown action")
        refreshed = demo_driver.get_link(link_id) or {}
        return HTMLResponse(_pay_page(config, refreshed))

    return router
