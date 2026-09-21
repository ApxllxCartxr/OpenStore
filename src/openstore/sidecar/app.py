"""FastAPI wiring. At A1 this is the skeleton: health, readiness, and nothing
that touches money.

Routes are mounted behind the reverse proxy split (SPEC §10): `/` goes to the
store, and `/.well-known/*`, `/agent/*` and `/agentic/*` come here, on the
Merchant's own origin so passkeys and tap tokens are bound to the domain the
Consumer is actually looking at.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.console.approve import router as approve_router
from openstore.sidecar.console.merchant_actions import router as merchant_actions_router
from openstore.sidecar.console.routes import router as console_router
from openstore.sidecar.core.settings import Settings, get_settings
from openstore.sidecar.evidence.keys import Keyring, load_or_enroll
from openstore.sidecar.evidence.store import ReceiptStore, get_receipt_store
from openstore.sidecar.protocols.agent_routes import get_surface
from openstore.sidecar.protocols.agent_routes import router as agent_router
from openstore.sidecar.verify.checks import verify


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Point the agent surface at this deployment's Merchant.

    At startup rather than at import, so settings are read once after the
    process has decided what it is. `lifespan` rather than the deprecated
    `on_event` — and it **logs what it wired**, because a feed that silently
    serves nothing looks like an empty shop rather than a misconfiguration.
    """
    import httpx

    from openstore.sidecar.admission.oauth import Admission
    from openstore.sidecar.admission.profile import ProfileFetcher
    from openstore.sidecar.checkout import CheckoutContext
    from openstore.sidecar.checkout import configure as configure_checkout
    from openstore.sidecar.console.approve import ApproveContext
    from openstore.sidecar.console.approve import configure as configure_approve
    from openstore.sidecar.core.db import create_all, make_engine, make_sessionmaker
    from openstore.sidecar.evidence.store import get_receipt_store
    from openstore.sidecar.gate.policy import Policy
    from openstore.sidecar.protocols.agent_routes import AgentSurface
    from openstore.sidecar.protocols.agent_routes import configure as configure_surface
    from openstore.sidecar.trait.client import TraitClient

    settings = get_settings()
    trait = (
        TraitClient(
            settings.trait_base_url,
            settings.trait_hmac_secret,
            client=httpx.AsyncClient(timeout=10),
        )
        if settings.trait_base_url
        else None
    )
    merchant_domain = settings.openstore_merchant_domain or "localhost"
    keyring = _load_keyring(settings, merchant_domain)
    configure_surface(
        AgentSurface(
            merchant_domain=merchant_domain,
            merchant_name=settings.webauthn_rp_name or "This shop",
            public_origin=settings.openstore_public_origin,
            demo=settings.openstore_demo_mode,
            trait=trait,
            jwks=keyring.jwks() if keyring else {"keys": []},
            keyring=keyring,
            # The allowlist was reported in health output and the console banner
            # while the fetcher that enforces it held an empty tuple, so the
            # named exception refused the one host it exists for and no agent
            # could register at all. Reporting a policy is not applying it.
            fetcher=ProfileFetcher(dev_hosts=settings.dev_profile_hosts),
            # Same story as the allowlist: the credentials were configurable and
            # reached nothing, so the OAuth route refused the very client the
            # deploy had been given.
            admission=Admission(
                clients=(
                    {settings.oauth_client_id: settings.oauth_client_secret}
                    if settings.oauth_client_id and settings.oauth_client_secret
                    else {}
                )
            ),
        )
    )
    # The money path. Until this existed the Gate, the Ledger, the Provider and
    # the receipt were reachable only from a test.
    engine = None
    sessionmaker = None
    if settings.sidecar_database_url:
        engine = make_engine(settings.sidecar_database_url)
        await create_all(engine)
        sessionmaker = make_sessionmaker(engine)
    else:
        logging.getLogger("openstore").warning(
            "NO DATABASE: SIDECAR_DATABASE_URL is unset, so there is no Ledger and no "
            "order can be taken."
        )

    provider = _provider_for(settings)
    policy = Policy()
    tokens = TokenStore()
    checkout_context = CheckoutContext(
        trait=trait,
        policy=policy,
        tokens=tokens,
        provider=provider,
        keyring=keyring,
        sessionmaker=sessionmaker,
        receipts=get_receipt_store(),
        merchant_domain=merchant_domain,
        public_origin=settings.openstore_public_origin,
        deploy_pseudonym_key=settings.deploy_pseudonym_key.encode(),
        demo=settings.openstore_demo_mode,
    )
    configure_checkout(checkout_context)
    # The approve page reads the same token store the tap spends from — two
    # stores would render one token and refuse another.
    configure_approve(
        ApproveContext(
            tokens=tokens,
            quotes=_QuoteView(checkout_context),
            merchant_domain=merchant_domain,
            merchant_name=settings.webauthn_rp_name or "This shop",
            enabled_methods=policy.enabled_methods,
            demo=settings.openstore_demo_mode,
        )
    )

    logging.getLogger("openstore").warning(
        "money path wired: db=%s provider=%s signing_key=%s",
        "yes" if sessionmaker else "NOT CONFIGURED - no order can be taken",
        provider.name,
        "yes" if keyring else "NOT CONFIGURED - nothing can be sealed",
    )
    logging.getLogger("openstore").warning(
        "agent surface wired: merchant=%s trait=%s",
        settings.openstore_merchant_domain or "unset",
        settings.trait_base_url or "NOT CONFIGURED - the feed will be empty",
    )
    yield
    if engine is not None:
        await engine.dispose()


