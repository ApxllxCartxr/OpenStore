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

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.core.settings import Settings, get_settings

app = FastAPI(
    title="OpenStore sidecar",
    description="Makes one Merchant site transactable by any Buyer Agent.",
    version="0.1.0",
    docs_url=None,  # no interactive docs on a money surface
    redoc_url=None,
)


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


@app.get("/agentic/codes")
def codes() -> dict[str, list[str]]:
    """The closed reason-code set, served so an operator can check what a
    refusal they were handed actually means. Generated from the enum — there is
    no second list."""
    return {"reason_codes": [c.value for c in ReasonCode]}
