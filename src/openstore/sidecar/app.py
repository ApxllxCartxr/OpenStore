"""FastAPI wiring. At A1 this is the skeleton: health, readiness, and nothing
that touches money.

Routes are mounted behind the reverse proxy split (SPEC §10): `/` goes to the
store, and `/.well-known/*`, `/agent/*` and `/agentic/*` come here, on the
Merchant's own origin so passkeys and tap tokens are bound to the domain the
Consumer is actually looking at.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openstore.sidecar.console.routes import router as console_router
from openstore.sidecar.core.settings import Settings, get_settings
from openstore.sidecar.evidence.store import ReceiptStore, get_receipt_store
from openstore.sidecar.verify.checks import verify

app = FastAPI(
    title="OpenStore sidecar",
    description="Makes one Merchant site transactable by any Buyer Agent.",
    version="0.1.0",
    docs_url=None,  # no interactive docs on a money surface
    redoc_url=None,
)


app.include_router(console_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Says nothing about whether it can work."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: configuration loaded and the dangerous combinations refused.

    Reports the dev SSRF allowlist when it is in use, because SPEC §14 requires
    it to be visible in health output — an exception nobody can see is an
    exception that outlives its reason.
    """
    try:
        settings: Settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - the reason must reach the operator
        return JSONResponse(
            status_code=503,
            content={"status": "not-ready", "reason": str(exc)},
        )

    body: dict[str, Any] = {
        "status": "ready",
        "demo_mode": settings.openstore_demo_mode,
        "merchant_domain": settings.openstore_merchant_domain or None,
        "checks": {
            "settings": "ok",
            # A2 adds the trait, A3 the database and provider. Each lands as its
            # own named check rather than widening this one, so a partial outage
            # is legible at 2am (SPEC §14).
        },
    }
    if settings.dev_profile_hosts:
        body["warnings"] = [
            {
                "code": "dev-profile-allowlist-active",
                "hosts": list(settings.dev_profile_hosts),
                "detail": (
                    "The SSRF profile-fetch exception is enabled. Development only — "
                    "every fetch that uses it is logged and flagged in /agentic."
                ),
            }
        ]
    return JSONResponse(status_code=200, content=body)


@app.get("/receipt/{receipt_id}")
def receipt(receipt_id: str) -> JSONResponse:
    """The public receipt viewer.

    **Outside the console's auth boundary, deliberately.** `/agentic` is
    session-authenticated for the Merchant, and a receipt that opens by
    unguessable ID *with no login* is the whole point — putting it there would
    mean no Consumer could ever open their own. The ID is the only credential
    and 128 bits is the protection.

    A missing receipt and a wrong id answer identically, because the difference
    between them is exactly the oracle the unguessable id exists to close.
    """
    store: ReceiptStore = get_receipt_store()
    bundle = store.get(receipt_id)
    if bundle is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not-found", "detail": "No receipt with that id."}},
        )

    result = verify(bundle)
    return JSONResponse(
        status_code=200,
        content={
            "receipt": bundle.to_dict(),
            "verification": {
                "status": result.exit_code.name,
                "claims": result.claims,
                "unopened": result.unopened,
                "findings": [
                    {"ok": f.ok, "label": f.label, "detail": f.detail} for f in result.findings
                ],
            },
        },
    )