class _QuoteView(dict):  # type: ignore[type-arg]
    """The approve page's quote lookup, backed by live checkouts.

    `ApproveContext.quotes` is a plain dict of pre-computed quotes, which is
    right for a unit test and wrong for a running shop: the page must render the
    Quote the Merchant produced for *this* checkout. This reads it through
    rather than copying it, so a re-quote cannot leave the page showing a stale
    total.
    """

    def __init__(self, context: Any) -> None:
        super().__init__()
        self._context = context

    def get(self, key: Any, default: Any = None) -> Any:  # noqa: D102
        checkout = self._context.pending.get(key)
        return checkout.quote.model_dump(mode="json") if checkout else default


def _provider_for(settings: Settings) -> Any:
    """The Provider this deploy talks to (ADR-0013).

    `PAYMENT_PROVIDER` was a declared setting that reached nothing, so the
    adapter was never chosen at all. `razorpay` refuses live keys in demo mode
    at its own boot, which is where that check belongs.
    """
    if settings.payment_provider == "razorpay":
        from openstore.sidecar.provider.razorpay import RazorpayProvider

        return RazorpayProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            demo_mode=settings.openstore_demo_mode,
        )
    from openstore.sidecar.provider.fake import FakeProvider

    return FakeProvider()


def _load_keyring(settings: Settings, merchant_domain: str) -> Keyring | None:
    """The Merchant's signing keys, read from disk or minted once (ADR-0014).

    With no keyfile path configured there is no key, the JWKS is empty, and
    nothing can be sealed. That is reported here and at `/healthz` rather than
    discovered by an agent whose refusal says only that this shop carries no
    keys — which is how it was discovered.
    """
    log = logging.getLogger("openstore")
    if not settings.sidecar_signing_key_path:
        log.warning(
            "NO SIGNING KEY: SIDECAR_SIGNING_KEY_PATH is unset, so the JWKS is empty, "
            "no receipt can be sealed, and agents pinning this shop's keys will refuse it."
        )
        return None

    keyring, enrolled = load_or_enroll(
        Path(settings.sidecar_signing_key_path),
        merchant_domain=merchant_domain,
        passphrase=settings.sidecar_signing_key_passphrase,
    )
    log.warning(
        "signing keys %s: merchant=%s kid=%s path=%s",
        "ENROLLED (first boot for this keyfile)" if enrolled else "loaded",
        merchant_domain,
        keyring.current.kid,
        settings.sidecar_signing_key_path,
    )
    return keyring


app = FastAPI(
    lifespan=lifespan,
    title="OpenStore sidecar",
    description="Makes one Merchant site transactable by any Buyer Agent.",
    version="0.1.0",
    docs_url=None,  # no interactive docs on a money surface
    redoc_url=None,
)


# Order matters: the console's /agentic/{tab} catch-all would otherwise swallow
# every sibling route and answer "no console tab". The approve page in
# particular is authenticated by its one-time token and NOT by the Merchant
# session, so it must reach its own handler.
app.include_router(agent_router)
app.include_router(approve_router)
app.include_router(merchant_actions_router)
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
            # An empty JWKS is invisible from the outside until an agent refuses
            # the shop for carrying no keys. It is a check here for that reason.
            "signing_key": "ok" if get_surface().jwks.get("keys") else "absent",
            # A2 adds the trait, A3 the database and provider. Each lands as its
            # own named check rather than widening this one, so a partial outage
            # is legible at 2am (SPEC §14).
        },
    }
    warnings: list[dict[str, Any]] = []
    if body["checks"]["signing_key"] == "absent":
        warnings.append(
            {
                "code": "no-signing-key",
                "detail": (
                    "This sidecar holds no signing key, so its JWKS is empty, no receipt can "
                    "be sealed, and an agent pinning this shop's keys will refuse it. Set "
                    "SIDECAR_SIGNING_KEY_PATH."
                ),
            }
        )
    if settings.dev_profile_hosts:
        warnings.append(
            {
                "code": "dev-profile-allowlist-active",
                "hosts": list(settings.dev_profile_hosts),
                "detail": (
                    "The SSRF profile-fetch exception is enabled. Development only — "
                    "every fetch that uses it is logged and flagged in /agentic."
                ),
            }
        )
    if warnings:
        body["warnings"] = warnings
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
