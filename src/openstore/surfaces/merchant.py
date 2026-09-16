# OpenStore — Merchant console (S24 / Q-044, Q-045, DECISION-044/045).
#
# Every merchant task browser-doable after `openstore serve` (D5). All pages
# are session-gated except /merchant/login (claim/sign-in) and the
# read-only step guide /merchant/setup. Mutations ride POST on the SAME
# path as their page (no new route identifiers beyond the ratified ten).
#
# Form-validation failures (bad price, unknown setting, rename of a live
# store) answer 422 with reason_code "webhook.invalid_payload" — the
# registered generic malformed-payload code. Deliberate compromise, recorded
# in DECISION-044: minting a settings.* namespace for these would have added
# identifiers beyond the Q-044 ratification. Domain validations keep their
# own codes (catalog.sku_not_found, campaign.* via the campaign agent path).

from __future__ import annotations

import html as _html
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from openstore.config import Settings, merchant_id
from openstore.core.api import CommerceError
from openstore.core.database import get_session
from openstore.core.session import (
    CSRF_HEADER,
    SESSION_COOKIE,
    check_csrf,
    csrf_token_for,
    mint_session,
    revoke_session,
    store_claimed,
    validate_session,
)
from openstore.core.settings_overlay import apply_overlay, read_overlay
from openstore.core.webauthn_rp import (
    ChallengeStore,
    WebAuthnError,
    begin_assertion,
    complete_assertion,
)
from openstore.models import Checkout, IntentPolicy, MerchantSetting
from openstore.surfaces.render import render_template, safe_json

SESSION_HARD_CAP_SECONDS = 7 * 86400


class LoginRequest(BaseModel):
    action: str  # "begin" | "complete"
    operator_id: str
    claim: bool = False  # True only for the first-passkey TOFU claim flow
    credential_id: str | None = None
    client_data_json: str | None = None
    authenticator_data: str | None = None
    signature: str | None = None
    challenge: str | None = None


class CatalogMutation(BaseModel):
    action: str  # "add" | "update" | "delete"
    sku: str
    name: str | None = None
    unit_minor: int | None = None
    tags: list[str] | None = None
    related_skus: list[str] | None = None
    description: str | None = None


class OrderAction(BaseModel):
    action: str  # "share" | "revoke-share" | "cancel"
    checkout_id: str


class CampaignAction(BaseModel):
    action: str  # "draft" | "growth-check"
    calendar_event: str | None = None
    days: int = 7


class SettingsForm(BaseModel):
    merchant_name: str | None = None
    campaign_min_bps: int | None = None
    campaign_max_bps: int | None = None
    campaign_max_active: int | None = None
    evidence_share_ttl_days: int | None = None
    webauthn_rp_id: str | None = None
    webauthn_origin: str | None = None
    public_base_url: str | None = None
    catalog_source: dict[str, Any] | None = None


class SetupAction(BaseModel):
    action: str  # "test"
    source: dict[str, Any]


# Non-secret keys editable from the browser. Secrets stay in .env
# (presence/absence only in the UI). Maps overlay key -> Settings path.
SETTABLE_KEYS = frozenset(
    {
    "merchant.name",
    "campaign.min_bps",
    "campaign.max_bps",
    "campaign.max_active",
    "evidence_share_ttl_days",
    "webauthn.rp_id",
    "webauthn.origin",
    "public_base_url",
    "catalog_source",
    }
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"reason_code": code, "message": message})


def merchant_router(
    config: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
    challenge_store: ChallengeStore | None = None,
) -> APIRouter:
    """Build the merchant-console APIRouter (evidence.py router-factory shape)."""
    make_session = session_factory or (lambda: get_session(config))
    login_store = challenge_store or ChallengeStore()

    router = APIRouter()

    # ------------------------------------------------------------ internals
    def _commerce_to_http(e: CommerceError) -> HTTPException:
        return _fail(e.status_code, e.reason_code, e.message)

    def _need_session(request: Request) -> tuple[str, str]:
        """Valid session cookie -> (operator_id, raw token). Plain values, not
        the ORM row: the session closes before the handler renders, and a
        detached row would raise DetachedInstanceError on attribute access."""
        raw = request.cookies.get(SESSION_COOKIE)
        db = make_session()
        try:
            try:
                row = validate_session(db, raw, request.headers.get("user-agent"))
            except CommerceError as e:
                db.rollback()
                raise _commerce_to_http(e) from e
            db.commit()  # persist the sliding-window refresh
            db.refresh(row)
            operator_id = row.operator_id
            assert raw is not None
            return operator_id, raw
        finally:
            db.close()

    def _need_csrf(request: Request, raw: str) -> None:
        try:
            check_csrf(raw, request.headers.get(CSRF_HEADER))
        except CommerceError as e:
            raise _commerce_to_http(e) from e

    def _page(request: Request, title: str, body: str, active: str) -> HTMLResponse:
        raw = request.cookies.get(SESSION_COOKIE)
        return render_template(
            "merchant",
            title,
            body,
            active=active,
            csrf_token=csrf_token_for(raw) if raw else "",
        )

    def _set_cookie(resp: HTMLResponse | JSONResponse, raw: str) -> None:
        origin = (config.public_base_url or config.webauthn.origin or "")
        resp.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=SESSION_HARD_CAP_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
            secure=origin.startswith("https"),
        )

    # ------------------------------------------------------------ overlay
    def _overlay_all(db: Session) -> dict[str, str]:
        return read_overlay(db)

    def _effective(config_base: Settings) -> Settings:
        """Request-time effective config: DB overlay over YAML (DECISION-045).
        A copy — the shared process config is never mutated per-request.
        Overlay parsing itself lives in core/settings_overlay.py so the
        buyer-facing catalog path (load_catalog) applies identical rules —
        make_session() here only preserves this router's test session_factory
        override; the parsing logic is shared, not re-implemented."""
        db = make_session()
        try:
            overlay = _overlay_all(db)
        finally:
            db.close()
        return apply_overlay(config_base, overlay)

    def _current_source_json(eff: Settings) -> str:
        """Effective catalog source as JSON for the settings form."""
        import json as _json

        if eff.catalog_source is not None:
            return _json.dumps(eff.catalog_source.model_dump(), separators=(",", ":"))
        if eff.shopify is not None:
            return _json.dumps(
                {"type": "shopify", "store_domain": eff.shopify.store_domain,
                 "client_id": "***", "client_secret": "***"},
                separators=(",", ":"),
            )
        return ""

    def _check_sid5(rp_id: str, public_base_url: str | None) -> None:
        """Pre-validate the SID-5 rule in-page (DEF-14): a mismatch must be a
        readable refusal here, never a boot-time traceback later."""
        if public_base_url:
            host = urlparse(public_base_url).hostname
            if host and host != rp_id:
                raise _fail(
                    422,
                    "webhook.invalid_payload",
                    "SID-5 origin mismatch refused: public_base_url host "
                    f"({host}) != webauthn.rp_id ({rp_id}). Fix one of the "
                    "two — the server would refuse to boot with this pair.",
                )

    # ------------------------------------------------------------ dashboard
    @router.get("/merchant", response_class=HTMLResponse)
    async def merchant_home(request: Request) -> HTMLResponse:
        operator_id, _raw = _need_session(request)
        eff = _effective(config)
        db = make_session()
        try:
            day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
            todays = db.exec(
                select(Checkout).where(Checkout.created_at >= day_start)
            ).all()
            revenue = sum(
                c.amount_minor
                for c in todays
                if str(getattr(c.state, "value", c.state)) in ("RELEASED", "PAID")
            )
            from openstore.models import Campaign, CampaignState

            active_campaigns = len(
                db.exec(
                    select(Campaign).where(Campaign.state == CampaignState.ACTIVE)
                ).all()
            )
            recent_evidence = [
                c
                for c in db.exec(
                    select(Checkout)
                    .order_by(Checkout.created_at.desc())  # type: ignore[attr-defined]
                    .limit(50)
                ).all()
                if c.poai_bundle is not None
            ][:5]
            from openstore.surfaces.catalog import load_catalog

            catalog_count = len(load_catalog(config))
            claimed = store_claimed(db)
        finally:
            db.close()
        readiness = [
            ("Passkey registered", claimed),
            ("Catalog non-empty", catalog_count > 0),
            ("Razorpay test keys", eff.razorpay.key_id.startswith("rzp_test")),
            ("Discord token set", bool(eff.discord.bot_token and eff.discord.bot_token != "token")),
        ]
        body = (
            f"<h1>{_html.escape(eff.merchant.name)}</h1>"
            '<p class="lede">Merchant console — signed in as '
            f"{_html.escape(operator_id)}.</p>"
            '<div class="card"><h2>Today</h2>'
            f"<p>{len(todays)} order(s), revenue "
            f"₹{revenue / 100:.2f} · {active_campaigns} active campaign(s) · "
            f"{catalog_count} SKU(s)</p></div>"
            '<div class="card"><h2>Store readiness</h2><table>'
            + "".join(
                f"<tr><td>{_html.escape(label)}</td>"
                f"<td>{'✓' if ok else '✗ missing'}</td></tr>"
                for label, ok in readiness
            )
            + "</table></div>"
            '<div class="card"><h2>Stock</h2>'
            '<p class="muted">Stock tracking lands in Stage 26. '
            "Until then the catalog below is the source of truth.</p></div>"
            '<div class="card"><h2>Recent evidence</h2>'
            + (
                "".join(
                    f'<p><a href="/orders/{c.id}/evidence/view">'
                    f"{_html.escape(c.id)}</a> "
                    f"<span class='muted'>{_html.escape(str(getattr(c.state, 'value', c.state)))}</span></p>"
                    for c in recent_evidence
                )
                or '<p class="muted">No evidence bundles yet.</p>'
            )
            + "</div>"
        )
        return _page(request, "Dashboard", body, "/merchant")

    # ---------------------------------------------------------------- login
    @router.get("/merchant/login", response_class=HTMLResponse)
    async def merchant_login_page(request: Request) -> HTMLResponse:
        db = make_session()
        try:
            claimed = store_claimed(db)
        finally:
            db.close()
        body = (
            "<h1>Merchant sign in</h1>"
            + (
                '<p class="lede">No passkey is registered for this store yet. '
                "Claim it: pick an operator id, register the first passkey — "
                "it becomes operator #1 (trust-on-first-use, DECISION-044).</p>"
                if not claimed
                else '<p class="lede">This store is claimed. Sign in with your '
                "passkey — the assertion mints a 12h session cookie. Sessions "
                "prove which operator is browsing; money actions still need a "
                "fresh passkey tap each time.</p>"
            )
            + '<div class="card"><label for="op">Operator id</label>'
            '<input id="op" value="merchant-1" autocomplete="username">'
            + (
                '<div class="actions"><button class="primary" id="claim">Claim this store</button></div>'
                if not claimed
                else '<div class="actions"><button class="primary" id="go">Sign in with passkey</button></div>'
            )
            + '<div class="status" id="st"></div></div>'
            + '<script src="/static/js/webauthn.js"></script>'
            + '<script src="/static/js/api.js"></script>'
            + "<script>"
            '"use strict";'
            "const claimed = " + safe_json(claimed) + ";"
            "const {api} = makeApi(null);"
            "const st = (t, e) => { const el = document.getElementById('st');"
            " el.textContent = t; el.className = e ? 'status err' : 'status'; };"
            "async function login(op) {"
            " st('Requesting sign-in challenge…');"
            " const begin = await api('/merchant/login', {action:'begin', operator_id: op});"
            " const a = await navigator.credentials.get({publicKey: {"
            "  challenge: b64d(begin.challenge), rpId: begin.rpId,"
            "  allowCredentials: [], userVerification: 'required'}});"
            " const done = await api('/merchant/login', {action:'complete', operator_id: op,"
            "  credential_id: a.id,"
            "  client_data_json: b64url(new Uint8Array(a.response.clientDataJSON)),"
            "  authenticator_data: b64url(new Uint8Array(a.response.authenticatorData)),"
            "  signature: b64url(new Uint8Array(a.response.signature)),"
            "  challenge: begin.challenge});"
            " st('Signed in as ' + done.operator_id + ' — opening console…');"
            " location.href = '/merchant';}"
            "async function claim(op) {"
            " st('Registering first passkey…');"
            " const name = document.getElementById('op').value || op;"
            " const {api: rawApi} = makeApi(name);"
            " await webauthnRegister(rawApi, name, 'Merchant operator');"
            " st('Claimed — now signing in…');"
            " const begin = await api('/merchant/login', {action:'begin', operator_id: name});"
            " const a = await navigator.credentials.get({publicKey: {"
            "  challenge: b64d(begin.challenge), rpId: begin.rpId,"
            "  allowCredentials: [], userVerification: 'required'}});"
            " const done = await api('/merchant/login', {action:'complete', claim:true, operator_id: name,"
            "  credential_id: a.id,"
            "  client_data_json: b64url(new Uint8Array(a.response.clientDataJSON)),"
            "  authenticator_data: b64url(new Uint8Array(a.response.authenticatorData)),"
            "  signature: b64url(new Uint8Array(a.response.signature)),"
            "  challenge: begin.challenge});"
            " st('Signed in as ' + done.operator_id + ' — opening console…');"
            " location.href = '/merchant';}"
            "document.getElementById(claimed ? 'go' : 'claim').addEventListener('click',"
            " async (e) => { const b = e.target; b.disabled = true;"
            "  try { const op = document.getElementById('op').value.trim();"
            "   if (!op) throw new Error('operator id required');"
            "   await (claimed ? login(op) : claim(op));"
            "  } catch (err) { st('Failed: ' + (err.code || err.message), true); b.disabled = false; } });"
            "</script>"
        )
        return _page(request, "Sign in", body, "")

    @router.post("/merchant/login")
    async def merchant_login(request: Request, body: LoginRequest) -> JSONResponse:
        op = (body.operator_id or "").strip()
        if not op:
            raise _fail(422, "webhook.invalid_payload", "operator_id is required")
        binding = {"mode": "merchant-login"}
        eff = _effective(config)
        if body.action == "begin":
            options = begin_assertion(eff, op, binding=binding, store=login_store)
            options["binding"] = binding
            return JSONResponse(options)
        if body.action == "complete":
            missing = [
                k
                for k in (
                    "credential_id",
                    "client_data_json",
                    "authenticator_data",
                    "signature",
                    "challenge",
                )
                if not getattr(body, k)
            ]
            if missing:
                raise _fail(
                    422, "webhook.invalid_payload", f"assertion missing {', '.join(missing)}"
                )
            db = make_session()
            try:
                # TOFU guard FIRST (before burning the single-use challenge):
                # a login for an unclaimed store is a contradiction — no
                # credential exists to assert with. Refuse, point at claim.
                from openstore.core.session import store_claimed as _claimed
                from openstore.core.session import store_claimed_by_other as _claimed_by_other

                if not _claimed(db):
                    raise _fail(
                        401,
                        "auth.session_required",
                        "no passkey is registered for this store yet — claim it first",
                    )
                try:
                    complete_assertion(
                        db,
                        eff,
                        op,
                        body.credential_id or "",
                        body.client_data_json or "",
                        body.authenticator_data or "",
                        body.signature or "",
                        body.challenge or "",
                        binding=binding,
                        store=login_store,
                    )
                except WebAuthnError as e:
                    db.rollback()
                    raise _fail(401, e.reason_code, e.message) from e
                # Stale-claim guard: this page was opened pre-claim and
                # submitted after ANOTHER operator claimed in the meantime.
                # Must not silently become operator #2 — 409, sign in
                # instead. The ordinary self-claim path (register a passkey,
                # then complete this same login) is NOT this case: op's own
                # just-created credential already makes store_claimed()
                # true, so the check must be "does a credential exist for a
                # DIFFERENT operator", not "does any credential exist".
                if body.claim and _claimed_by_other(db, op):
                    raise _fail(
                        409,
                        "auth.store_already_claimed",
                        "store is already claimed — sign in instead",
                    )
                raw = mint_session(
                    db, op, body.credential_id or "", request.headers.get("user-agent")
                )
                db.commit()
            finally:
                db.close()
            resp = JSONResponse(
                {"ok": True, "operator_id": op, "csrf_token": csrf_token_for(raw)}
            )
            _set_cookie(resp, raw)
            return resp
        raise _fail(422, "webhook.invalid_payload", f"unknown action {body.action!r}")

    @router.post("/merchant/logout")
    async def merchant_logout(request: Request) -> JSONResponse:
        raw = request.cookies.get(SESSION_COOKIE)
        if raw is not None:
            _need_csrf(request, raw)
        db = make_session()
        try:
            revoke_session(db, raw)
            db.commit()
        finally:
            db.close()
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # ---------------------------------------------------------------- setup
    @router.get("/merchant/setup", response_class=HTMLResponse)
    async def merchant_setup(request: Request) -> HTMLResponse:
        db = make_session()
        try:
            claimed = store_claimed(db)
            fresh = (
                db.exec(select(IntentPolicy.id).limit(1)).first() is None
                and db.exec(select(Checkout.id).limit(1)).first() is None
            )
        finally:
            db.close()
        source = (
            f"Shopify ({config.shopify.store_domain})"
            if config.shopify is not None
            else f"YAML ({config.catalog_path})"
        )
        body = (
            "<h1>First-run setup</h1>"
            '<p class="lede">Someone ran <span class="mono">openstore serve</span> '
            "once. Everything after that is here.</p>"
            '<div class="card"><h2>1. Claim the store</h2><p>'
            + (
                "Done — a passkey is registered."
                if claimed
                else '<a class="btn primary" href="/merchant/login">Claim with a passkey</a>'
            )
            + "</p></div>"
            '<div class="card"><h2>2. Catalog source</h2>'
            f"<p>Current: <b>{_html.escape(source)}</b></p>"
            '<p class="muted">YAML merchants edit SKUs at '
            '<a href="/merchant/catalog">Catalog</a>. Platform adapters '
            "(WooCommerce, …) land in Stage 25 with a connect-and-test picker here.</p></div>"
            '<div class="card"><h2>3. Store settings</h2><p>'
            + (
                "Fresh store — set the merchant name below, then continue."
                if fresh
                else "Store is live (policies/orders exist) — the name is locked."
            )
            + ' <a class="btn" href="/merchant/settings">Settings</a></p></div>'
            '<div class="card"><h2>4. First policy</h2><p class="muted">'
            "Sign a standing policy at Policy Studio, then return.</p>"
            '<p><a class="btn" href="/intent/studio">Policy Studio</a> '
            '<a class="btn" href="/merchant/policies">Policies</a></p></div>'
            '<div class="card"><h2>5. Connect a platform</h2>'
            '<p class="muted">Paste a source block (WooCommerce example below), '
            "test the connection, then save it under Settings &gt; Catalog source. "
            "Secrets ride in ${VAR} placeholders resolved from .env — never paste "
            "a live secret into this box on a shared screen.</p>"
            "<label>Source JSON</label><input id='src' value='{\"type\": \"woocommerce\", "
            "\"base_url\": \"https://shop.example\", \"consumer_key\": \"${WOO_KEY}\", "
            "\"consumer_secret\": \"${WOO_SECRET}\"}'>"
            "<div class='actions'><button class='primary' id='test'>Test connection</button></div>"
            "<div class='status' id='tst'></div></div>"
            "<script src='/static/js/api.js'></script><script>"
            "\"use strict\";const {api}=makeApi(null);"
            "document.getElementById('test').addEventListener('click', async()=>{"
            " const el=document.getElementById('tst');"
            " try{const r=await api('/merchant/setup',{action:'test',"
            "  source:JSON.parse(document.getElementById('src').value)});"
            "  el.textContent=(r.ok?'Connected: ':'FAILED: ')+r.adapter+' '+(r.item_count??'')+' '+(r.detail||r.message||'');}"
            " catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';}});"
            "</script>"
        )
        return _page(request, "Setup", body, "/merchant/setup")

    @router.post("/merchant/setup")
    async def merchant_setup_test(request: Request, body: SetupAction) -> dict[str, Any]:
        """Connect-and-test a candidate source (DONE WHEN #4). Builds the
        adapter from the submitted block WITHOUT touching Settings — saving
        happens explicitly under /merchant/settings. Secrets are used once
        for the test connection and never persisted."""
        _op, raw = _need_session(request)
        _need_csrf(request, raw)
        if body.action != "test":
            raise _fail(422, "webhook.invalid_payload", f"unknown action {body.action!r}")
        from openstore.config import _interpolate_env
        from openstore.surfaces.adapters.errors import AdapterError as _AdapterError
        from openstore.surfaces.adapters.registry import build_adapter as _build

        try:
            # ${VAR} placeholders resolve from .env server-side (the same
            # rule as YAML config): secrets are used once for the test
            # connection and never persisted.
            adapter = _build(_interpolate_env(dict(body.source)))
            health = adapter.health_check()
            count = health.item_count
            if health.ok and count is None:
                count = len(adapter.fetch_items())
            return {
                "ok": health.ok,
                "adapter": adapter.name,
                "capabilities": sorted(c.value for c in adapter.capabilities),
                "item_count": count,
                "detail": health.detail,
            }
        except _AdapterError as e:
            return {"ok": False, "reason_code": e.reason_code, "message": e.message}
        except ValueError as e:
            # Undefined ${VAR} placeholder (fail loud, but as a test RESULT —
            # the merchant fixes .env and retries, no traceback).
            return {
                "ok": False,
                "reason_code": "catalog.adapter_not_configured",
                "message": str(e),
            }

    # --------------------------------------------------------------- catalog
    def _catalog_source_label() -> tuple[str, bool, str]:
        """(label, editable, deep_link). Remote sources are read-only here."""
        if config.shopify is not None:
            dom = config.shopify.store_domain
            return f"managed by Shopify ({dom})", False, f"https://{dom}/admin/products"
        return f"YAML ({config.catalog_path})", True, ""

    def _read_yaml_items() -> list[dict[str, Any]]:
        path = Path(config.catalog_path or "")
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = data if isinstance(data, list) else data.get("items", [])
        return [dict(i) for i in items]

    def _write_yaml_items(items: list[dict[str, Any]]) -> None:
        path = Path(config.catalog_path or "")
        path.write_text(
            yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        # Invalidate both caches (mirrors the redteam conftest pattern).
        import openstore.surfaces.catalog as _catalog_mod

        _catalog_mod.CATALOG_CACHE = None
        _catalog_mod._CATALOG_BY_PATH.pop(str(path.resolve()), None)

    @router.get("/merchant/catalog", response_class=HTMLResponse)
    async def merchant_catalog(request: Request, fresh: int = 0) -> HTMLResponse:
        _need_session(request)
        if fresh:
            import openstore.surfaces.catalog as _catalog_mod

            _catalog_mod.CATALOG_CACHE = None
        from openstore.surfaces.catalog import load_catalog

        label, editable, deep_link = _catalog_source_label()
        items = load_catalog(config)
        rows = "".join(
            f"<tr><td class='mono'>{_html.escape(i['sku'])}</td>"
            f"<td>{_html.escape(i.get('name', ''))}</td>"
            f"<td>₹{i.get('unit_minor', 0) / 100:.2f}</td>"
            f"<td class='muted'>{_html.escape(','.join(i.get('tags', [])))}</td></tr>"
            for i in items
        )
        body = (
            "<h1>Catalog</h1>"
            f"<p class='lede'>{_html.escape(label)} — {len(items)} SKU(s).</p>"
            + (
                f"<p><a class='btn' href='{deep_link}'>Open in Shopify admin</a> "
                "<a class='btn' href='/merchant/catalog?fresh=1'>Resync</a></p>"
                if not editable
                else "<div class='card'><h2>Add SKU</h2>"
                "<label>SKU</label><input id='sku'>"
                "<label>Name</label><input id='nm'>"
                "<label>Price (paise, integer &gt; 0)</label><input id='px' inputmode='numeric'>"
                "<label>Tags (comma-separated)</label><input id='tg'>"
                "<div class='actions'><button class='primary' id='add'>Add SKU</button></div>"
                "<div class='status' id='st'></div></div>"
                "<script src='/static/js/api.js'></script>"
                "<script>\"use strict\";const {api}=makeApi(null);"
                "document.getElementById('add').addEventListener('click', async()=>{"
                " const el=document.getElementById('st');"
                " try{await api('/merchant/catalog',{action:'add',"
                " sku:document.getElementById('sku').value.trim(),"
                " name:document.getElementById('nm').value.trim(),"
                " unit_minor:parseInt(document.getElementById('px').value,10),"
                " tags:document.getElementById('tg').value.split(',').map(s=>s.trim()).filter(Boolean)});"
                " location.reload();}catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';}});"
                "</script>"
            )
            + "<table><tr><th>SKU</th><th>Name</th><th>Price</th><th>Tags</th></tr>"
            + (rows or "<tr><td colspan=4 class='muted'>No SKUs yet.</td></tr>")
            + "</table>"
            + ("<script src='/static/js/api.js'></script>" if not editable else "")
        )
        return _page(request, "Catalog", body, "/merchant/catalog")

    @router.post("/merchant/catalog")
    async def merchant_catalog_mutate(request: Request, body: CatalogMutation) -> dict[str, Any]:
        _row, raw = _need_session(request)
        _need_csrf(request, raw)
        label, editable, _link = _catalog_source_label()
        if not editable:
            raise _fail(
                403,
                "auth.insufficient_scope",
                f"catalog is {label}; edit it there, not here",
            )
        sku = (body.sku or "").strip()
        if not sku:
            raise _fail(422, "webhook.invalid_payload", "sku is required")
        items = _read_yaml_items()
        by_sku = {str(i.get("sku", "")): i for i in items}
        if body.action == "add":
            if sku in by_sku:
                raise _fail(422, "webhook.invalid_payload", f"sku {sku!r} already exists")
            if body.unit_minor is None or not isinstance(body.unit_minor, int) or body.unit_minor <= 0:
                raise _fail(
                    422, "webhook.invalid_payload", "unit_minor must be a positive integer (paise)"
                )
            items.append(
                {
                    "sku": sku,
                    "name": body.name or sku,
                    "unit_minor": body.unit_minor,
                    "tags": body.tags or [],
                    "related_skus": body.related_skus or [],
                    "description": body.description or "",
                }
            )
            _write_yaml_items(items)
            return {"ok": True, "sku": sku}
        if body.action == "delete":
            if sku not in by_sku:
                raise _fail(404, "catalog.sku_not_found", f"sku {sku!r} not found")
            _write_yaml_items([i for i in items if str(i.get("sku", "")) != sku])
            return {"ok": True, "sku": sku}
        if body.action == "update":
            if sku not in by_sku:
                raise _fail(404, "catalog.sku_not_found", f"sku {sku!r} not found")
            row_item = by_sku[sku]
            if body.name is not None:
                row_item["name"] = body.name
            if body.unit_minor is not None:
                if not isinstance(body.unit_minor, int) or body.unit_minor <= 0:
                    raise _fail(
                        422, "webhook.invalid_payload", "unit_minor must be a positive integer (paise)"
                    )
                row_item["unit_minor"] = body.unit_minor
            if body.tags is not None:
                row_item["tags"] = body.tags
            if body.related_skus is not None:
                row_item["related_skus"] = body.related_skus
            if body.description is not None:
                row_item["description"] = body.description
            _write_yaml_items(items)
            return {"ok": True, "sku": sku}
        raise _fail(422, "webhook.invalid_payload", f"unknown action {body.action!r}")

    # ---------------------------------------------------------------- orders
    @router.get("/merchant/orders", response_class=HTMLResponse)
    async def merchant_orders(
        request: Request, limit: int = Query(default=20, ge=1, le=200)
    ) -> HTMLResponse:
        _need_session(request)
        from openstore.surfaces.studio import _order_rows

        db = make_session()
        try:
            rows = _order_rows(db, limit)
            ids = [r["checkout_id"] for r in rows if r.get("checkout_id")]
            share_live = {
                c.id: bool(c.evidence_token_hash)
                for c in db.exec(select(Checkout).where(Checkout.id.in_(ids))).all()  # type: ignore[attr-defined]
            }
        finally:
            db.close()
        cards = "".join(
            "<div class='card'>"
            f"<h2 class='mono'>{_html.escape(r['checkout_id'])}</h2>"
            f"<p>{_html.escape(r['state'])} · ₹{r['amount_minor'] / 100:.2f} · "
            f"<span class='muted'>{_html.escape(r['created_at'] or '')}</span></p>"
            + (
                f"<p><a href=\"/orders/{r['checkout_id']}/evidence/view\">Evidence</a>"
                + (
                    " · <span class='badge'>share link live</span>"
                    if share_live.get(r["checkout_id"])
                    else ""
                )
                + "</p>"
                f"<div class='actions'><button data-share='{r['checkout_id']}'>Share receipt</button>"
                f"<button data-revoke='{r['checkout_id']}'>Revoke link</button>"
                f"<button data-cancel='{r['checkout_id']}'>Cancel</button></div>"
                f"<div class='status' id='st-{_html.escape(r['checkout_id'])}'></div>"
                f"<div class='status' id='link-{_html.escape(r['checkout_id'])}'></div>"
                if r.get("checkout_id")
                else ""
            )
            + "</div>"
            for r in rows
        )
        body = (
            "<h1>Orders</h1><p class='lede'>List, evidence, share links, cancel.</p>"
            + (cards or "<p class='empty'>No orders yet.</p>")
            + "<script src='/static/js/api.js'></script><script>"
            '"use strict";const {api}=makeApi(null);'
            "async function act(a,id,btn){const el=document.getElementById('st-'+id);btn.disabled=true;"
            " try{const r=await api('/merchant/orders',{action:a,checkout_id:id});"
            "  el.textContent=JSON.stringify(r);"
            "  if(r.share_url){document.getElementById('link-'+id).textContent=r.share_url;}}"
            " catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';btn.disabled=false;}}"
            "document.querySelectorAll('button[data-share]').forEach(b=>b.addEventListener('click',()=>act('share',b.dataset.share,b)));"
            "document.querySelectorAll('button[data-revoke]').forEach(b=>b.addEventListener('click',()=>act('revoke-share',b.dataset.revoke,b)));"
            "document.querySelectorAll('button[data-cancel]').forEach(b=>b.addEventListener('click',()=>act('cancel',b.dataset.cancel,b)));"
            "</script>"
        )
        return _page(request, "Orders", body, "/merchant/orders")

    @router.post("/merchant/orders")
    async def merchant_order_action(request: Request, body: OrderAction) -> dict[str, Any]:
        operator_id, raw = _need_session(request)
        _need_csrf(request, raw)
        db = make_session()
        try:
            checkout = db.exec(select(Checkout).where(Checkout.id == body.checkout_id)).first()
            if checkout is None:
                raise _fail(404, "checkout.not_found", f"checkout {body.checkout_id} not found")
            if body.action == "share":
                from openstore.core.session import hash_token as _ht

                eff = _effective(config)
                token = secrets.token_urlsafe(32)
                checkout.evidence_token_hash = _ht(token)
                checkout.evidence_token_expires_at = _now() + timedelta(
                    days=eff.evidence_share_ttl_days
                )
                db.add(checkout)
                db.commit()
                return {
                    "ok": True,
                    "share_url": f"/orders/{checkout.id}/evidence/view?t={token}",
                }
            if body.action == "revoke-share":
                checkout.evidence_token_hash = None
                checkout.evidence_token_expires_at = None
                db.add(checkout)
                db.commit()
                return {"ok": True, "revoked": True}
            if body.action == "cancel":
                if not checkout.cancel_token:
                    raise _fail(
                        422,
                        "webhook.invalid_payload",
                        f"checkout {checkout.id} has no payment link to cancel",
                    )
                import openstore.psp.razorpay_driver as driver

                try:
                    result = driver.cancel_checkout_by_id(
                        config=config,
                        session=db,
                        trace_id=f"merchant-cancel-{secrets.token_hex(4)}",
                        client_id=f"merchant:{operator_id}",
                        checkout=checkout,
                    )
                except driver.RazorpayError as e:
                    db.rollback()
                    if e.error_code == "psp.invalid_state":
                        raise _fail(400, "psp.invalid_state", e.message) from e
                    raise
                db.commit()
                return {"ok": True, "result": result}
            raise _fail(422, "webhook.invalid_payload", f"unknown action {body.action!r}")
        finally:
            db.close()

    # ------------------------------------------------------------- campaigns
    @router.get("/merchant/campaigns", response_class=HTMLResponse)
    async def merchant_campaigns(request: Request) -> HTMLResponse:
        _need_session(request)
        from openstore.surfaces.studio import _campaign_rows

        db = make_session()
        try:
            rows = _campaign_rows(db)
        finally:
            db.close()
        body = (
            "<h1>Campaigns</h1><p class='lede'>Approve with passkey, or draft "
            "from the browser — the approval gate is unchanged (DECISION-045).</p>"
            "<div class='card'><h2>New draft</h2>"
            "<label>Occasion (optional)</label><input id='cal' placeholder='Diwali'>"
            "<div class='actions'><button class='primary' id='draft'>Draft campaign</button>"
            "<button id='growth'>Run growth check</button></div>"
            "<div class='status' id='dst'></div></div>"
            "<div id='list'></div>"
            "<script src='/static/js/api.js'></script>"
            "<script src='/static/js/webauthn.js'></script>"
            "<script src='/static/js/fmt.js'></script>"
            "<script>\"use strict\";const ROWS=" + safe_json(rows) + ";"
            "const {api}=makeApi(null);" + _campaigns_js() + "</script>"
        )
        return _page(request, "Campaigns", body, "/merchant/campaigns")

    @router.post("/merchant/campaigns")
    async def merchant_campaign_action(request: Request, body: CampaignAction) -> dict[str, Any]:
        _row, raw = _need_session(request)
        _need_csrf(request, raw)
        if body.action == "draft":
            import secrets as _secrets
            from datetime import timedelta as _td

            from openstore.agents.campaign_agent import CampaignAgent
            from openstore.core.campaigns import create_campaign, submit_for_approval
            from openstore.core.database import session_scope as _scope

            days = body.days if 1 <= body.days <= 90 else 7
            trace_id = f"merchant-draft-{_secrets.token_hex(4)}"
            eff = _effective(config)
            with _scope(config) as db:
                draft = CampaignAgent(eff).draft_campaign(
                    db, merchant_id(eff), body.calendar_event or None, trace_id
                )
                starts_at = _now()
                campaign = create_campaign(
                    db,
                    eff,
                    merchant_id=merchant_id(eff),
                    title=draft["title"],
                    rationale=draft["rationale"],
                    discount_bps=draft["discount_bps"],
                    applies_to_skus=draft["applies_to_skus"],
                    starts_at=starts_at,
                    ends_at=starts_at + _td(days=days),
                    source_signals=draft["source_signals"],
                    trace_id=trace_id,
                )
                submit_for_approval(db, campaign.id, trace_id)
                cid = campaign.id
            return {"ok": True, "campaign_id": cid, "state": "PENDING_APPROVAL"}
        if body.action == "growth-check":
            from openstore.agents.campaign_agent import auto_draft_campaign_if_stalled
            from openstore.core.database import session_scope as _scope2

            eff = _effective(config)
            with _scope2(config) as db:
                auto = auto_draft_campaign_if_stalled(db, eff, merchant_id(eff))
                result = None if auto is None else auto.id
            return {"ok": True, "campaign_id": result}
        raise _fail(422, "webhook.invalid_payload", f"unknown action {body.action!r}")

    # -------------------------------------------------------------- policies
    @router.get("/merchant/policies", response_class=HTMLResponse)
    async def merchant_policies(request: Request) -> HTMLResponse:
        _need_session(request)
        from openstore.core.database import compute_policy_exposure

        db = make_session()
        try:
            policies = db.exec(
                select(IntentPolicy).order_by(IntentPolicy.created_at.desc())  # type: ignore[attr-defined]
            ).all()
            rows = [
                (
                    p.id,
                    p.merchant_id,
                    p.max_spend_per_tx_minor,
                    p.max_spend_total_minor,
                    compute_policy_exposure(db, p.id),
                    bool(p.is_active),
                )
                for p in policies
            ]
        finally:
            db.close()
        body = (
            "<h1>Policies</h1><p class='lede'>Standing policies and live exposure. "
            "Sign a new one at <a href='/intent/studio'>Policy Studio</a>; "
            "blast-radius stays at "
            "<a href='/internal/policy/blast-radius'>/internal/policy/blast-radius</a>.</p>"
            "<table><tr><th>ID</th><th>Per-tx</th><th>Total</th><th>Exposure</th><th>Active</th></tr>"
            + "".join(
                f"<tr><td class='mono'>{_html.escape(pid)}</td>"
                f"<td>₹{ptx / 100:.2f}</td><td>₹{pt / 100:.2f}</td>"
                f"<td>₹{exp / 100:.2f}</td><td>{'yes' if act else 'no'}</td></tr>"
                for pid, _m, ptx, pt, exp, act in rows
            )
            + "</table>"
            + ("" if rows else "<p class='empty'>No policies yet.</p>")
        )
        return _page(request, "Policies", body, "/merchant/policies")

    # -------------------------------------------------------------- settings
    @router.get("/merchant/settings", response_class=HTMLResponse)
    async def merchant_settings_page(request: Request) -> HTMLResponse:
        _need_session(request)
        eff = _effective(config)
        db = make_session()
        try:
            overlay = _overlay_all(db)
            live = (
                db.exec(select(IntentPolicy.id).limit(1)).first() is not None
                or db.exec(select(Checkout.id).limit(1)).first() is not None
            )
        finally:
            db.close()

        def _val(key: str, current: str) -> str:
            return _html.escape(overlay.get(key, current))

        body = (
            "<h1>Settings</h1><p class='lede'>Non-secret config only. Secrets stay "
            "in <span class='mono'>.env</span> — shown here as present/absent, never values.</p>"
            "<div class='card'><h2>Store</h2>"
            f"<label>Merchant name{' (locked — store is live)' if live else ''}</label>"
            f"<input id='s-name' value='{_val('merchant.name', eff.merchant.name)}'"
            + (" disabled" if live else "")
            + ">"
            f"<p class='muted'>Currency: {_html.escape(eff.merchant.currency)} (fixed at init)</p></div>"
            "<div class='card'><h2>Campaign bounds</h2>"
            f"<label>min_bps</label><input id='s-min' inputmode='numeric' value='{_val('campaign.min_bps', str(eff.campaign.min_bps))}'>"
            f"<label>max_bps</label><input id='s-max' inputmode='numeric' value='{_val('campaign.max_bps', str(eff.campaign.max_bps))}'>"
            f"<label>max_active</label><input id='s-act' inputmode='numeric' value='{_val('campaign.max_active', str(eff.campaign.max_active))}'>"
            f"<label>evidence_share_ttl_days (1–540, ≤ retention {eff.evidence_retention_days})</label>"
            f"<input id='s-ttl' inputmode='numeric' value='{_val('evidence_share_ttl_days', str(eff.evidence_share_ttl_days))}'></div>"
            "<div class='card'><h2>WebAuthn origin (SID-5 guarded)</h2>"
            f"<label>rp_id</label><input id='s-rpid' value='{_val('webauthn.rp_id', eff.webauthn.rp_id)}'>"
            f"<label>origin</label><input id='s-origin' value='{_val('webauthn.origin', eff.webauthn.origin)}'>"
            f"<label>public_base_url (empty = same-origin)</label>"
            f"<input id='s-pub' value='{_val('public_base_url', eff.public_base_url or '')}'>"
            "<p class='muted'>A rp_id/public_base_url mismatch is refused here — "
            "the server would refuse to boot with it (DEF-14).</p></div>"
            "<div class='card'><h2>Catalog source</h2>"
            "<p class='muted'>Tested first at Setup &gt; Connect a platform. "
            "Saving switches the live catalog on next load.</p>"
            f"<label>Source JSON (empty = YAML default)</label>"
            f"<input id='s-src' value='{_html.escape(_current_source_json(eff))}'>"
            "</div>"
            "<div class='card'><h2>Secrets (.env only)</h2><table>"
            f"<tr><td>Razorpay keys</td><td>{'present' if eff.razorpay.key_id else 'MISSING'}</td></tr>"
            f"<tr><td>Discord token</td><td>{'present' if eff.discord.bot_token and eff.discord.bot_token != 'token' else 'MISSING'}</td></tr>"
            "</table></div>"
            "<div class='actions'><button class='primary' id='save'>Save settings</button></div>"
            "<div class='status' id='st'></div>"
            "<script src='/static/js/api.js'></script><script>"
            '"use strict";const {api}=makeApi(null);'
            + ("const LIVE=true;" if live else "const LIVE=false;")
            + "document.getElementById('save').addEventListener('click', async()=>{"
            " const el=document.getElementById('st');const b={};"
            " if(!LIVE){const n=document.getElementById('s-name').value.trim();if(n)b.merchant_name=n;}"
            " const num=(id)=>{const v=document.getElementById(id).value.trim();return v===''?null:parseInt(v,10);};"
            " const mi=num('s-min'),ma=num('s-max'),ac=num('s-act'),ttl=num('s-ttl');"
            " if(mi!==null)b.campaign_min_bps=mi;if(ma!==null)b.campaign_max_bps=ma;"
            " if(ac!==null)b.campaign_max_active=ac;if(ttl!==null)b.evidence_share_ttl_days=ttl;"
            " b.webauthn_rp_id=document.getElementById('s-rpid').value.trim();"
            " b.webauthn_origin=document.getElementById('s-origin').value.trim();"
            " b.public_base_url=document.getElementById('s-pub').value.trim()||null;"
            " const src=document.getElementById('s-src').value.trim();"
            " if(src){try{b.catalog_source=JSON.parse(src);}catch(e){"
            "  el.textContent='Failed: source is not valid JSON';el.className='status err';return;}}"
            " try{await api('/merchant/settings',b);el.textContent='Saved.';}"
            " catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';}});"
            "</script>"
        )
        return _page(request, "Settings", body, "/merchant/settings")

    @router.post("/merchant/settings")
    async def merchant_settings_save(request: Request, body: SettingsForm) -> dict[str, Any]:
        _row, raw = _need_session(request)
        _need_csrf(request, raw)
        eff = _effective(config)
        db = make_session()
        try:
            live = (
                db.exec(select(IntentPolicy.id).limit(1)).first() is not None
                or db.exec(select(Checkout.id).limit(1)).first() is not None
            )
            proposed: dict[str, str] = {}
            if body.merchant_name is not None:
                name = body.merchant_name.strip()
                if not name:
                    raise _fail(422, "webhook.invalid_payload", "merchant name must not be blank")
                if live:
                    raise _fail(
                        422,
                        "webhook.invalid_payload",
                        "renaming a live store orphans its policies and checkouts "
                        "(merchant_id is slugged from the name) — refused",
                    )
                proposed["merchant.name"] = name
            if body.campaign_min_bps is not None:
                proposed["campaign.min_bps"] = str(body.campaign_min_bps)
            if body.campaign_max_bps is not None:
                proposed["campaign.max_bps"] = str(body.campaign_max_bps)
            if body.campaign_max_active is not None:
                if body.campaign_max_active < 1:
                    raise _fail(422, "webhook.invalid_payload", "campaign_max_active must be >= 1")
                proposed["campaign.max_active"] = str(body.campaign_max_active)
            if body.evidence_share_ttl_days is not None:
                ttl = body.evidence_share_ttl_days
                if not 1 <= ttl <= 540 or ttl > eff.evidence_retention_days:
                    raise _fail(
                        422,
                        "webhook.invalid_payload",
                        f"evidence_share_ttl_days must be within 1-540 and never exceed "
                        f"evidence_retention_days ({eff.evidence_retention_days})",
                    )
                proposed["evidence_share_ttl_days"] = str(ttl)
            # Campaign bounds sanity (discount_out_of_bounds is the honest code).
            try:
                lo = int(proposed.get("campaign.min_bps", eff.campaign.min_bps))
                hi = int(proposed.get("campaign.max_bps", eff.campaign.max_bps))
            except ValueError:
                raise _fail(422, "webhook.invalid_payload", "campaign bounds must be integers") from None
            if not 0 <= lo <= hi <= 10000:
                raise _fail(
                    422,
                    "campaign.discount_out_of_bounds",
                    f"campaign bounds must satisfy 0 <= min <= max <= 10000, got {lo}/{hi}",
                )
            # SID-5 pre-validation on the EFFECTIVE triple after this save.
            overlay = _overlay_all(db)
            overlay.update(proposed)
            rp_id = overlay.get("webauthn.rp_id", eff.webauthn.rp_id)
            pub = overlay.get("public_base_url", eff.public_base_url or "")
            if body.webauthn_rp_id is not None:
                rp_id = body.webauthn_rp_id.strip()
                if not rp_id:
                    raise _fail(422, "webhook.invalid_payload", "webauthn rp_id must not be blank")
                proposed["webauthn.rp_id"] = rp_id
            if body.webauthn_origin is not None:
                origin = body.webauthn_origin.strip()
                if not origin:
                    raise _fail(422, "webhook.invalid_payload", "webauthn origin must not be blank")
                proposed["webauthn.origin"] = origin
            if "public_base_url" in body.model_fields_set:
                # Distinguish "field omitted" from "field explicitly cleared
                # to null" (model_fields_set, not `is not None`) — the
                # frontend sends null to mean "reset to same-origin".
                pub = (body.public_base_url or "").strip()
                proposed["public_base_url"] = pub
            _check_sid5(rp_id, pub or None)
            if body.catalog_source is not None:
                import json as _json

                from openstore.config import _interpolate_env as _interp
                from openstore.surfaces.adapters.errors import AdapterError as _AE
                from openstore.surfaces.adapters.registry import coerce_source as _coerce

                try:
                    src = _coerce(_interp(dict(body.catalog_source)))
                except _AE as e:
                    raise _fail(422, e.reason_code, e.message) from e
                except ValueError as e:
                    raise _fail(422, "catalog.adapter_not_configured", str(e)) from e
                proposed["catalog_source"] = _json.dumps(
                    src.model_dump(), separators=(",", ":")
                )
            for key, value in proposed.items():
                assert key in SETTABLE_KEYS, key  # closed set by construction
                existing = db.get(MerchantSetting, key)
                if existing is None:
                    db.add(MerchantSetting(key=key, value=value, updated_at=_now()))
                else:
                    existing.value = value
                    existing.updated_at = _now()
                    db.add(existing)
            db.commit()
            return {"ok": True, "saved": sorted(proposed)}
        finally:
            db.close()

    # -------------------------------------------------------- merchandising
    @router.get("/merchant/merchandising", response_class=HTMLResponse)
    async def merchant_merchandising(request: Request) -> HTMLResponse:
        _need_session(request)
        body = (
            "<h1>Merchandising</h1>"
            '<p class="lede">Cross-sell and up-sell rules land here in Stage 27. '
            "Until then, suggestions come from the catalog's "
            "<span class='mono'>related_skus</span> field.</p>"
            "<p><a class='btn' href='/merchant/catalog'>Catalog</a></p>"
        )
        return _page(request, "Merchandising", body, "/merchant/merchandising")

    return router


def _campaigns_js() -> str:
    """Inline campaign-list renderer for /merchant/campaigns (kept inline:
    it reuses the approve/reject/pause assertion flow verbatim)."""
    return """
function esc(s){return String(s??"").replace(/[&<>"']/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function render(){const el=document.getElementById('list');
 if(!ROWS.length){el.innerHTML='<p class="empty">No campaigns yet — draft one above.</p>';return;}
 el.innerHTML=ROWS.map(c=>'<div class="card"><h2>'+esc(c.title)+'</h2><p class="muted">'+esc(c.state)+' · '+(c.discount_bps/100)+'% · '+esc((c.applies_to_skus||[]).join(", "))+'</p><div class="actions">'
 +(c.state==="PENDING_APPROVAL"?'<button class="primary" data-approve="'+esc(c.campaign_id)+'">Approve with passkey</button><button data-reject="'+esc(c.campaign_id)+'">Reject</button>':'')
 +(c.state==="ACTIVE"?'<button data-pause="'+esc(c.campaign_id)+'">Pause</button>':'')
 +'</div><div class="status" id="st-'+esc(c.campaign_id)+'"></div></div>').join("");}
async function approve(id){const st=document.getElementById('st-'+id);st.textContent='Requesting challenge…';
 const begin=await api('/internal/webauthn/assertion/begin',{campaign_id:id});
 const a=await navigator.credentials.get({publicKey:{challenge:b64d(begin.challenge),rpId:begin.rpId,allowCredentials:[],userVerification:'required'}});
 const r=await api('/campaign/'+encodeURIComponent(id)+'/approve',{approver_credential_id:a.id,client_data_json:b64url(new Uint8Array(a.response.clientDataJSON)),authenticator_data:b64url(new Uint8Array(a.response.authenticatorData)),signature:b64url(new Uint8Array(a.response.signature)),challenge:begin.challenge});
 st.textContent='Approved — '+r.state+'.';}
document.getElementById('draft').addEventListener('click',async()=>{const el=document.getElementById('dst');
 try{const r=await api('/merchant/campaigns',{action:'draft',calendar_event:document.getElementById('cal').value});el.textContent='Drafted '+r.campaign_id+' — reload to review.';}
 catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';}});
document.getElementById('growth').addEventListener('click',async()=>{const el=document.getElementById('dst');
 try{const r=await api('/merchant/campaigns',{action:'growth-check'});el.textContent=r.campaign_id?('Auto-drafted '+r.campaign_id):'Nothing stalled.';}
 catch(e){el.textContent='Failed: '+(e.code||e.message);el.className='status err';}});
document.getElementById('list').addEventListener('click',async(e)=>{const t=e.target;
 const ap=t.closest('button[data-approve]'),rj=t.closest('button[data-reject]'),pa=t.closest('button[data-pause]');
 try{if(ap){ap.disabled=true;await approve(ap.dataset.approve);}
 else if(rj){await api('/campaign/'+encodeURIComponent(rj.dataset.reject)+'/reject',{reason:''});location.reload();}
 else if(pa){await api('/campaign/'+encodeURIComponent(pa.dataset.pause)+'/pause');location.reload();}}
 catch(err){const id=(ap||rj||pa).dataset.approve||(ap||rj||pa).dataset.reject||(ap||rj||pa).dataset.pause;
  const st=document.getElementById('st-'+id);st.textContent='Failed: '+(err.code||err.message);st.className='status err';}});
render();
"""
